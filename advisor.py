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
    """گزارش کوتاه بله، به زبان گفت‌وگو. جزئیات کامل در report_text است."""
    now = pd.Timestamp.now(tz="Asia/Tehran")
    jy, jm, jd = to_jalali(now.year, now.month, now.day)
    xaut = answers["xaut"]
    L = [f"📊 وضعیت شما — {fa(jd)} {FA_MONTHS[jm - 1]}", ""]
    if not f["book_live"]:
        L += ["⚠️ قیمت لحظه‌ای گرفته نشد؛ قیمت دیروز حساب شد.", ""]

    if xaut > 0:
        L.append(f"دارایی شما الان حدود {fa(f['total_now'])} تتر می‌ارزد؛ "
                 f"{fa(f['gold_share'] * 100)}٪ آن طلاست.")
        if f["pnl"] >= 0:
            L.append(f"اگر همین الان طلا را بفروشید، {fa(f['pnl'])} تتر سود می‌کنید "
                     f"({fa(f['pnl_pct'] * 100, 1)}٪).")
            L.append(f"تا وقتی قیمت بالای {fa(f['breakeven_bid'])} تتر باشد، فروختن ضرر ندارد.")
        else:
            L.append(f"اگر همین الان طلا را بفروشید، {fa(f['pnl'])} تتر ضرر می‌کنید "
                     f"({fa(f['pnl_pct'] * 100, 1)}٪).")
            L.append(f"برای اینکه بی‌ضرر بفروشید، قیمت باید به {fa(f['breakeven_bid'])} تتر برسد.")
        L.append(f"کارمزد فروش همه طلا الان حدود {fa(xaut * f['mid'] * f['sell_cost'])} تتر است.")
        if price_looks_wrong(answers["buy_price"], f["mid"]):
            L.append("⚠️ قیمت خریدی که دادید خیلی با قیمت امروز فرق دارد؛ شاید قیمت هر گرم بوده.")
    else:
        L.append(f"دارایی شما {fa(f['total_now'])} تتر است و طلا ندارید.")

    L += ["", f"قیمت طلا الان {fa(f['mid'])} تتر است."]
    if f["trend"] == "DOWN":
        L.append("در چند ماه اخیر قیمت پایین آمده.")
    elif f["trend"] == "UP":
        L.append("در چند ماه اخیر قیمت بالا رفته.")
    if f["from_year_high"] < -0.02:
        L.append(f"الان {fa(abs(f['from_year_high']) * 100)}٪ ارزان‌تر از گران‌ترین قیمت یک سال اخیر است.")
    calm = {"HIGH": "بازار این روزها پرتلاطم است و قیمت تند بالا و پایین می‌رود.",
            "CALM": "بازار این روزها آرام است.", "NORMAL": "بازار این روزها حالت عادی دارد."}
    if f["vol_state"] in calm:
        L.append(calm[f["vol_state"]])

    goal = answers["goal"]
    if goal == "1":
        advice = "هدف شما نگه داشتن بلندمدت است؛ پیشنهاد ربات این است که نگه دارید و کمتر خرید و فروش کنید."
    elif goal == "2":
        advice = ("هدف شما سود بیشتر است؛ در ۲۰ سال گذشته کسانی که طلا را نگه داشتند "
                  "بیشتر از کسانی که مدام خرید و فروش کردند سود بردند.")
    elif xaut > 0:
        advice = "هدف شما نقد کردن است؛ ربات درباره زمان فروش نظری نمی‌دهد. هزینه‌اش را بالا نوشتیم."
    else:
        advice = "هدف شما نقد کردن است و دارایی شما همین الان تتر است."
    L += ["", f"🤖 نظر ربات: {advice}", "",
          "ربات خودش خرید و فروش نمی‌کند. برای توضیح بیشتر «جزئیات کامل» را بزنید."]
    return "\n".join(L)

# ---------------------------------------------------------------- سیگنال (برای بله)

SIGNAL_PLAIN = {"MA200_MONTHLY": "میانگین ۷ ماه",
                "MOM12_MONTHLY": "مقایسه با پارسال",
                "CROSS_50_200": "میانگین ۲ ماه و ۷ ماه"}
