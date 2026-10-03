#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
signal_bot.py - ربات سیگنال‌دهنده برای والکس (هیچ سفارش واقعی ثبت نمی‌کند)

اجزای علمی (نسخه سبک و قابل اجرا روی سرور ۱ گیگ):
  - رژیم بازار (Regime): روند ۳۰ روزه بالا / پایین / خنثی
  - بازگشت به میانگین (Z-Score): فاصله قیمت از خط تعادل ۱۰ روزه
  - مدل نوسان (EWMA، خویشاوند ساده GARCH): نوسان روزانه امروز در مقایسه با حالت عادی
  - مدیریت ریسک (ایده کلی): هر چه نوسان بیشتر، حجم پیشنهادی کمتر
  - فیلتر هزینه: اگر فاصله تا خط تعادل از هزینه معامله کمتر باشد، سیگنال خرید نمی‌دهد

اجرا:
  python3 signal_bot.py                       # سیگنال فعلی تتر و طلا
  python3 signal_bot.py --backtest            # تست همین قوانین روی ۳۶۵ روز گذشته، برای هر دو بازار
  python3 signal_bot.py --symbols PAXGUSDT    # فقط یک بازار خاص
  python3 signal_bot.py --loop                # هر ساعت چک کن و در فایل ثبت کن
