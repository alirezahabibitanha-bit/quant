#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_slow.py - تست سه روش کند و معروف روی ۲۰ سال قیمت جهانی طلا

روش‌ها (از قبل مشخص، بدون دست‌کاری عددها):
  1. MA200 ماهانه: آخر هر ماه، اگر قیمت بالای میانگین ۲۰۰ روزه بود در طلا بمان، وگرنه تتر
  2. مومنتوم ۱۲ ماهه: آخر هر ماه، اگر قیمت از ۱۲ ماه پیش بالاتر بود در طلا بمان، وگرنه تتر
  3. تقاطع ۵۰/۲۰۰: هر روز، اگر میانگین ۵۰ روزه بالای ۲۰۰ روزه بود در طلا بمان، وگرنه تتر
  و مقایسه با «فقط نگه داشتن».

قواعد (از CLAUDE.md):
  - هزینه هر خرید یا فروش 0.45% (کارمزد + فاصله قیمت در XAUTUSDT)
  - سیگنال با قیمت بسته شدن روز ساخته می‌شود و معامله با قیمت باز شدن روز بعد انجام می‌شود
  - هیچ محاسبه‌ای از داده آینده استفاده نمی‌کند
  - وقتی بیرون از طلا هستیم، پول تتر است و سودی نمی‌گیرد
  - در پایان دوره همه طلا فروخته می‌شود (هزینه فروش برای همه، از جمله «نگه داشتن»)

معیار «بهتر» (پیش از دیدن نتیجه‌ها تعیین شده):
  روشی بهتر است که نسبت «رشد سالانه / بدترین افت» آن در هر سه دوره
  (2006-2012، 2013-2019، 2020 تا حالا) و روی هر دو منبع داده (GLD و GC=F)
  از «فقط نگه داشتن» بیشتر باشد.

اجرا:
  python3 backtest_slow.py              # با داده ذخیره‌شده در پوشه data
  python3 backtest_slow.py --download   # اول داده تازه از یاهو بگیر