SIGNAL_REMINDER = ("یادآوری: در ۲۰ سال گذشته، عمل کردن به این سیگنال‌ها سود بیشتری از "
                   "نگه داشتن ساده طلا نداده. فقط در دوره‌های ریزش، ضرر را کمتر کرده.")


def signal_word(v):
    return "داده کافی نیست" if v is None else ("طلا 🟡" if v else "تتر 💵")


def signal_vote(signals):
    """یک جمله ساده: بیشتر روش‌ها چه می‌گویند."""
    known = [v for v in signals.values() if v is not None]
    if not known:
        return "داده کافی برای حساب کردن نیست."
    gold = sum(known)
    usdt = len(known) - gold
    if gold == usdt:
        return "روش‌ها با هم موافق نیستند."
    if min(gold, usdt) == 0:
        who = "هر " + fa(len(known)) + " روش"
    else:
        who = f"{fa(max(gold, usdt))} روش از {fa(len(known))} روش"
    if gold > usdt:
        return f"{who} می‌گویند الان بهتر است طلا داشته باشید."
    return f"{who} می‌گویند الان بهتر است پول در تتر باشد، نه طلا."
def jalali_now():
    now = pd.Timestamp.now(tz="Asia/Tehran")
    jy, jm, jd = to_jalali(now.year, now.month, now.day)
    return f"{fa(jd)} {FA_MONTHS[jm - 1]}، ساعت {fa(now.hour)}:{fa(now.minute).rjust(2, '۰')}"


def jalali_date(ts):
    jy, jm, jd = to_jalali(ts.year, ts.month, ts.day)
    return f"{fa(jd)} {FA_MONTHS[jm - 1]}"


def signal_text(levels, price):
    """پیام «سیگنال امروز» به زبان گفت‌وگو."""
    signals = {m: v["signal"] for m, v in levels.items()}
    L = [f"📡 سیگنال امروز — {jalali_now()}"]
    if price:
        L.append(f"قیمت طلا: {fa(price)} تتر")
    L += ["", signal_vote(signals), "", "چرا؟"]
    ma, mom, cr = levels["MA200_MONTHLY"], levels["MOM12_MONTHLY"], levels["CROSS_50_200"]
    if ma["line_next"] is not None and price:
        if price < ma["line_next"]:
            L.append(f"• قیمت از میانگین ۷ ماه اخیر ({fa(ma['line_next'])}) پایین‌تر است؛ یعنی بازار ضعیف شده.")
        else:
            L.append(f"• قیمت از میانگین ۷ ماه اخیر ({fa(ma['line_next'])}) بالاتر است؛ یعنی بازار قوی است.")
    if mom["line_next"] is not None and price:
        if price >= mom["line_next"]:
            L.append(f"• قیمت از پارسال همین موقع ({fa(mom['line_next'])}) بالاتر است؛ یعنی در یک سال رشد کرده.")
        else:
            L.append(f"• قیمت از پارسال همین موقع ({fa(mom['line_next'])}) پایین‌تر است؛ یعنی در یک سال افت کرده.")
    if cr["signal"] is not None:
        L.append("• در ۲ ماه اخیر قیمت " + ("بالا رفته." if cr["signal"] else "پایین آمده."))
    nxt = []
    for v, name in ((ma, "اول"), (mom, "دوم")):
        if v["signal"] is None or v["line_next"] is None:
            continue
        side = "پایین‌تر از" if v["signal"] else "بالاتر از"
        nxt.append(f"اگر قیمت آخر ماه ({jalali_date(v['next_check'] - pd.Timedelta(days=1))}) "
                   f"{side} {fa(v['line_next'])} باشد، روش {name} نظرش عوض می‌شود.")
    if nxt:
        L += ["", "کی نظرشان عوض می‌شود؟"] + nxt
    L += ["", SIGNAL_REMINDER]
    return "\n".join(L)
def signal_change_text(changes, signals, price):
    """پیام خبر تغییر سیگنال. changes = [(روش، قبلی، الان)، ...]"""
    L = [f"🔔 یکی از سیگنال‌ها عوض شد — {jalali_now()}"]
    if price:
        L.append(f"قیمت طلا: {fa(price)} تتر")
    L.append("")
    for m, old, new in changes:
        L.append(f"روش «{SIGNAL_PLAIN[m]}» تا حالا می‌گفت {'طلا' if old else 'تتر'}، "
                 f"حالا می‌گوید {'طلا' if new else 'تتر'}.")
    L += ["", f"جمع‌بندی: {signal_vote(signals)}", "", SIGNAL_REMINDER]
    return "\n".join(L)

