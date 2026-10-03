#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
guardian_bot.py - ربات نگهبان برای تتر و طلا (هیچ سفارش واقعی ثبت نمی‌کند)

منطق: حالت پیش‌فرض «در بازار ماندن» است (چون تومان مداوم ارزش از دست می‌دهد).
ربات فقط وقتی خطر جدی می‌بیند، پیشنهاد خروج یا کم کردن می‌دهد.

  خطر ۱ - برگشت جریان اصلی: قیمت زیر میانگین ۳۰ روزه و میانگین هم رو به پایین
  خطر ۲ - ریزش تند: قیمت خیلی بیشتر از حد عادی از سقف ۱۰ روز اخیر پایین آمده
  احتیاط - نوسان خیلی بالا: نصف کردن حجم
  برگشت به بازار: وقتی قیمت دوباره بالای میانگین ۳۰ روزه و بالای خط تعادل ۱۰ روزه آمد

نیاز دارد فایل signal_bot.py در همین پوشه باشد.

اجرا:
  python3 guardian_bot.py              # وضعیت فعلی تتر و طلا
  python3 guardian_bot.py --backtest   # تست روی گذشته
  python3 guardian_bot.py --loop       # هر ساعت چک کن و در فایل ثبت کن
"""
import argparse
import os
import time

import numpy as np
import pandas as pd

from signal_bot import fetch, indicators, live_spread, max_drawdown, MARKETS

SYMBOLS = ["USDTTMN", "PAXGTMN"]
HIGH_BARS = 60            # حدود ۱۰ روز: سقف اخیر
MIN_CRASH = 0.06          # حداقل افت برای «ریزش تند»
CRASH_VOL_MULT = 3.0      # یا ۳ برابر نوسان روزانه، هر کدام بزرگ‌تر


def add_guardian(ind):
    g = ind.copy()
    lp = np.log(g.close)
    g["ma_long"] = lp.rolling(180).mean()
    g["balance"] = lp.rolling(60).mean()
    g["recent_high"] = g.close.rolling(HIGH_BARS).max()
    g["crash_limit"] = np.maximum(MIN_CRASH, CRASH_VOL_MULT * g.vol_day)
    g["drop_from_high"] = g.close / g.recent_high - 1
    g["crash"] = g.drop_from_high < -g.crash_limit
    g["recovered"] = (lp > g.ma_long) & (lp > g.balance)
    return g


def target_level(row, current):
    """سهم پیشنهادی از سرمایه: 1 = کامل، 0.5 = نصف، 0 = بیرون."""
    full = 0.5 if row.vol_high else 1.0
    if current > 0:
        if row.trend_down:
            return 0.0, "Main trend turned DOWN."
        if row.crash:
            return 0.0, "Sharp drop from the recent high."
        return full, ("Volatility is very high, keep only half." if row.vol_high
                      else "No danger seen. Stay in.")
    if row.recovered and not row.crash:
        return full, "Price recovered above its long and short averages. Safe to return."
    return 0.0, "Still waiting for recovery before returning."


def label(level):
    return {1.0: "STAY IN (100%)", 0.5: "REDUCE (keep 50%)", 0.0: "EXIT (0%)"}[level]


def backtest(symbol, days):
    m = MARKETS.get(symbol, {"fee": 0.0035, "spread": 0.0})
    side_cost = m["fee"] + m["spread"] / 2
    g = add_guardian(indicators(fetch(symbol, days))).dropna(
        subset=["ma_long", "vol_normal", "recent_high"])
    if len(g) < 50:
        raise SystemExit("Not enough data.")

    first_open = g.open.iloc[1]
    units = (1 - side_cost) / first_open      # شروع: کامل در بازار، مثل نگه داشتن
    cash, level, changes, out_bars = 0.0, 1.0, 0, 0
    curve = []

    for i in range(len(g) - 1):
        row = g.iloc[i]
        price_next = g.open.iloc[i + 1]
        new_level, _ = target_level(row, level)
        if new_level != level:
            value = cash + units * price_next
            target_units_value = value * new_level
            trade = target_units_value - units * price_next
            fee_paid = abs(trade) * side_cost
            units = target_units_value / price_next
            cash = value - target_units_value - fee_paid
            level = new_level
            changes += 1
        out_bars += level == 0
        curve.append(cash + units * g.close.iloc[i])

    final = cash + units * g.close.iloc[-1] * (1 - side_cost)
    curve = pd.Series(curve + [final])
    hold_curve = g.close.iloc[1:] / first_open
    hold = g.close.iloc[-1] / first_open * (1 - side_cost) ** 2 - 1

    print("=" * 62)
    print(f" GUARDIAN BACKTEST {symbol} | {g.index[1]:%Y-%m-%d} to {g.index[-1]:%Y-%m-%d}")
    print(f" Costs per round trip: {2 * side_cost * 100:.2f}%  | trades on next bar's open")
    print("=" * 62)
    print(f" Guardian result      : {(final - 1) * 100:+.2f}%")
    print(f" Just holding         : {hold * 100:+.2f}%")
    print(f" Guardian worst drop  : {max_drawdown(curve) * 100:.2f}%")
    print(f" Holding worst drop   : {max_drawdown(hold_curve) * 100:.2f}%")
    print(f" Position changes     : {changes}")
    print(f" Time fully out       : {out_bars / max(1, len(g) - 1) * 100:.1f}%")
    print("=" * 62)
    return final - 1, hold, max_drawdown(curve), max_drawdown(hold_curve)


def live(symbol, assume_in=True):
    g = add_guardian(indicators(fetch(symbol, 150))).dropna(
        subset=["ma_long", "vol_normal", "recent_high"])
    if g.empty:
        raise SystemExit("Not enough data.")
    row = g.iloc[-1]
    level, why = target_level(row, 1.0 if assume_in else 0.0)
    gap = live_spread(symbol)
    trend = "UP" if row.trend_up else ("DOWN" if row.trend_down else "SIDEWAYS")
    print("=" * 62)
    print(f" {symbol} | last closed 4h bar: {g.index[-1]:%Y-%m-%d %H:%M} (Tehran)")
    print("=" * 62)
    print(f" Price                 : {row.close:,.2f}")
    print(f" Main trend (30 days)  : {trend}")
    print(f" Drop from 10-day high : {row.drop_from_high * 100:+.2f}% "
          f"(danger at {-row.crash_limit * 100:.1f}%)")
    print(f" Daily volatility      : {row.vol_day * 100:.2f}% (normal {row.vol_normal * 100:.2f}%)")
    if gap is not None:
        print(f" Buy/sell gap (live)   : {gap * 100:.3f}%")
    print("-" * 62)
    print(f" ADVICE : {label(level)}")
    print(f" WHY    : {why}")
    if not assume_in:
        print(" (assuming you are currently OUT of this market)")
    print("-" * 62)
    print(" Test signal only. Not financial advice. No order was placed.")
    path = "guardian_log.csv"
    pd.DataFrame([{
        "checked_at": pd.Timestamp.now(tz="Asia/Tehran").strftime("%Y-%m-%d %H:%M"),
        "bar": g.index[-1].strftime("%Y-%m-%d %H:%M"), "symbol": symbol,
        "price": row.close, "advice": label(level), "trend": trend,
        "drop_from_high": round(row.drop_from_high, 4), "vol_day": round(row.vol_day, 5),
    }]).to_csv(path, mode="a", header=not os.path.exists(path), index=False)
    return label(level)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=SYMBOLS)
    p.add_argument("--backtest", action="store_true")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--out", action="store_true", help="assume I am currently out of the market")
    p.add_argument("--loop", action="store_true", help="check every hour")
    a = p.parse_args()

    def run():
        summary = []
        for s in a.symbols:
            try:
                if a.backtest:
                    r, h, dd, hdd = backtest(s, a.days)
                    summary.append(f" {s:9s} guardian {r * 100:+7.1f}% (worst {dd * 100:.0f}%)"
                                   f" | hold {h * 100:+7.1f}% (worst {hdd * 100:.0f}%)")
                else:
                    summary.append(f" {s:9s} -> {live(s, not a.out)}")
            except SystemExit as e:
                summary.append(f" {s:9s} -> skipped ({e})")
            except Exception as e:
                summary.append(f" {s:9s} -> error ({e})")
            print()
        print("#" * 62)
        print(" SUMMARY")
        print("#" * 62)
        for line in summary:
            print(line)
        print("#" * 62)

    if a.loop and not a.backtest:
        while True:
            run()
            time.sleep(3600)
    else:
        run()


if __name__ == "__main__":
    main()
