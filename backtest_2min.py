#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_2min.py - آیا اندیکاتورهای معروف تریدرها جهت ۲ دقیقه بعد طلا را درست حدس می‌زنند؟

داده: قیمت ۲ دقیقه‌ای طلای جهانی (GC=F از یاهو، حدود ۵ هفته) در data/GC_F_2m.csv
اندیکاتورها با تنظیمات استاندارد کتاب‌ها (بدون دست‌کاری):
  RSI(14): زیر ۳۰ = بالا می‌رود، بالای ۷۰ = پایین می‌آید
  MACD(12,26,9): خط MACD بالای خط سیگنال = بالا
  بولینگر(20, 2): زیر باند پایین = بالا، بالای باند بالا = پایین
  تقاطع میانگین ۹ و ۲۱: میانگین کوتاه بالای بلند = بالا
  مومنتوم: کندل قبلی صعودی بود = بالا
قانون‌ها:
  - سیگنال با بسته شدن کندل t ساخته می‌شود؛ معامله با باز شدن کندل t+1 و بستن با باز شدن کندل t+2 (۲ دقیقه)
  - فقط وقتی کندل‌ها پشت سر هم‌اند (بدون فاصله تعطیلی)
  - معیار «کار می‌کند» (از قبل تعیین شده): درصد درستی جهت به‌طور معنادار بالای ۵۰٪ (فاصله اطمینان ۹۵٪)،
    در هر دو نیمه داده، و سود متوسط هر معامله بیشتر از هزینه معامله.
همچنین بازه پیش‌بینی ۲ دقیقه بعد (مدل نوسان EWMA، خویشاوند ساده GARCH) سنجیده می‌شود.

اجرا:  python3 backtest_2min.py
"""
import os

import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "GC_F_2m.csv")
COSTS = {"MetaTrader gold (about)": 0.0001, "Wallex XAUTUSDT (about)": 0.004}   # هزینه رفت و برگشت


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def signals(df):
    """+1 = بالا، -1 = پایین، 0 = سیگنالی نیست. فقط از داده تا همان کندل."""
    c = df.close
    out = pd.DataFrame(index=df.index)
    r = rsi(c)
    out["RSI(14)"] = np.where(r < 30, 1, np.where(r > 70, -1, 0))
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    out["MACD(12,26,9)"] = np.where(macd > macd.ewm(span=9, adjust=False).mean(), 1, -1)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    out["Bollinger(20,2)"] = np.where(c < mid - 2 * sd, 1, np.where(c > mid + 2 * sd, -1, 0))
    out["SMA 9/21 cross"] = np.where(c.rolling(9).mean() > c.rolling(21).mean(), 1, -1)
    out["Momentum(1)"] = np.sign(c.diff()).fillna(0).astype(int)
    out.iloc[:30] = 0                                      # تا اندیکاتورها کامل شوند
    return out


def main():
    df = pd.read_csv(DATA, parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    step = pd.Timedelta(minutes=2)
    ok = (df.time.shift(-1) - df.time == step) & (df.time.shift(-2) - df.time.shift(-1) == step)
    nxt = df.open.shift(-2) / df.open.shift(-1) - 1        # بازده ۲ دقیقه بعد از سیگنال
    sig = signals(df)
    half = df.time.iloc[len(df) // 2]
    print("=" * 100)
    print(f" Gold 2-minute bars: {df.time.iloc[0]:%Y-%m-%d} to {df.time.iloc[-1]:%Y-%m-%d}, {len(df)} bars")
    print(f" Typical 2-minute move (median absolute): {nxt[ok].abs().median() * 100:.3f}%")
    print("=" * 100)
    print(f" {'method':18s} {'signals':>8s} {'right':>7s} {'95% range':>14s} {'1st half':>9s} {'2nd half':>9s} "
          f"{'avg gain/trade':>15s}  verdict")
    rows = []
    for m in sig.columns:
        s = pd.Series(sig[m].values, index=df.index)
        use = ok & (s != 0) & nxt.notna() & (nxt != 0)
        hit = (np.sign(nxt[use]) == s[use])
        n, p = len(hit), hit.mean()
        ci = 1.96 * np.sqrt(0.25 / n)
        gain = float((s[use] * nxt[use]).mean())
        h1 = hit[df.time[use] < half].mean()
        h2 = hit[df.time[use] >= half].mean()
        works = (p - ci > 0.5) and h1 > 0.5 and h2 > 0.5 and gain > min(COSTS.values())
        rows.append((m, n, p, gain, works))
        print(f" {m:18s} {n:8d} {p * 100:6.1f}% {(p - ci) * 100:6.1f}-{(p + ci) * 100:5.1f}% "
              f"{h1 * 100:8.1f}% {h2 * 100:8.1f}% {gain * 100:+14.4f}%  {'WORKS' if works else 'no edge'}")
    print("-" * 100)
    for name, cost in COSTS.items():
        print(f" Round-trip cost {name}: {cost * 100:.2f}%  (avg gain must beat this)")

    # بازه پیش‌بینی ۲ دقیقه بعد با مدل نوسان EWMA
    lr = np.log(df.close).diff()
    var = lr.pow(2).ewm(alpha=0.06, adjust=False).mean()   # لامبدا ۰.۹۴، استاندارد RiskMetrics
    sigma = np.sqrt(var)
    fut = np.log(df.close.shift(-1) / df.close)
    okr = (df.time.shift(-1) - df.time == step) & sigma.gt(0)
    z = (fut / sigma)[okr].dropna().iloc[100:]
    print("=" * 100)
    print(f" 2-minute RANGE forecast (EWMA volatility): real price inside +-1 sigma {np.mean(z.abs() <= 1) * 100:.0f}% "
          f"(target 68%), inside +-2 sigma {np.mean(z.abs() <= 2) * 100:.0f}% (target 95%), {len(z)} tests")
    print("=" * 100)


if __name__ == "__main__":
    main()