# ---------------------------------------------------------------- پیش‌بینی حدودی (برای بله)

FORECAST_LOOKBACK = 84        # روز تقویمی ≈ ۶۰ روز کاری، همان که forecast_check.py تست کرده
FORECAST_HORIZONS = ((7, "تا یک هفته دیگر"), (30, "تا یک ماه دیگر"))
FORECAST_HIT = (69, 94)       # درصد درستی بازه‌ها در ۲۱ سال (خروجی forecast_check.py)
FORECAST_LOG_PATH = "forecast_log.csv"


def price_range(daily, price, days):
    """بازه احتمالی قیمت بعد از days روز: (پایین، بالا) برای «به احتمال زیاد» و «تقریباً حتماً».
    مرکز بازه قیمت امروز است؛ جهت را حدس نمی‌زنیم، فقط اندازه تکان‌های اخیر را می‌گیریم."""
    sig = float(np.log(daily).diff().dropna().iloc[-FORECAST_LOOKBACK:].std())
    s = sig * np.sqrt(days)
    return (price * np.exp(-s), price * np.exp(s)), (price * np.exp(-2 * s), price * np.exp(2 * s))


def forecast_text(daily, price):
    """پیام «پیش‌بینی حدودی» + ثبت در لاگ برای سنجش بعدی."""
    if price is None:
        price = float(daily.iloc[-1])
    L = [f"🔮 قیمت طلا در آینده نزدیک — {jalali_now()}", f"قیمت الان: {fa(price)} تتر", "",
         "هیچ‌کس نمی‌داند طلا بالا می‌رود یا پایین. ولی می‌دانیم معمولاً چقدر تکان می‌خورد:", ""]
    rows = []
    now = pd.Timestamp.now(tz="Asia/Tehran").strftime("%Y-%m-%d %H:%M")
    for days, label in FORECAST_HORIZONS:
        (lo1, hi1), (lo2, hi2) = price_range(daily, price, days)
        L.append(f"• {label.replace('تا ', '')}: به احتمال زیاد بین {fa(lo1)} و {fa(hi1)} تتر")
        rows.append({"made_at": now, "price": round(price, 2), "days": days,
                     "likely_low": round(lo1, 2), "likely_high": round(hi1, 2),
                     "sure_low": round(lo2, 2), "sure_high": round(hi2, 2)})
    L += ["", "«به احتمال زیاد» یعنی از هر ۳ بار، ۲ بار. در ۲۱ سال گذشته همین‌قدر درست بوده.",
          "گاهی هم حرکت بزرگ‌تری پیش می‌آید، ولی کم."]
    pd.DataFrame(rows).to_csv(FORECAST_LOG_PATH, mode="a", header=not os.path.exists(FORECAST_LOG_PATH),
                              index=False)
    return "\n".join(L)


# ---------------------------------------------------------------- پیش‌بینی ۲ دقیقه (برای بله)

TWO_MIN_LOG_PATH = "forecast_2min_log.csv"
TWO_MIN_RANGE_HIT = (72, 94)      # درستی بازه ۲ دقیقه در آزمایش (خروجی backtest_2min.py)
INDICATOR_HIT = {"RSI(14)": 51.3, "MACD(12,26,9)": 50.5, "Bollinger(20,2)": 50.6,
                 "SMA 9/21 cross": 50.5, "Momentum(1)": 49.5}   # درصد درستی جهت در همان آزمایش
INDICATOR_LOG = {"RSI(14)": "rsi", "MACD(12,26,9)": "macd", "Bollinger(20,2)": "bollinger",
                 "SMA 9/21 cross": "sma_9_21", "Momentum(1)": "momentum"}
INDICATOR_FA = {"RSI(14)": "RSI", "MACD(12,26,9)": "MACD", "Bollinger(20,2)": "بولینگر",
                "SMA 9/21 cross": "میانگین ۹ و ۲۱", "Momentum(1)": "مومنتوم"}


