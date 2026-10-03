#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
advisor.py - مشاور شخصی طلا و تتر (بازار XAUTUSDT والکس)

هیچ خرید و فروشی انجام نمی‌دهد و کلید API لازم ندارد. فقط داده عمومی والکس را می‌خواند.
دارایی‌ها فقط تتر و طلا هستند؛ تومان هیچ جا در محاسبات نیست.

کار برنامه:
  1. سه سؤال می‌پرسد (مقدار تتر، مقدار و قیمت خرید طلا، هدف)
  2. «واقعیت‌ها» را از داده زنده حساب می‌کند
  3. «نظر الگوریتم» را جدا و با برچسب آزمایشی نشان می‌دهد (فعلاً ساخته نشده)
  4. گزارش فارسی را در advisor_report.md می‌نویسد و نتیجه را در advice_log.csv ثبت می‌کند

اجرا:
  python3 advisor.py
"""
import os
import time

import numpy as np
import pandas as pd
import requests

SYMBOL = "XAUTUSDT"
HISTORY_URL = "https://api.wallex.ir/v1/udf/history"
DEPTH_URL = "https://api.wallex.ir/v1/depth"
HEADERS = {"User-Agent": "quant-research/1.0"}

FEE = 0.002              # کارمزد هر طرف در بازار تتری والکس
HISTORY_DAYS = 430       # برای سیگنال‌ها (میانگین ۲۹۰ روزه، مومنتوم ۳۶۵ روزه در آخر ماه قبل) و نوسان یک سال
TREND_DAYS = 200         # میانگین ۲۰۰ روزه
VOL_DAYS = 20            # نوسان «امروز» = نوسان ۲۰ روز اخیر
VOL_HIGH = 1.5           # بیش از ۱.۵ برابر حالت عادی = پرنوسان
VOL_CALM = 0.75          # کمتر از ۰.۷۵ برابر حالت عادی = آرام
GRAMS_PER_XAUT = 31.1035 # هر ۱ XAUT = ۱ اونس طلا ≈ ۳۱.۱ گرم
PRICE_CHECK = (0.5, 2.0) # قیمت خرید کمتر از نصف یا بیشتر از دو برابر قیمت فعلی = مشکوک

# سیگنال سه روش تست‌شده در backtest_slow.py. آن تست روی روزهای کاری بازار جهانی بود؛
# XAUTUSDT هر روز هفته معامله می‌شود، پس دوره‌ها به روز تقویمی تبدیل شده‌اند.
SIG_MA_LONG = 290        # ≈ ۲۰۰ روز کاری
SIG_MA_SHORT = 70        # ≈ ۵۰ روز کاری
SIG_MOM_DAYS = 365       # ۱۲ ماه
SIGNAL_NAMES = {"MA200_MONTHLY": ("میانگین ۲۰۰ روزه (ماهانه)", "MA200", "MA"),
                "MOM12_MONTHLY": ("مومنتوم ۱۲ ماهه (ماهانه)", "MOM12", "MOM"),
                "CROSS_50_200": ("تقاطع ۵۰/۲۰۰ (روزانه)", "50/200", "X")}
BACKTEST_RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slow_methods_results.csv")

REPORT_PATH = "advisor_report.md"
LOG_PATH = "advice_log.csv"

GOALS = {
    "1": ("long_term", "نگه داشتن بلندمدت"),
    "2": ("more_profit", "سود بیشتر"),
    "3": ("cash_out", "نقد کردن سریع"),
}


# ---------------------------------------------------------------- سؤال‌ها

def ask_number(prompt, allow_zero=True):
    while True:
        text = input(prompt).strip().replace(",", "")
        try:
            value = float(text)
        except ValueError:
            print("  Please enter a number, e.g. 250 or 0.5")
            continue
        if value < 0 or (value == 0 and not allow_zero):
            print("  Please enter a positive number.")
            continue
        return value


def price_looks_wrong(buy_price, market_price):
    """قیمت خرید خیلی دور از قیمت فعلی است؟ (مثلاً قیمت هر گرم به‌جای هر اونس)"""
    if not market_price:
        return False
    low, high = PRICE_CHECK
    return not (low * market_price <= buy_price <= high * market_price)


def ask_questions(market_price=None):
    print("=" * 62)
    print(" Personal gold/USDT advisor (XAUTUSDT) - no orders are placed")
    print("=" * 62)
    usdt = ask_number(" 1) How much USDT (Tether) do you hold? (1 USDT ~ 1 US dollar, e.g. 250) ")
    xaut = ask_number(" 2) How much gold do you hold, in XAUT, as shown in your Wallex wallet?\n"
                      "    (1 XAUT = 1 ounce = about 31.1 grams, e.g. 0.05) ")
    buy_price = None
    if xaut > 0:
        hint = f"; now about {market_price:,.0f}" if market_price else ""
        while True:
            buy_price = ask_number(f"    Average buy price, in USDT per 1 XAUT "
                                   f"(price per ounce, not per gram{hint}): ", allow_zero=False)
            if not price_looks_wrong(buy_price, market_price):
                break
            ok = input(f"    {buy_price:,.2f} is far from today's price ({market_price:,.0f}). "
                       f"Maybe a per-gram price? Keep it? (y/n): ").strip().lower()
            if ok in ("y", "yes"):
                break
    print(" 3) What is your goal?")
    print("    1 = hold long term   2 = more profit   3 = cash out soon")
    while True:
        g = input("    Choose 1, 2 or 3: ").strip()
        if g in GOALS:
            break
        print("    Please type 1, 2 or 3.")
    return {"usdt": usdt, "xaut": xaut, "buy_price": buy_price, "goal": g}


# ---------------------------------------------------------------- داده زنده

def fetch_daily(days=HISTORY_DAYS):
    """کندل‌های ساعتی را می‌گیرد و به کندل روزانه (به وقت تهران) تبدیل می‌کند."""
    end = int(time.time())
    start = end - days * 86400
    frames, t_to = [], end
    while t_to > start:
        t_from = max(start, t_to - 3600 * 500)
        d = None
        for attempt in range(4):
            try:
                r = requests.get(HISTORY_URL, headers=HEADERS, timeout=20, params={
                    "symbol": SYMBOL, "resolution": "60", "from": t_from, "to": t_to})
                d = r.json()
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        if isinstance(d, dict) and "s" not in d and isinstance(d.get("result"), dict):
            d = d["result"]
        if isinstance(d, dict) and d.get("s") == "ok" and d.get("t"):
            frames.append(pd.DataFrame({"time": d["t"], "close": d["c"]}))
        t_to = t_from
        time.sleep(0.5)
    if not frames:
        raise SystemExit(f"No price history received for {SYMBOL}.")
    df = (pd.concat(frames).apply(pd.to_numeric, errors="coerce").dropna()
          .drop_duplicates("time").sort_values("time"))
    df.index = pd.to_datetime(df.time.values, unit="s", utc=True).tz_convert("Asia/Tehran")
    daily = df.close.resample("D").last().dropna()
    # روز جاری هنوز تمام نشده؛ فقط روزهای بسته‌شده
    today = pd.Timestamp.now(tz="Asia/Tehran").normalize()
    return daily[daily.index < today]


def fetch_book():
    """بهترین قیمت خرید و فروش فعلی را از دفتر سفارش می‌گیرد. در صورت خطا None."""
    try:
        d = requests.get(DEPTH_URL, params={"symbol": SYMBOL}, headers=HEADERS, timeout=15).json()
        r = d.get("result", d)
        px = lambda side: [float(x["price"] if isinstance(x, dict) else x[0]) for x in side]
        asks = px(r.get("ask") or r.get("asks") or [])
        bids = px(r.get("bid") or r.get("bids") or [])
        return {"ask": min(asks), "bid": max(bids)}
    except Exception:
        return None


# ---------------------------------------------------------------- واقعیت‌ها

def compute_facts(answers, daily, book):
    f = {"book_live": book is not None}
    if book is None:
        last = float(daily.iloc[-1])
        book = {"ask": last, "bid": last}
    bid, ask = book["bid"], book["ask"]
    mid = (bid + ask) / 2
    f.update(bid=bid, ask=ask, mid=mid, spread=(ask - bid) / mid)

    # هزینه واقعی فروش همین الان، نسبت به قیمت وسط بازار
    f["sell_cost"] = 1 - bid * (1 - FEE) / mid

    xaut, usdt, buy_price = answers["xaut"], answers["usdt"], answers["buy_price"]
    f["gold_cash_now"] = xaut * bid * (1 - FEE)          # اگر الان همه طلا فروخته شود
    f["total_now"] = usdt + f["gold_cash_now"]
    f["gold_share"] = f["gold_cash_now"] / f["total_now"] if f["total_now"] > 0 else 0.0
    if xaut > 0:
        paid = xaut * buy_price
        f["gold_paid"] = paid
        f["pnl"] = f["gold_cash_now"] - paid
        f["pnl_pct"] = f["gold_cash_now"] / paid - 1
        # قیمتی که از آن به بعد فروش، بعد از کارمزد، ضرر نیست
        f["breakeven_bid"] = buy_price / (1 - FEE)
    else:
        f["pnl"] = f["pnl_pct"] = f["breakeven_bid"] = f["gold_paid"] = None

    # نوسان: نوسان ۲۰ روز اخیر در مقایسه با حالت عادی یک سال گذشته
    ret = np.log(daily).diff().dropna()
    roll = ret.rolling(VOL_DAYS).std().dropna()
    f["vol_now"] = float(roll.iloc[-1]) if len(roll) else None
    f["vol_normal"] = float(roll.iloc[-365:].median()) if len(roll) >= 60 else None
    if f["vol_now"] is not None and f["vol_normal"]:
        ratio = f["vol_now"] / f["vol_normal"]
        f["vol_ratio"] = ratio
        f["vol_state"] = "HIGH" if ratio > VOL_HIGH else ("CALM" if ratio < VOL_CALM else "NORMAL")
    else:
        f["vol_ratio"], f["vol_state"] = None, "UNKNOWN"

    # روند: قیمت نسبت به میانگین ۲۰۰ روزه
    if len(daily) >= TREND_DAYS:
        ma = float(daily.iloc[-TREND_DAYS:].mean())
        f["ma200"] = ma
        f["vs_ma200"] = mid / ma - 1
        f["trend"] = "UP" if mid > ma else "DOWN"
    else:
        f["ma200"] = f["vs_ma200"] = None
        f["trend"] = "UNKNOWN"

    # فاصله از بالاترین قیمت یک سال اخیر
    year = daily.iloc[-365:]
    f["year_high"] = float(year.max())
    f["from_year_high"] = mid / f["year_high"] - 1
    f["year_change"] = float(year.iloc[-1] / year.iloc[0] - 1)
    f["year_days"] = len(year)
    f["last_day"] = daily.index[-1]
    f["signals"] = trend_signals(daily)
    return f


def signal_levels(daily):
    """سیگنال هر روش و «خط تصمیم» آن (عددی که بالا یا پایین بودن قیمت از آن سیگنال را تعیین می‌کند).
    روش‌های ماهانه با قیمت آخرین روز ماه قبل تصمیم می‌گیرند (مثل تست)، تقاطع با آخرین روز بسته‌شده.
    signal: True = در طلا باشید، False = در تتر باشید، None = داده کافی نیست.
    line_next: خطی که در بررسی بعدی ملاک است (برای میانگین ۷ ماه، مقدار امروزش که تا آن روز کمی جابه‌جا می‌شود)."""
    month_start = pd.Timestamp.now(tz="Asia/Tehran").normalize().replace(day=1)
    next_check = month_start + pd.offsets.MonthBegin(1)
    upto = daily[daily.index < month_start]

    ma = {"signal": None, "line_next": None, "next_check": next_check}
    if len(upto) >= SIG_MA_LONG:
        ma["signal"] = bool(upto.iloc[-1] > upto.iloc[-SIG_MA_LONG:].mean())
    if len(daily) >= SIG_MA_LONG:
        ma["line_next"] = float(daily.iloc[-SIG_MA_LONG:].mean())

    mom = {"signal": None, "line_next": None, "next_check": next_check}
    if len(upto):
        past = upto[upto.index <= upto.index[-1] - pd.Timedelta(days=SIG_MOM_DAYS)]
        if len(past):
            mom["signal"] = bool(upto.iloc[-1] > past.iloc[-1])
    # بررسی بعدی با قیمت آخر همین ماه انجام می‌شود؛ قیمت ۱۲ ماه قبل از آن روز از الان معلوم است
    ref = daily[daily.index <= next_check - pd.Timedelta(days=1 + SIG_MOM_DAYS)]
    if len(ref) and len(daily) and (daily.index[0] <= ref.index[-1]):
        mom["line_next"] = float(ref.iloc[-1])

    cr = {"signal": None, "line_next": None, "short": None, "next_check": None}
    if len(daily) >= SIG_MA_LONG:
        cr["short"] = float(daily.iloc[-SIG_MA_SHORT:].mean())
        cr["line_next"] = float(daily.iloc[-SIG_MA_LONG:].mean())
        cr["signal"] = cr["short"] > cr["line_next"]
    return {"MA200_MONTHLY": ma, "MOM12_MONTHLY": mom, "CROSS_50_200": cr}


def trend_signals(daily):
    """True = در طلا باشید، False = در تتر باشید، None = داده کافی نیست."""
    return {m: v["signal"] for m, v in signal_levels(daily).items()}


def track_record():
    """کارنامه ۲۰ ساله هر روش از نتیجه backtest_slow.py (منبع GLD، کل دوره)."""
    try:
        r = pd.read_csv(BACKTEST_RESULTS)
    except (FileNotFoundError, ValueError):
        return {}
    r = r[(r.source == "GLD") & (r.period == "FULL")]
    return {row.method: row for row in r.itertuples()}


# ---------------------------------------------------------------- نظر الگوریتم

OPINION_EN = {
    "HOLD_AVOID_TRADING": "Hold; avoid moving in and out (no timing method beat holding in a 20-year test).",
    "HOLD_EVIDENCE": "Holding gave more profit than timing methods in a 20-year test.",
    "NO_TIMING_ADVICE": "No advice on when to sell; see the real cost of selling now.",
}


def algorithm_opinion(answers, f):
    """قطعه قابل تعویض. بر پایه تست ۲۰ ساله (backtest_slow.py): هیچ روش زمان‌بندی از نگه داشتن بهتر نبود.
    خروجی: (کد کوتاه برای لاگ، فهرست جمله‌های فارسی برای گزارش)"""
    lines = ["در تست ۲۰ ساله روی قیمت جهانی طلا، هیچ روشی برای زمان‌بندی خرید و فروش "
             "از «فقط نگه داشتن» بهتر نبود."]
    goal = answers["goal"]
    if goal == "1":
        code = "HOLD_AVOID_TRADING"
        lines.append("برای هدف «نگه داشتن بلندمدت»: نگه دارید و از جابه‌جایی پرهیز کنید.")
    elif goal == "2":
        code = "HOLD_EVIDENCE"
        lines.append("برای هدف «سود بیشتر»: شواهد نشان می‌دهد نگه داشتن، سود بیشتری از جابه‌جایی داده است.")
    else:
        code = "NO_TIMING_ADVICE"
        if answers["xaut"] > 0:
            cost_usdt = answers["xaut"] * f["mid"] * f["sell_cost"]
            lines.append(f"برای هدف «نقد کردن سریع»: هزینه واقعی فروش همه طلا همین الان "
                         f"{pct(f['sell_cost'], False, 3)} است، یعنی حدود {cost_usdt:,.2f} تتر. "
                         "درباره زمان فروش پیشنهادی نمی‌دهیم.")
        else:
            lines.append("برای هدف «نقد کردن سریع»: طلایی برای فروش ندارید؛ دارایی شما همین الان تتر است.")
    lines.extend(signal_lines(f["signals"]))
    code += "|" + "|".join(f"{SIGNAL_NAMES[m][2]}:{'NA' if v is None else ('GOLD' if v else 'USDT')}"
                           for m, v in f["signals"].items())
    return code, lines


def signal_lines(signals):
    rec = track_record()
    L = ["سیگنال سه روش تست‌شده برای امروز (فقط برای اطلاع؛ در تست هیچ‌کدام سود را از نگه داشتن "
         "بیشتر نکرد، ولی در دوره‌های ریزشی افت را کمتر کرد):"]
    for m, v in signals.items():
        state = "داده کافی نیست" if v is None else ("می‌گوید در طلا باشید" if v else "می‌گوید در تتر باشید")
        line = f"  - {SIGNAL_NAMES[m][0]}: {state}"
        if m in rec:
            line += (f" — کارنامه ۲۰ ساله: سود سالانه {rec[m].cagr * 100:+.1f}٪، "
                     f"بدترین افت {rec[m].max_dd * 100:.0f}٪، حدود {rec[m].trades_per_year:.1f} جابه‌جایی در سال")
        L.append(line)
    if "HOLD" in rec:
        h = rec["HOLD"]
        L.append(f"  - برای مقایسه، فقط نگه داشتن: سود سالانه {h.cagr * 100:+.1f}٪، "
                 f"بدترین افت {h.max_dd * 100:.0f}٪")
    known = [v for v in signals.values() if v is not None]
    if known:
        L.append(f"  - جمع: {sum(known)} از {len(known)} روش می‌گویند در طلا باشید.")
    return L


# ---------------------------------------------------------------- خروجی

def pct(x, sign=True, digits=2):
    if x is None:
        return "n/a"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def grams(xaut):
    return xaut * GRAMS_PER_XAUT


def print_summary(answers, f, opinion):
    print()
    print("=" * 62)
    print(f" FACTS  ({SYMBOL}, {pd.Timestamp.now(tz='Asia/Tehran'):%Y-%m-%d %H:%M} Tehran)")
    print("=" * 62)
    note = "" if f["book_live"] else "  (live price failed, using last close)"
    print(f" Sell price now (bid)     : {f['bid']:,.2f} USDT{note}")
    print(f" Buy price now (ask)      : {f['ask']:,.2f} USDT")
    print(f" Buy/sell gap             : {pct(f['spread'], False, 3)}")
    print(f" Cost to sell now         : {pct(f['sell_cost'], False, 3)} (fee + half the gap)")
    if answers["xaut"] > 0:
        print(f" Gold held                : {answers['xaut']:,.6g} XAUT "
              f"(about {grams(answers['xaut']):,.2f} grams)")
        print(f" Gold profit/loss if sold : {f['pnl']:+,.2f} USDT ({pct(f['pnl_pct'])})")
        print(f" Break-even sell price    : {f['breakeven_bid']:,.2f} USDT")
    print(f" Total value now          : {f['total_now']:,.2f} USDT "
          f"(gold share {f['gold_share'] * 100:.0f}%)")
    print(f" Volatility vs normal     : {f['vol_state']}"
          + (f" ({f['vol_ratio']:.2f}x)" if f["vol_ratio"] is not None else ""))
    print(f" Trend (200-day average)  : {f['trend']}"
          + (f" ({pct(f['vs_ma200'])} from average)" if f["vs_ma200"] is not None else ""))
    print(f" From 1-year high         : {pct(f['from_year_high'])}")
    print("-" * 62)
    print(" ALGORITHM OPINION (EXPERIMENTAL):")
    print(f"   {OPINION_EN[opinion[0].split('|')[0]]}")
    sig = "  ".join(f"{SIGNAL_NAMES[m][1]}={'n/a' if v is None else ('GOLD' if v else 'USDT')}"
                    for m, v in f["signals"].items())
    print(f"   Signals (tested; did not beat holding): {sig}")
    print("-" * 62)
    print(f" Full Persian report: {REPORT_PATH}")
    print(f" Logged to          : {LOG_PATH}")


def report_text(answers, f, opinion):
    """متن کامل گزارش فارسی (برای فایل و برای ربات بله)."""
    now = pd.Timestamp.now(tz="Asia/Tehran")
    vol_fa = {"HIGH": "بالاتر از حالت عادی (بازار پرتلاطم است)",
              "NORMAL": "در حد عادی", "CALM": "آرام‌تر از حالت عادی",
              "UNKNOWN": "داده کافی نیست"}[f["vol_state"]]
    trend_fa = {"UP": "بالای میانگین ۲۰۰ روزه (روند کلی رو به بالا)",
                "DOWN": "زیر میانگین ۲۰۰ روزه (روند کلی رو به پایین)",
                "UNKNOWN": "داده کافی برای میانگین ۲۰۰ روزه نیست"}[f["trend"]]
    L = []
    L.append(f"# گزارش مشاور طلا و تتر — {now:%Y-%m-%d %H:%M} (تهران)\n")
    L.append("> این برنامه هیچ خرید و فروشی انجام نمی‌دهد. فقط اطلاعات می‌دهد.\n")

    L.append("## جواب‌های شما\n")
    L.append(f"- تتر (USDT): {answers['usdt']:,.2f}")
    L.append(f"- طلا: {answers['xaut']:,.6g} XAUT (حدود {grams(answers['xaut']):,.2f} گرم)"
             + (f" — قیمت خرید هر ۱ XAUT (هر اونس): {answers['buy_price']:,.2f} تتر"
                if answers["xaut"] > 0 else ""))
    L.append(f"- هدف: {GOALS[answers['goal']][1]}\n")

    L.append("## بخش ۱: واقعیت‌ها (قابل اعتماد)\n")
    if not f["book_live"]:
        L.append("⚠️ قیمت زنده گرفته نشد؛ آخرین قیمت بسته‌شده روزانه استفاده شد.\n")
    L.append(f"- **قیمتی که الان می‌توانید بفروشید:** {f['bid']:,.2f} تتر")
    L.append(f"- **قیمتی که الان می‌توانید بخرید:** {f['ask']:,.2f} تتر")
    L.append(f"- **هزینه واقعی فروش همین الان:** {pct(f['sell_cost'], False, 3)} "
             f"(کارمزد {FEE * 100:.1f}٪ + نصف فاصله قیمت خرید و فروش که {pct(f['spread'], False, 3)} است)")
    if answers["xaut"] > 0 and price_looks_wrong(answers["buy_price"], f["mid"]):
        L.append("- ⚠️ **قیمت خرید واردشده خیلی با قیمت امروز فرق دارد.** "
                 "اگر قیمت هر گرم را زده‌اید، عددهای سود و زیان درست نیستند.")
    if answers["xaut"] > 0:
        word = "سود" if f["pnl"] >= 0 else "زیان"
        L.append(f"- **اگر همین الان همه طلا را بفروشید:** {f['gold_cash_now']:,.2f} تتر "
                 f"می‌گیرید. شما {f['gold_paid']:,.2f} تتر پرداخت کرده بودید، پس "
                 f"**{word} {abs(f['pnl']):,.2f} تتر ({pct(f['pnl_pct'])})**.")
        L.append(f"- **قیمت سر به سر:** اگر قیمت فروش به {f['breakeven_bid']:,.2f} تتر برسد، "
                 f"بعد از کارمزد نه سود کرده‌اید نه ضرر.")
    L.append(f"- **کل دارایی الان:** {f['total_now']:,.2f} تتر "
             f"({f['gold_share'] * 100:.0f}٪ آن طلاست)")
    L.append(f"- **نوسان:** {vol_fa}"
             + (f" — نوسان روزانه ۲۰ روز اخیر {pct(f['vol_now'], False)} "
                f"در برابر حالت عادی {pct(f['vol_normal'], False)}" if f["vol_ratio"] is not None else ""))
    L.append(f"- **روند:** قیمت {trend_fa}"
             + (f"؛ فاصله از میانگین {pct(f['vs_ma200'])}" if f["vs_ma200"] is not None else ""))
    L.append(f"- **فاصله از بالاترین قیمت یک سال اخیر** ({f['year_high']:,.2f}): {pct(f['from_year_high'])}")
    L.append(f"- **تغییر قیمت در {f['year_days']} روز اخیر**: {pct(f['year_change'])}\n")

    L.append("## بخش ۲: نظر الگوریتم (آزمایشی)\n")
    L.append("> این بخش نظر است، نه واقعیت. بر پایه تست ۲۰ ساله قیمت جهانی طلا "
             "(گزارش کامل: slow_methods_report.md). گذشته تضمین آینده نیست.\n")
    L.extend(line if line.startswith("  ") else f"- {line}" for line in opinion[1])
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- گزارش کوتاه (برای بله)

FA_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
             "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def to_jalali(gy, gm, gd):
    """تاریخ میلادی به شمسی (الگوریتم استاندارد)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
            + gd + g_d_m[gm - 1])
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def fa(x, decimals=0, sep=True):
    """عدد با رقم و جداکننده فارسی، بدون علامت + و - (جهت را با کلمه می‌گوییم)."""
    text = f"{abs(x):,.{decimals}f}" if sep else f"{abs(x):.{decimals}f}"
    return text.translate(str.maketrans("0123456789,.", "۰۱۲۳۴۵۶۷۸۹٬٫"))