"""
import argparse
import os

import numpy as np
import pandas as pd
import requests

COST = 0.0045
SOURCES = {"GLD": "GLD", "GC_F": "GC=F"}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PERIODS = [("2006-2012", "2006-01-01", "2012-12-31"),
           ("2013-2019", "2013-01-01", "2019-12-31"),
           ("2020-now", "2020-01-01", None),
           ("FULL", "2006-01-01", None)]
METHODS = ["HOLD", "MA200_MONTHLY", "MOM12_MONTHLY", "CROSS_50_200"]
REPORT_PATH = "slow_methods_report.md"


def download(name, ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    r = requests.get(url, params={"period1": 1104537600, "period2": 4102444800, "interval": "1d"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    d = r.json()["chart"]["result"][0]
    q = d["indicators"]["quote"][0]
    df = pd.DataFrame({"date": pd.to_datetime(d["timestamp"], unit="s").date,
                       "open": q["open"], "close": q["close"]}).dropna()
    df.to_csv(os.path.join(DATA_DIR, f"{name}_daily.csv"), index=False, float_format="%.4f")
    print(f" Downloaded {name}: {len(df)} days")


def load(name):
    df = pd.read_csv(os.path.join(DATA_DIR, f"{name}_daily.csv"), parse_dates=["date"])
    return df.set_index("date").sort_index()


def signals(df):
    """۱ = در طلا، ۰ = در تتر. هر عدد فقط از قیمت‌های همان روز و قبل از آن ساخته می‌شود."""
    c = df.close
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    # آخرین روز معاملاتی هر ماه (روز آخر داده را آخر ماه حساب نمی‌کنیم چون ماه تمام نشده)
    month = df.index.to_period("M")
    month_end = pd.Series(month != month.to_series().shift(-1).values, index=df.index)
    month_end.iloc[-1] = False

    def monthly(rule):
        s = pd.Series(np.nan, index=df.index)
        s[month_end] = rule[month_end].astype(float)
        return s.ffill()

    out = pd.DataFrame(index=df.index)
    out["HOLD"] = 1.0
    out["MA200_MONTHLY"] = monthly((c > sma200) & sma200.notna())
    out["MOM12_MONTHLY"] = monthly((c > c.shift(252)) & c.shift(252).notna())
    out["CROSS_50_200"] = ((sma50 > sma200) & sma200.notna()).astype(float)
    return out


def run(df, sig, start, end):
    """شبیه‌سازی روز به روز. تصمیم دیروز با قیمت باز شدن امروز اجرا می‌شود."""
    days = df.loc[start:end].index
    first = df.index.get_loc(days[0])
    equity, pos, trades, in_days = 1.0, 0.0, 0, 0
    curve = []
    for i in range(first, first + len(days)):
        o, c, prev_c = df.open.iloc[i], df.close.iloc[i], df.close.iloc[i - 1]
        equity *= 1 + pos * (o / prev_c - 1)               # فاصله شب تا صبح
        target = sig.iloc[i - 1]                           # سیگنال دیروز
        target = 0.0 if np.isnan(target) else target
        if target != pos:
            equity *= 1 - COST
            trades += 1
            pos = target
        equity *= 1 + pos * (c / o - 1)                    # حرکت طول روز
        in_days += pos > 0
        curve.append(equity)
    if pos > 0:                                            # فروش پایانی
        equity *= 1 - COST
        trades += 1
        curve[-1] = equity
    curve = pd.Series(curve, index=days)
    years = (days[-1] - days[0]).days / 365.25
    cagr = equity ** (1 / years) - 1
    max_dd = float((curve / curve.cummax() - 1).min())
    return {"return": equity - 1, "cagr": cagr, "max_dd": max_dd,
            "ratio": cagr / abs(max_dd) if max_dd < 0 else np.inf,
            "trades_per_year": trades / years, "in_market": in_days / len(days)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--download", action="store_true", help="refresh data from Yahoo first")
    a = p.parse_args()
    if a.download:
        for name, ticker in SOURCES.items():
            download(name, ticker)

    rows = []
    for name in SOURCES:
        df = load(name)
        sig = signals(df)
        for period, start, end in PERIODS:
            for m in METHODS:
                r = run(df, sig[m], start, end)
                rows.append({"source": name, "period": period, "method": m, **r})
    res = pd.DataFrame(rows)

    pd.set_option("display.width", 120)
    for name in SOURCES:
        print("=" * 96)
        print(f" SOURCE {name} | cost {COST * 100:.2f}% per side | trade at next day's open")
        print("=" * 96)
        print(f" {'period':10s} {'method':15s} {'return':>9s} {'per year':>9s} {'worst drop':>11s} "
              f"{'ratio':>7s} {'trades/yr':>10s} {'in gold':>8s}")
        for _, r in res[res.source == name].iterrows():
            print(f" {r.period:10s} {r.method:15s} {r['return'] * 100:+8.1f}% {r.cagr * 100:+8.2f}% "
                  f"{r.max_dd * 100:10.1f}% {r.ratio:7.2f} {r.trades_per_year:10.1f} "
                  f"{r.in_market * 100:7.0f}%")

    # معیار از پیش تعیین‌شده: در هر سه دوره و روی هر دو منبع، نسبت بیشتر از نگه داشتن
    sub = res[res.period != "FULL"].set_index(["source", "period", "method"]).ratio
    verdict = {}
    for m in METHODS[1:]:
        wins = [(s, per, sub[(s, per, m)] > sub[(s, per, "HOLD")])
                for s in SOURCES for per, _, _ in PERIODS[:3]]
        verdict[m] = (all(w for _, _, w in wins), sum(w for _, _, w in wins), len(wins))
    print("=" * 96)
    print(" VERDICT (better than HOLD on return/worst-drop in every period and both sources?)")
    for m, (ok, n, total) in verdict.items():
        print(f"   {m:15s}: {'YES' if ok else 'NO '}  (won {n} of {total})")
    print("=" * 96)
    res.to_csv("slow_methods_results.csv", index=False)
    write_report(res, verdict)
    print(f" Persian report: {REPORT_PATH} | numbers: slow_methods_results.csv")


def write_report(res, verdict):
    fa = {"HOLD": "فقط نگه داشتن", "MA200_MONTHLY": "میانگین ۲۰۰ روزه ماهانه",
          "MOM12_MONTHLY": "مومنتوم ۱۲ ماهه", "CROSS_50_200": "تقاطع ۵۰/۲۰۰"}
    L = ["# نتیجه تست روش‌های کند روی قیمت جهانی طلا\n",
         f"- هزینه هر خرید یا فروش: {COST * 100:.2f}٪ | معامله با قیمت باز شدن روز بعد از سیگنال",
         "- «نسبت» = رشد سالانه تقسیم بر بدترین افت (هر چه بیشتر، بهتر)",
         "- معیار «بهتر»: نسبت بیشتر از «فقط نگه داشتن» در هر سه دوره و روی هر دو منبع داده\n"]
    for name in SOURCES:
        L.append(f"## منبع داده: {name}\n")
        L.append("| دوره | روش | کل سود | سود سالانه | بدترین افت | نسبت | جابه‌جایی در سال | زمان در طلا |")
        L.append("|---|---|---|---|---|---|---|---|")
        for _, r in res[res.source == name].iterrows():
            L.append(f"| {r.period} | {fa[r.method]} | {r['return'] * 100:+.1f}٪ | {r.cagr * 100:+.2f}٪ | "
                     f"{r.max_dd * 100:.1f}٪ | {r.ratio:.2f} | {r.trades_per_year:.1f} | {r.in_market * 100:.0f}٪ |")
        L.append("")
    L.append("## حکم\n")
    for m, (ok, n, total) in verdict.items():
        L.append(f"- {fa[m]}: {'بهتر است' if ok else 'بهتر نیست'} (در {n} از {total} آزمون برنده شد)")
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
