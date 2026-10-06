#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dxy_research.py - آیا شاخص دلار آمریکا (DXY) به تحلیل طلا کمک می‌کند؟

سه سؤال، همه با قانون‌های از قبل تعیین‌شده (قبل از دیدن نتیجه‌ها):

1. رابطه هم‌زمان (واقعیت، نه پیش‌بینی): همبستگی بازده روزانه و هفتگی طلا و دلار در سه دوره
   (2006-2012، 2013-2019، 2020 تا حالا) و یک سال اخیر.

2. پیش‌بینی جهت (آیا حرکت دلار، حرکت بعدی طلا را می‌گوید؟):
   - روزانه: اگر دلار امروز پایین آمد، فردا طلا بالا می‌رود (و برعکس).
   - ۲ دقیقه: اگر دلار در ۲ دقیقه گذشته پایین آمد، طلا در ۲ دقیقه بعد بالا می‌رود (و برعکس).
   قانون قبول: درصد درستی حداقل ۵۲٪ در همه دوره‌ها (روزانه: سه دوره؛ ۲ دقیقه: دو نیمه داده)،
   و برای ۲ دقیقه سود متوسط هر معامله بیشتر از هزینه رفت و برگشت متاتریدر (۰٫۰۱٪).

3. زمان‌بندی کند با دلار (روش‌های معروف، عددهای استاندارد، بدون دست‌کاری):
   - DXY_MA200: آخر هر ماه، اگر دلار زیر میانگین ۲۰۰ روزه‌اش بود در طلا بمان، وگرنه تتر.
   - DXY_MOM12: آخر هر ماه، اگر دلار از ۱۲ ماه پیش پایین‌تر بود در طلا بمان، وگرنه تتر.
   همان قواعد backtest_slow.py: هزینه ۰٫۴۵٪ هر طرف، معامله با قیمت باز شدن روز بعد، هیچ داده آینده‌ای.
   قانون قبول (همان قبلی): نسبت «رشد سالانه / بدترین افت» در هر سه دوره و روی هر دو منبع (GLD و GC=F)
   بیشتر از «فقط نگه داشتن».

داده: یاهو. دلار DX-Y.NYB (روزانه از ۲۰۰۵، ۲ دقیقه‌ای ۶۰ روز اخیر)، طلا GC=F و GLD.

اجرا:
  python3 dxy_research.py              # با داده ذخیره‌شده (اگر نبود دانلود می‌کند)
  python3 dxy_research.py --download   # اول داده تازه از یاهو بگیر