"""
import argparse
import os
import time

import numpy as np
import pandas as pd
import requests

URL = "https://api.wallex.ir/v1/udf/history"
DEPTH_URL = "https://api.wallex.ir/v1/depth"

# بازارها و هزینه‌هایشان: fee = کارمزد هر طرف، spread = فاصله قیمت خرید و فروش
# (spread در حالت زنده هر بار از والکس اندازه گرفته می‌شود؛ این عدد فقط برای بک‌تست است)
MARKETS = {
    "USDTTMN":  {"fee": 0.0035, "spread": 0.0001},
    "PAXGTMN":  {"fee": 0.0035, "spread": 0.015},
    "PAXGUSDT": {"fee": 0.0020, "spread": 0.0195},
    "XAUTTMN":  {"fee": 0.0035, "spread": 0.015},
    "XAUTUSDT": {"fee": 0.0020, "spread": 0.005},
}
DEFAULT_SYMBOLS = ["USDTTMN", "PAXGTMN"]
HEADERS = {"User-Agent": "quant-research/1.0"}

# ---- پارامترهای قوانین (کندل ۴ ساعته = ۶ کندل در روز) ----
BAR = "240min"
TREND_BARS = 180         # حدود ۳۰ روز: روند بلندمدت
MEAN_BARS = 60           # حدود ۱۰ روز: خط تعادل کوتاه‌مدت
VOL_LAMBDA = 0.94        # حافظه مدل نوسان
VOL_REGIME_BARS = 540    # حدود ۹۰ روز: نوسان «عادی» چقدر است
Z_BUY = -1.5             # این‌قدر زیر خط تعادل = افت قابل توجه
Z_STRETCHED = 2.0        # این‌قدر بالای خط تعادل = کش‌آمده
TARGET_DAILY_VOL = 0.01  # هدف: نوسان روزانه سرمایه حدود ۱٪


def fetch(symbol, days):
    """کندل‌های ساعتی را می‌گیرد و به کندل ۴ ساعته تبدیل می‌کند."""
    end = int(time.time())
    start = end - days * 86400
    frames, t_to = [], end
    while t_to > start:
        t_from = max(start, t_to - 3600 * 500)
        d = None
        for attempt in range(4):
            try:
                r = requests.get(URL, headers=HEADERS, timeout=20, params={
                    "symbol": symbol, "resolution": "60", "from": t_from, "to": t_to})
                d = r.json()
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        if isinstance(d, dict) and "s" not in d and isinstance(d.get("result"), dict):
            d = d["result"]
        if isinstance(d, dict) and d.get("s") == "ok" and d.get("t"):
            frames.append(pd.DataFrame({"time": d["t"], "open": d["o"], "high": d["h"],
                                        "low": d["l"], "close": d["c"]}))
        t_to = t_from
        time.sleep(0.5)
    if not frames:
        raise SystemExit(f"No data received for {symbol}")
    df = (pd.concat(frames).apply(pd.to_numeric, errors="coerce").dropna()
          .drop_duplicates("time").sort_values("time"))
    df.index = pd.to_datetime(df.time.values, unit="s", utc=True).tz_convert("Asia/Tehran")
    bars = df.resample(BAR).agg({"open": "first", "high": "max",
                                 "low": "min", "close": "last"}).dropna()
    # کندل آخر اگر هنوز بسته نشده، حذف شود
    now = pd.Timestamp.now(tz="Asia/Tehran")
    if len(bars) and bars.index[-1] + pd.Timedelta(BAR) > now:
        bars = bars.iloc[:-1]
    return bars


def live_spread(symbol):
    """فاصله فعلی قیمت خرید و فروش را از دفتر سفارش والکس می‌گیرد."""
    try:
        d = requests.get(DEPTH_URL, params={"symbol": symbol}, headers=HEADERS, timeout=15).json()
        r = d.get("result", d)
        px = lambda side: [float(x["price"] if isinstance(x, dict) else x[0]) for x in side]
        asks = px(r.get("ask") or r.get("asks") or [])
        bids = px(r.get("bid") or r.get("bids") or [])
        buy, sell = min(asks), max(bids)
        return (buy - sell) / ((buy + sell) / 2)
    except Exception:
        return None


def indicators(bars):
    """همه شاخص‌ها فقط از داده‌های گذشته ساخته می‌شوند (بدون نگاه به آینده)."""
    out = bars.copy()
    lp = np.log(out.close)
    ret = lp.diff()

    ma_long = lp.rolling(TREND_BARS).mean()
    out["trend_up"] = (lp > ma_long) & (ma_long > ma_long.shift(30))
    out["trend_down"] = (lp < ma_long) & (ma_long < ma_long.shift(30))

    mean = lp.rolling(MEAN_BARS).mean()
    std = lp.rolling(MEAN_BARS).std()
    out["z"] = (lp - mean) / std
    out["room"] = np.exp(mean - lp) - 1          # چند درصد تا خط تعادل

    var = ret.pow(2).ewm(alpha=1 - VOL_LAMBDA, adjust=False).mean()
    out["vol_day"] = np.sqrt(var * 6)
    out["vol_normal"] = out.vol_day.rolling(VOL_REGIME_BARS, min_periods=120).median()
    out["vol_high"] = out.vol_day > 1.5 * out.vol_normal
    return out


def decide(row, cost):
    if row.trend_down:
        return "STAY OUT", "Long-term trend is DOWN. Buying now fights the main flow."
    if row.trend_up and row.z <= Z_BUY:
        if row.room < cost:
            return "WAIT", "There is a dip, but the room back to balance is smaller than costs."
        return "BUY", "Price dipped well below its 10-day balance while the main trend is UP."
    if row.z >= Z_STRETCHED:
        return "TAKE PROFIT", "Price is stretched far above its 10-day balance."
    return "HOLD / WAIT", "No clear edge right now."


def position_size(row):
    f = min(1.0, TARGET_DAILY_VOL / row.vol_day) if row.vol_day > 0 else 0.0
    if row.vol_high:
        f *= 0.5
    return f


def trend_label(row):
    return "UP" if row.trend_up else ("DOWN" if row.trend_down else "SIDEWAYS")


def live(symbol, fee, spread, log=True):
    ind = indicators(fetch(symbol, 150)).dropna(subset=["z", "vol_normal"])
    if ind.empty:
        raise SystemExit("Not enough data to compute signals.")
    row = ind.iloc[-1]
    measured = live_spread(symbol)
    if measured is not None:
        spread = measured
    cost = 2 * fee + spread
    sig, why = decide(row, cost)
    vol_state = "HIGH (careful)" if row.vol_high else "NORMAL"
    print("=" * 62)
    print(f" {symbol} | last closed 4h bar: {ind.index[-1]:%Y-%m-%d %H:%M} (Tehran)")
    print("=" * 62)
    print(f" Price                        : {row.close:,.2f}")
    print(f" Main trend (30 days)         : {trend_label(row)}")
    print(f" Distance from balance (z)    : {row.z:+.2f}")
    print(f" Room back to balance         : {row.room * 100:+.2f}%")
    print(f" Daily volatility             : {row.vol_day * 100:.2f}% "
          f"(normal {row.vol_normal * 100:.2f}%) -> {vol_state}")
    print(f" Buy/sell gap (live)          : {spread * 100:.3f}%"
          + ("" if measured is not None else " (assumed, live check failed)"))
    print(f" Round-trip cost (fee + gap)  : {cost * 100:.2f}%")
    print("-" * 62)
    print(f" SIGNAL : {sig}")
    print(f" WHY    : {why}")
    if sig == "BUY":
        print(f" Suggested size: {position_size(row) * 100:.0f}% of the capital for this market")
    print("-" * 62)
    print(" Test signal only. Not financial advice. No order was placed.")
    if log:
        path = "signals_log.csv"
        pd.DataFrame([{
            "checked_at": pd.Timestamp.now(tz="Asia/Tehran").strftime("%Y-%m-%d %H:%M"),
            "bar": ind.index[-1].strftime("%Y-%m-%d %H:%M"), "symbol": symbol,
            "price": row.close, "signal": sig, "z": round(row.z, 3),
            "trend": trend_label(row), "vol_day": round(row.vol_day, 5),
            "cost": round(cost, 5),
        }]).to_csv(path, mode="a", header=not os.path.exists(path), index=False)
    return sig


def max_drawdown(series):
    return float((series / series.cummax() - 1).min())


def backtest(symbol, fee, spread, days):
    ind = indicators(fetch(symbol, days)).dropna(subset=["z", "vol_normal"])
    if len(ind) < 50:
        raise SystemExit("Not enough data for a backtest.")
    side_cost = fee + spread / 2
    cash, units, entry, stop = 1.0, 0.0, 0.0, 0.0
    trades, equity, in_market = [], [], 0

    for i in range(len(ind) - 1):
        row = ind.iloc[i]
        next_open = ind.open.iloc[i + 1]           # اجرا روی کندل بعد
        sig, _ = decide(row, 2 * side_cost)
        if units == 0 and sig == "BUY":
            spend = cash * position_size(row)
            units = spend * (1 - side_cost) / next_open
            cash -= spend
            entry = next_open
            stop = entry * (1 - max(0.03, 3 * row.vol_day))
        elif units > 0 and (row.z >= 0 or sig in ("STAY OUT", "TAKE PROFIT")
                            or row.close <= stop):
            cash += units * next_open * (1 - side_cost)
            trades.append(next_open / entry * (1 - side_cost) ** 2 - 1)
            units = 0.0
        in_market += units > 0
        equity.append(cash + units * row.close)

    final = cash + units * ind.close.iloc[-1] * (1 - side_cost)
    equity = pd.Series(equity + [final])
    hold_curve = ind.close.iloc[1:] / ind.open.iloc[1]
    hold = ind.close.iloc[-1] / ind.open.iloc[1] * (1 - side_cost) ** 2 - 1
    wins = sum(t > 0 for t in trades)

    print("=" * 62)
    print(f" BACKTEST {symbol} | {ind.index[0]:%Y-%m-%d} to {ind.index[-1]:%Y-%m-%d}")
    print(f" Costs per round trip: {2 * side_cost * 100:.2f}%  | trades on next bar's open")
    print("=" * 62)
    print(f" Bot result           : {(final - 1) * 100:+.2f}%")
    print(f" Just holding         : {hold * 100:+.2f}%")
    print(f" Bot worst drop       : {max_drawdown(equity) * 100:.2f}%")
    print(f" Holding worst drop   : {max_drawdown(hold_curve) * 100:.2f}%")
    print(f" Number of trades     : {len(trades)}  (wins {wins}, losses {len(trades) - wins})")
    if trades:
        print(f" Average trade        : {np.mean(trades) * 100:+.2f}%")
    print(f" Time in market       : {in_market / max(1, len(ind) - 1) * 100:.1f}%")
    print("=" * 62)
    return final - 1, hold, len(trades)


def costs_for(symbol, fee, spread):
    m = MARKETS.get(symbol, {"fee": 0.0035, "spread": 0.0})
    return (m["fee"] if fee is None else fee), (m["spread"] if spread is None else spread)


def run_all(symbols, fee, spread, mode, days):
    summary = []
    for sym in symbols:
        f, sp = costs_for(sym, fee, spread)
        try:
            if mode == "backtest":
                bot, hold, n = backtest(sym, f, sp, days)
                summary.append(f" {sym:10s} bot {bot * 100:+8.2f}%  | hold {hold * 100:+8.2f}%  | trades {n}")
            else:
                sig = live(sym, f, sp)
                summary.append(f" {sym:10s} -> {sig}")
        except SystemExit as e:
            summary.append(f" {sym:10s} -> skipped ({e})")
        except Exception as e:
            summary.append(f" {sym:10s} -> error ({e})")
        print()
    print("#" * 62)
    print(" SUMMARY")
    print("#" * 62)
    for line in summary:
        print(line)
    print("#" * 62)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS,
                   help="markets, e.g. USDTTMN PAXGTMN PAXGUSDT")
    p.add_argument("--fee", type=float, default=None, help="override fee per side, 0.0035 = 0.35%%")
    p.add_argument("--spread", type=float, default=None, help="override buy/sell gap, 0.015 = 1.5%%")
    p.add_argument("--backtest", action="store_true")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--loop", action="store_true", help="check every hour")
    a = p.parse_args()

    if a.backtest:
        run_all(a.symbols, a.fee, a.spread, "backtest", a.days)
    elif a.loop:
        while True:
            run_all(a.symbols, a.fee, a.spread, "live", a.days)
            time.sleep(3600)
    else:
        run_all(a.symbols, a.fee, a.spread, "live", a.days)


if __name__ == "__main__":
    main()