def fetch_minutes(hours=4):
    """کندل‌های ۱ دقیقه‌ای چند ساعت اخیر والکس را می‌گیرد و به کندل ۲ دقیقه‌ای تبدیل می‌کند."""
    end = int(time.time())
    r = requests.get(HISTORY_URL, headers=HEADERS, timeout=20, params={
        "symbol": SYMBOL, "resolution": "1", "from": end - hours * 3600, "to": end})
    d = r.json()
    if isinstance(d, dict) and "s" not in d and isinstance(d.get("result"), dict):
        d = d["result"]
    if not (isinstance(d, dict) and d.get("s") == "ok" and d.get("t")):
        raise SystemExit("No minute data received.")
    df = pd.DataFrame({"time": d["t"], "open": d["o"], "close": d["c"]}).apply(pd.to_numeric, errors="coerce")
    df = df.dropna().drop_duplicates("time").sort_values("time")
    df.index = pd.to_datetime(df.time.values, unit="s", utc=True)
    bars = df.resample("2min").agg({"open": "first", "close": "last"}).dropna()
    if len(bars) and bars.index[-1] + pd.Timedelta("2min") > pd.Timestamp.now(tz="UTC"):
        bars = bars.iloc[:-1]                  # کندل نیمه‌کاره
    if len(bars) < 40:
        raise SystemExit("Not enough minute data.")
    return bars.reset_index(drop=True)


def two_min_text(bars, price):
    """پیام «پیش‌بینی ۲ دقیقه»: بازه آزمایش‌شده + نظر اندیکاتورها با درصد درستی‌شان."""
    from backtest_2min import signals   # همان کدی که آزمایش شده
    if price is None:
        price = float(bars.close.iloc[-1])
    lr = np.log(bars.close).diff()
    sigma = float(np.sqrt(lr.pow(2).ewm(alpha=0.06, adjust=False).mean().iloc[-1]))
    lo1, hi1 = price * np.exp(-sigma), price * np.exp(sigma)
    lo2, hi2 = price * np.exp(-2 * sigma), price * np.exp(2 * sigma)
    now = pd.Timestamp.now(tz="Asia/Tehran")
    last = signals(bars).iloc[-1]
    votes = {m: int(v) for m, v in last.items()}
    up = sum(v > 0 for v in votes.values())
    down = sum(v < 0 for v in votes.values())
    parts = [f"{fa(up)} تا می‌گویند بالا" if up else "", f"{fa(down)} تا می‌گویند پایین" if down else ""]
    said = "، ".join(p for p in parts if p) or "هیچ‌کدام نظری ندارند"
    if up > down:
        said = f"{fa(up)} تا از ۵ ابزار تریدرها می‌گویند بالا می‌رود"
    elif down > up:
        said = f"{fa(down)} تا از ۵ ابزار تریدرها می‌گویند پایین می‌آید"
    else:
        said = "ابزارهای تریدرها با هم موافق نیستند"
    L = [f"⏱ ۲ دقیقه بعد — ساعت {fa(now.hour)}:{fa(now.minute).rjust(2, '۰')}",
         f"قیمت الان: {fa(price)} تتر", "",
         f"تا ۲ دقیقه دیگر قیمت به احتمال زیاد بین {fa(lo1)} و {fa(hi1)} می‌ماند؛ "
         "یعنی تقریباً تکان نمی‌خورد.", "",
         f"{said}.",
         "ولی این ابزارها را روی ۱۷ هزار بار قیمت واقعی امتحان کردیم: فقط نصف دفعه‌ها درست گفتند؛ "
         "مثل شیر یا خط."]
    row = {"made_at": now.strftime("%Y-%m-%d %H:%M:%S"), "price": round(price, 2),
           "likely_low": round(lo1, 2), "likely_high": round(hi1, 2),
           "sure_low": round(lo2, 2), "sure_high": round(hi2, 2),
           **{INDICATOR_LOG[m]: v for m, v in votes.items()}}
    pd.DataFrame([row]).to_csv(TWO_MIN_LOG_PATH, mode="a", header=not os.path.exists(TWO_MIN_LOG_PATH),
                               index=False)
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