def short_text(answers, f, opinion):
    """گزارش کوتاه و ساده برای پیام بله. جزئیات کامل در report_text است."""
    now = pd.Timestamp.now(tz="Asia/Tehran")
    jy, jm, jd = to_jalali(now.year, now.month, now.day)
    L = [f"📊 گزارش طلا و تتر — {fa(jd)} {FA_MONTHS[jm - 1]} {fa(jy, sep=False)}، "
         f"ساعت {fa(now.hour)}:{fa(now.minute).rjust(2, '۰')}"]
    if not f["book_live"]:
        L.append("⚠️ قیمت زنده گرفته نشد؛ آخرین قیمت دیروز استفاده شد.")
    xaut = answers["xaut"]

    L += ["", "💰 دارایی شما",
          f"• کل: {fa(f['total_now'])} تتر ({fa(f['gold_share'] * 100)}٪ طلا)"]
    if xaut > 0:
        L.append(f"• طلا: {fa(xaut, 4).rstrip('۰').rstrip('٫')} واحد (حدود {fa(grams(xaut))} گرم)")
        word = "سود" if f["pnl"] >= 0 else "زیان"
        L.append(f"• اگر الان بفروشید: {fa(f['pnl'])} تتر {word} ({fa(f['pnl_pct'] * 100, 1)}٪)")
        L.append(f"• قیمت سر به سر: {fa(f['breakeven_bid'])} تتر (بالاتر از این، فروش ضرر ندارد)")
        if price_looks_wrong(answers["buy_price"], f["mid"]):
            L.append("⚠️ قیمت خریدی که وارد کردید خیلی با قیمت امروز فرق دارد؛ شاید قیمت هر گرم بوده.")

    L += ["", "🏷 قیمت هر واحد طلا الان",
          f"• فروش: {fa(f['bid'])} تتر — خرید: {fa(f['ask'])} تتر",
          f"• هزینه هر فروش: {fa(f['sell_cost'] * 100, 1)}٪"
          + (f" (برای همه طلای شما حدود {fa(xaut * f['mid'] * f['sell_cost'])} تتر)" if xaut > 0 else "")]

    trend = {"UP": "رو به بالا", "DOWN": "رو به پایین", "UNKNOWN": "معلوم نیست (داده کم است)"}[f["trend"]]
    L += ["", "📈 حال بازار", f"• روند: {trend}"]
    if f["vs_ma200"] is not None:
        where = "بالاتر" if f["vs_ma200"] >= 0 else "پایین‌تر"
        L[-1] += f" — قیمت {fa(abs(f['vs_ma200']) * 100)}٪ {where} از میانگین ۷ ماه اخیر"
    vol = {"HIGH": "زیاد (قیمت تند بالا و پایین می‌رود)", "NORMAL": "عادی",
           "CALM": "کم (بازار آرام است)", "UNKNOWN": "معلوم نیست"}[f["vol_state"]]
    L.append(f"• تلاطم: {vol}")
    L.append(f"• نسبت به بالاترین قیمت یک سال اخیر: {fa(abs(f['from_year_high']) * 100)}٪ پایین‌تر")

    goal = answers["goal"]
    if goal == "1":
        advice = "نگه دارید و کمتر خرید و فروش کنید."
    elif goal == "2":
        advice = "در ۲۰ سال گذشته، نگه داشتن بیشتر از خرید و فروش مکرر سود داده است."
    elif xaut > 0:
        advice = (f"فروش همه طلا الان حدود {fa(xaut * f['mid'] * f['sell_cost'])} تتر هزینه دارد. "
                  "درباره زمان فروش نظری نمی‌دهیم.")
    else:
        advice = "طلایی برای فروش ندارید؛ دارایی شما تتر است."
    L += ["", "🤖 نظر ربات (آزمایشی)", f"• هدف شما «{GOALS[goal][1]}» است: {advice}"]
    if any(v is not None for v in f["signals"].values()):
        L.append(f"• سیگنال: {signal_vote(f['signals'])} (برای جزئیات «📡 سیگنال امروز» را بزنید.)")
    L += ["", "ℹ️ این ربات خرید و فروش انجام نمی‌دهد. برای توضیح بیشتر «جزئیات کامل» را بزنید."]
    return "\n".join(L)