گزارش فارسی: dxy_report.md
"""
import argparse
import os

import numpy as np
import pandas as pd
import requests

import backtest_slow as bs

DATA_DIR = bs.DATA_DIR
REPORT_PATH = "dxy_report.md"
HIT_NEEDED = 0.52
MT_ROUND_TRIP = 0.0001            # هزینه رفت و برگشت متاتریدر حدود ۰٫۰۱٪
PERIODS3 = bs.PERIODS[:3]
H = {"User-Agent": "Mozilla/5.0"}


def two_min(ticker):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                     params={"interval": "2m", "range": "60d"}, headers=H, timeout=30)
    d = r.json()["chart"]["result"][0]
    s = pd.Series(d["indicators"]["quote"][0]["close"], index=pd.to_datetime(d["timestamp"], unit="s"))
    return s.dropna()


def load_all(download):
    if download or not os.path.exists(os.path.join(DATA_DIR, "DXY_daily.csv")):
        bs.download("DXY", "DX-Y.NYB")
    path2 = os.path.join(DATA_DIR, "DXY_GC_2m.csv")
    if download or not os.path.exists(path2):
        both = pd.DataFrame({"gold": two_min("GC=F"), "dxy": two_min("DX-Y.NYB")}).dropna()
        both.index.name = "time"
        both.to_csv(path2, float_format="%.4f")
        print(f" Downloaded 2-minute gold and DXY: {len(both)} common bars")
    m2 = pd.read_csv(path2, parse_dates=["time"]).set_index("time")
    return bs.load("DXY"), {name: bs.load(name) for name in bs.SOURCES}, m2


# ---------------------------------------------------------------- ۱. رابطه هم‌زمان

def relationship(dxy, gold):
    c = pd.DataFrame({"g": gold.close, "d": dxy.close}).dropna()
    rows = []
    last_year = (str((c.index[-1] - pd.Timedelta(days=365)).date()), None)
    for name, start, end in PERIODS3 + [("last 1y", *last_year)]:
        s = c.loc[start:end]
        rd, rw = s.pct_change().dropna(), s.resample("W-FRI").last().pct_change().dropna()
        beta = np.polyfit(rd.d, rd.g, 1)[0]
        rows.append({"period": name, "daily_corr": rd.g.corr(rd.d), "weekly_corr": rw.g.corr(rw.d),
                     "beta": beta, "same_dir": float((np.sign(rd.g) != np.sign(rd.d)).mean())})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- ۲. پیش‌بینی جهت

def daily_lead(dxy, gold):
    c = pd.DataFrame({"g": gold.close, "d": dxy.close}).dropna()
    r = c.pct_change()
    df = pd.DataFrame({"d_today": r.d, "g_next": r.g.shift(-1)}).dropna()
    df = df[(df.d_today != 0) & (df.g_next != 0)]
    rows = []
    for name, start, end in PERIODS3:
        s = df.loc[start:end]
        hit = float((np.sign(-s.d_today) == np.sign(s.g_next)).mean())
        rows.append({"period": name, "n": len(s), "hit": hit, "corr": s.d_today.corr(s.g_next)})
    return pd.DataFrame(rows)


def two_min_lead(m2):
    """فقط بازه‌های پشت سر هم (دقیقاً ۲ دقیقه فاصله) حساب می‌شوند؛ بازار بسته و جای خالی کنار می‌روند."""
    gap = m2.index.to_series().diff().dt.total_seconds()
    r = m2.pct_change()
    ok = gap == 120
    df = pd.DataFrame({"d_now": r.dxy, "g_now": r.gold, "g_next": r.gold.shift(-1),
                       "ok": ok & ok.shift(-1, fill_value=False)})
    df = df[df.ok & (df.d_now != 0) & (df.g_next != 0)].dropna()
    half = len(df) // 2
    rows = []
    for name, s in (("first half", df.iloc[:half]), ("second half", df.iloc[half:]), ("all", df)):
        bet = np.sign(-s.d_now)
        rows.append({"part": name, "n": len(s), "from": s.index[0], "to": s.index[-1],
                     "same_time_corr": s.d_now.corr(s.g_now),
                     "hit": float((bet == np.sign(s.g_next)).mean()),
                     "avg_gain": float((bet * s.g_next).mean()),
                     "typical_move": float(s.g_next.abs().median())})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- ۳. زمان‌بندی کند

def dxy_signals(gold, dxy):
    """۱ = در طلا. دلار روی روزهای طلا آورده می‌شود و فقط از داده همان روز و قبل استفاده می‌شود."""
    d = dxy.close.reindex(gold.index.union(dxy.index)).ffill().reindex(gold.index)
    sma200 = d.rolling(200).mean()
    month = gold.index.to_period("M")
    month_end = pd.Series(month != month.to_series().shift(-1).values, index=gold.index)
    month_end.iloc[-1] = False

    def monthly(rule):
        s = pd.Series(np.nan, index=gold.index)
        s[month_end] = rule[month_end].astype(float)
        return s.ffill()

    return pd.DataFrame({"HOLD": 1.0,
                         "DXY_MA200": monthly((d < sma200) & sma200.notna()),
                         "DXY_MOM12": monthly((d < d.shift(252)) & d.shift(252).notna())}, index=gold.index)


def timing(dxy, golds):
    rows = []
    for name, gold in golds.items():
        sig = dxy_signals(gold, dxy)
        for period, start, end in bs.PERIODS:
            for m in sig.columns:
                rows.append({"source": name, "period": period, "method": m, **bs.run(gold, sig[m], start, end)})
    res = pd.DataFrame(rows)
    sub = res[res.period != "FULL"].set_index(["source", "period", "method"]).ratio
    verdict = {}
    for m in ("DXY_MA200", "DXY_MOM12"):
        wins = [sub[(s, p, m)] > sub[(s, p, "HOLD")] for s in golds for p, _, _ in PERIODS3]
        verdict[m] = (all(wins), sum(wins), len(wins))
    return res, verdict


# ---------------------------------------------------------------- گزارش

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--download", action="store_true", help="refresh data from Yahoo first")
    a = p.parse_args()
    dxy, golds, m2 = load_all(a.download)
    gc = golds["GC_F"]

    rel = relationship(dxy, gc)
    print("=" * 90)
    print(" 1. SAME-TIME RELATIONSHIP (gold GC=F vs DXY)")
    for _, r in rel.iterrows():
        print(f"   {r.period:10s} daily corr {r.daily_corr:+.2f} | weekly corr {r.weekly_corr:+.2f} | "
              f"beta {r.beta:+.2f} | opposite direction {r.same_dir * 100:.0f}% of days")

    lead = daily_lead(dxy, gc)
    print(" 2a. DAILY: does today's DXY move predict tomorrow's gold?")
    for _, r in lead.iterrows():
        print(f"   {r.period:10s} n {int(r["n"]):5d} | hit {r.hit * 100:.1f}% | corr {r["corr"]:+.3f}")
    daily_ok = bool((lead.hit >= HIT_NEEDED).all())

    tm = two_min_lead(m2)
    print(" 2b. 2-MINUTE: does the last 2-minute DXY move predict the next 2-minute gold move?")
    for _, r in tm.iterrows():
        print(f"   {r.part:11s} n {int(r["n"]):5d} | same-time corr {r.same_time_corr:+.2f} | hit {r.hit * 100:.1f}% | "
              f"avg gain {r.avg_gain * 100:+.4f}% | typical move {r.typical_move * 100:.4f}%")
    halves = tm[tm.part != "all"]
    two_ok = bool((halves.hit >= HIT_NEEDED).all() and (halves.avg_gain > MT_ROUND_TRIP).all())

    res, verdict = timing(dxy, golds)
    print(" 3. SLOW TIMING WITH DXY (cost 0.45% per side, trade next open)")
    for name in golds:
        for _, r in res[res.source == name].iterrows():
            print(f"   {name:4s} {r.period:10s} {r.method:10s} per year {r.cagr * 100:+6.2f}% | worst drop "
                  f"{r.max_dd * 100:6.1f}% | ratio {r.ratio:5.2f} | trades/yr {r.trades_per_year:4.1f} | "
                  f"in gold {r.in_market * 100:3.0f}%")
    print("=" * 90)
    print(f" VERDICT daily direction: {'YES' if daily_ok else 'NO'} | 2-minute direction: "
          f"{'YES' if two_ok else 'NO'} | " +
          " | ".join(f"{m}: {'YES' if ok else 'NO'} (won {n} of {t})" for m, (ok, n, t) in verdict.items()))
    print("=" * 90)
    res.to_csv("dxy_results.csv", index=False)
    write_report(rel, lead, daily_ok, tm, two_ok, res, verdict, m2)
    print(f" Persian report: {REPORT_PATH} | numbers: dxy_results.csv")


def write_report(rel, lead, daily_ok, tm, two_ok, res, verdict, m2):
    yn = lambda ok: "**بله**" if ok else "**نه**"
    L = ["# شاخص دلار آمریکا (DXY) و طلا", "",
         "قانون‌های قبول قبل از دیدن نتیجه‌ها نوشته شدند (بالای dxy_research.py). داده: یاهو، طلا GC=F و GLD، دلار DX-Y.NYB.", "",
         "## ۱. طلا و دلار چقدر با هم حرکت می‌کنند؟ (واقعیت، نه پیش‌بینی)", "",
         "| دوره | همبستگی روزانه | همبستگی هفتگی | ۱٪ رشد دلار ≈ تغییر طلا | روزهای خلاف جهت |", "|---|---|---|---|---|"]
    for _, r in rel.iterrows():
        L.append(f"| {r.period} | {r.daily_corr:+.2f} | {r.weekly_corr:+.2f} | {r.beta:+.2f}٪ | {r.same_dir * 100:.0f}٪ |")
    L += ["", "همبستگی منفی یعنی معمولاً وقتی دلار بالا می‌رود طلا پایین می‌آید (هم‌زمان). این توضیح می‌دهد امروز چه شد، "
          "ولی به‌تنهایی نمی‌گوید فردا چه می‌شود.", "",
          "## ۲. آیا حرکت دلار حرکت بعدی طلا را پیش‌بینی می‌کند؟", "",
          f"قانون: درستی حداقل {HIT_NEEDED * 100:.0f}٪ در همه دوره‌ها؛ برای ۲ دقیقه، سود متوسط بیشتر از هزینه ۰٫۰۱٪.", "",
          "**روزانه** (دلار امروز پایین ← طلا فردا بالا):", "", "| دوره | تعداد روز | درستی | همبستگی |", "|---|---|---|---|"]
    for _, r in lead.iterrows():
        L.append(f"| {r.period} | {int(r["n"])} | {r.hit * 100:.1f}٪ | {r["corr"]:+.3f} |")
    L += ["", f"قبول شد؟ {yn(daily_ok)}", "",
          f"**۲ دقیقه** (از {m2.index[0]:%Y-%m-%d} تا {m2.index[-1]:%Y-%m-%d}، فقط بازه‌های پشت سر هم):", "",
          "| بخش | تعداد | همبستگی هم‌زمان | درستی جهت بعدی | سود متوسط هر معامله | حرکت معمول |", "|---|---|---|---|---|---|"]
    for _, r in tm.iterrows():
        L.append(f"| {r.part} | {int(r["n"])} | {r.same_time_corr:+.2f} | {r.hit * 100:.1f}٪ | {r.avg_gain * 100:+.4f}٪ | "
                 f"{r.typical_move * 100:.4f}٪ |")
    L += ["", f"قبول شد؟ {yn(two_ok)}", "",
          "## ۳. زمان‌بندی کند با دلار (مثل backtest_slow.py)", "",
          "هزینه ۰٫۴۵٪ هر طرف، معامله با قیمت باز شدن روز بعد. نسبت = رشد سالانه ÷ بدترین افت.", "",
          "| منبع | دوره | روش | رشد سالانه | بدترین افت | نسبت | معامله در سال | در طلا |", "|---|---|---|---|---|---|---|---|"]
    for _, r in res.iterrows():
        L.append(f"| {r.source} | {r.period} | {r.method} | {r.cagr * 100:+.2f}٪ | {r.max_dd * 100:.1f}٪ | "
                 f"{r.ratio:.2f} | {r.trades_per_year:.1f} | {r.in_market * 100:.0f}٪ |")
    L += ["", "قبول شد؟ (بهتر از نگه داشتن در هر سه دوره و هر دو منبع)", ""]
    for m, (ok, n, t) in verdict.items():
        L.append(f"- {m}: {yn(ok)} (در {n} از {t} مورد بهتر)")
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