# ---------------------------------------------------------------- سیگنال (برای بله)

SIGNAL_PLAIN = {"MA200_MONTHLY": "میانگین ۷ ماه",
                "MOM12_MONTHLY": "مقایسه با پارسال",
                "CROSS_50_200": "میانگین ۲ ماه و ۷ ماه"}
SIGNAL_REMINDER = ("یادآوری: این سیگنال‌ها در ۲۰ سال گذشته سود را بیشتر نکردند؛ "
                   "فقط در ریزش‌ها ضرر را کمتر کردند. تصمیم با شماست.")


def signal_word(v):
    return "داده کافی نیست" if v is None else ("طلا 🟡" if v else "تتر 💵")


def signal_vote(signals):
    """یک جمله: اکثر روش‌ها چه می‌گویند."""
    known = [v for v in signals.values() if v is not None]
    if not known:
        return "داده کافی برای سیگنال نیست."
    gold = sum(known)
    usdt = len(known) - gold
    if gold == usdt:
        return "روش‌ها نظر یکسانی ندارند (نصف طلا، نصف تتر)."
    side = "طلا" if gold > usdt else "تتر"
    if min(gold, usdt) == 0:
        return f"هر {fa(len(known))} روش می‌گویند «{side} نگه دارید»."
    return f"{fa(max(gold, usdt))} روش از {fa(len(known))} روش می‌گویند «{side} نگه دارید»."


def jalali_now():
    now = pd.Timestamp.now(tz="Asia/Tehran")
    jy, jm, jd = to_jalali(now.year, now.month, now.day)
    return f"{fa(jd)} {FA_MONTHS[jm - 1]}، ساعت {fa(now.hour)}:{fa(now.minute).rjust(2, '۰')}"


def jalali_date(ts):
    jy, jm, jd = to_jalali(ts.year, ts.month, ts.day)
    return f"{fa(jd)} {FA_MONTHS[jm - 1]}"


def signal_text(levels, price):
    """پیام «سیگنال امروز»: اول نتیجه، بعد هر روش در دو جمله."""
    signals = {m: v["signal"] for m, v in levels.items()}
    L = [f"📡 سیگنال امروز — {jalali_now()}"]
    if price:
        L.append(f"قیمت طلا: {fa(price)} تتر")
    L += ["", f"نتیجه: {signal_vote(signals)}"]
    for i, (m, v) in enumerate(levels.items(), 1):
        L += ["", f"{fa(i)}. {SIGNAL_PLAIN[m]}: {signal_word(v['signal'])}"]
        line, sig = v["line_next"], v["signal"]
        if line is None or sig is None:
            continue
        other = "تتر" if sig else "طلا"
        if m == "CROSS_50_200":
            where = "بالای" if sig else "زیر"
            move = "پایین‌تر" if sig else "بالاتر"
            L.append(f"میانگین ۲ ماه ({fa(v['short'])}) {where} میانگین ۷ ماه ({fa(line)}) است.")
            L.append(f"اگر {move} برود، سیگنال «{other}» می‌شود.")
            continue
        what = "میانگین ۷ ماه" if m == "MA200_MONTHLY" else "قیمت پارسال"
        if price:
            where = "بالاتر از" if price >= line else "زیر"
            L.append(f"قیمت {fa(abs(price / line - 1) * 100)}٪ {where} {what} ({fa(line)}) است.")
        cond = "زیر" if sig else "بالای"
        L.append(f"اگر آخر ماه ({jalali_date(v['next_check'] - pd.Timedelta(days=1))}) قیمت {cond} "
                 f"{fa(line)} باشد، سیگنال «{other}» می‌شود.")
    L += ["", SIGNAL_REMINDER]
    return "\n".join(L)


def signal_change_text(changes, signals, price):
    """پیام خبر تغییر سیگنال. changes = [(روش، قبلی، الان)، ...]"""
    L = [f"🔔 سیگنال عوض شد — {jalali_now()}"]
    if price:
        L.append(f"قیمت طلا: {fa(price)} تتر")
    L.append("")
    for m, old, new in changes:
        L.append(f"• {SIGNAL_PLAIN[m]}: از «{'طلا' if old else 'تتر'}» به «{'طلا' if new else 'تتر'}» عوض شد.")
    L += ["", f"نتیجه: {signal_vote(signals)}", "", SIGNAL_REMINDER]
    return "\n".join(L)


def write_report(answers, f, opinion):
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write(report_text(answers, f, opinion))


def write_log(answers, f, opinion):
    row = {
        "checked_at": pd.Timestamp.now(tz="Asia/Tehran").strftime("%Y-%m-%d %H:%M"),
        "bid": round(f["bid"], 2), "ask": round(f["ask"], 2), "live_price": f["book_live"],
        "usdt": answers["usdt"], "xaut": answers["xaut"], "buy_price": answers["buy_price"],
        "goal": GOALS[answers["goal"]][0],
        "pnl_pct": None if f["pnl_pct"] is None else round(f["pnl_pct"], 5),
        "vol_state": f["vol_state"], "trend": f["trend"], "opinion": opinion[0],
    }
    pd.DataFrame([row]).to_csv(LOG_PATH, mode="a", header=not os.path.exists(LOG_PATH),
                               index=False)


def main():
    book = fetch_book()
    answers = ask_questions(None if book is None else (book["bid"] + book["ask"]) / 2)
    print("\n Fetching live data from Wallex (about 30 seconds)...")
    daily = fetch_daily()
    book = fetch_book()
    facts = compute_facts(answers, daily, book)
    opinion = algorithm_opinion(answers, facts)
    print_summary(answers, facts, opinion)
    write_report(answers, facts, opinion)
    write_log(answers, facts, opinion)


if __name__ == "__main__":
    main()
