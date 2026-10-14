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
# cash = دارایی غیرطلای کاربر؛ gold = واحد طلا؛ minutes = داده ۱ دقیقه‌ای زنده دارد (پیش‌بینی ۲ دقیقه)
MARKET = {"name": "والکس", "unit": "تتر", "digits": 0, "cash": "تتر", "cash_code": "USDT", "gold": "XAUT",
          "minutes": True}   # در حالت متاتریدر و بازار جهانی عوض می‌شود (use_metatrader، use_global)
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
    c = MARKET["cash_code"]
    print(f" Personal gold/{c} advisor ({SYMBOL}) - no orders are placed")
    print("=" * 62)
    if c == "USD":
        usdt = ask_number(" 1) How many US dollars (cash, not gold) do you hold? (e.g. 250) ")
        xaut = ask_number(" 2) How much gold do you hold, in troy ounces?\n"
                          "    (1 ounce = about 31.1 grams, e.g. 0.05) ")
    else:
        usdt = ask_number(" 1) How much USDT (Tether) do you hold? (1 USDT ~ 1 US dollar, e.g. 250) ")
        xaut = ask_number(" 2) How much gold do you hold, in XAUT, as shown in your Wallex wallet?\n"
                          "    (1 XAUT = 1 ounce = about 31.1 grams, e.g. 0.05) ")
    buy_price = None
    if xaut > 0:
        hint = f"; now about {market_price:,.0f}" if market_price else ""
        while True:
            buy_price = ask_number(f"    Average buy price, in {c} per ounce "
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


def use_metatrader():
    """منبع همه قیمت‌ها را از والکس به متاتریدر (mt_bridge.py) عوض می‌کند. برای سرور آلمان.
    کارمزد هر طرف از ~/.mt_fee (درصد، مثلاً 0.005) خوانده می‌شود؛ اگر نباشد صفر (حساب‌های بدون کمیسیون)."""
    global fetch_daily, fetch_book, fetch_minutes, FEE, MARKET
    import mt_bridge
    fetch_daily, fetch_book, fetch_minutes = mt_bridge.fetch_daily, mt_bridge.fetch_book, mt_bridge.fetch_minutes
    try:
        with open(os.path.expanduser("~/.mt_fee"), encoding="utf-8") as fh:
            FEE = float(fh.read().strip()) / 100
    except (FileNotFoundError, ValueError):
        FEE = 0.0
    MARKET = {"name": "متاتریدر", "unit": "دلار", "digits": 2,   # تکان ۲ دقیقه حدود ۱ دلار است؛ سنت لازم است
              "cash": "تتر", "cash_code": "USDT", "gold": "XAUT", "minutes": True}


def use_global():
    """منبع همه قیمت‌ها را از والکس به بازار جهانی (global_gold.py: یاهو + gold-api) عوض می‌کند.
    دارایی کاربر دلار و طلا (اونس) است؛ تتر و والکس هیچ جا نمی‌آید.
    کندل دقیقه‌ای را خود ربات جمع می‌کند (global_gold.start_collector)."""
    global fetch_daily, fetch_book, fetch_minutes, FEE, MARKET, SYMBOL
    import global_gold
    fetch_daily, fetch_book, fetch_minutes = global_gold.fetch_daily, global_gold.fetch_book, global_gold.fetch_minutes
    FEE = global_gold.fee()
    SYMBOL = "XAUUSD"
    MARKET = {"name": "بازار جهانی", "unit": "دلار", "digits": 2,
              "cash": "دلار", "cash_code": "USD", "gold": "اونس", "minutes": True}


def cash():
    return MARKET["cash"]


def per_gold():
    """«هر ۱ XAUT (هر اونس)» یا فقط «هر اونس»"""
    return "هر اونس" if MARKET["gold"] == "اونس" else f"هر ۱ {MARKET['gold']} (هر اونس)"


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
                         f"{pct(f['sell_cost'], False, 3)} است، یعنی حدود {cost_usdt:,.2f} {cash()}. "
                         "درباره زمان فروش پیشنهادی نمی‌دهیم.")
        else:
            lines.append(f"برای هدف «نقد کردن سریع»: طلایی برای فروش ندارید؛ دارایی شما همین الان {cash()} است.")
    lines.extend(signal_lines(f["signals"]))
    code += "|" + "|".join(f"{SIGNAL_NAMES[m][2]}:{'NA' if v is None else ('GOLD' if v else MARKET['cash_code'])}"
                           for m, v in f["signals"].items())
    return code, lines


def signal_lines(signals):
    rec = track_record()
    L = ["سیگنال سه روش تست‌شده برای امروز (فقط برای اطلاع؛ در تست هیچ‌کدام سود را از نگه داشتن "
         "بیشتر نکرد، ولی در دوره‌های ریزشی افت را کمتر کرد):"]
    for m, v in signals.items():
        state = "داده کافی نیست" if v is None else ("می‌گوید در طلا باشید" if v else f"می‌گوید در {cash()} باشید")
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
    c = MARKET["cash_code"]
    print(f" Sell price now (bid)     : {f['bid']:,.2f} {c}{note}")
    print(f" Buy price now (ask)      : {f['ask']:,.2f} {c}")
    print(f" Buy/sell gap             : {pct(f['spread'], False, 3)}")
    print(f" Cost to sell now         : {pct(f['sell_cost'], False, 3)} (fee + half the gap)")
    if answers["xaut"] > 0:
        print(f" Gold held                : {answers['xaut']:,.6g} {'oz' if c == 'USD' else 'XAUT'} "
              f"(about {grams(answers['xaut']):,.2f} grams)")
        print(f" Gold profit/loss if sold : {f['pnl']:+,.2f} {c} ({pct(f['pnl_pct'])})")
        print(f" Break-even sell price    : {f['breakeven_bid']:,.2f} {c}")
    print(f" Total value now          : {f['total_now']:,.2f} {c} "
          f"(gold share {f['gold_share'] * 100:.0f}%)")
    print(f" Volatility vs normal     : {f['vol_state']}"
          + (f" ({f['vol_ratio']:.2f}x)" if f["vol_ratio"] is not None else ""))
    print(f" Trend (200-day average)  : {f['trend']}"
          + (f" ({pct(f['vs_ma200'])} from average)" if f["vs_ma200"] is not None else ""))
    print(f" From 1-year high         : {pct(f['from_year_high'])}")
    print("-" * 62)
    print(" ALGORITHM OPINION (EXPERIMENTAL):")
    print(f"   {OPINION_EN[opinion[0].split('|')[0]]}")
    sig = "  ".join(f"{SIGNAL_NAMES[m][1]}={'n/a' if v is None else ('GOLD' if v else c)}"
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
    L.append(f"# گزارش مشاور طلا و {cash()} — {now:%Y-%m-%d %H:%M} (تهران)\n")
    L.append("> این برنامه هیچ خرید و فروشی انجام نمی‌دهد. فقط اطلاعات می‌دهد.\n")

    L.append("## جواب‌های شما\n")
    L.append(f"- {cash()} ({MARKET['cash_code']}): {answers['usdt']:,.2f}")
    L.append(f"- طلا: {answers['xaut']:,.6g} {MARKET['gold']} (حدود {grams(answers['xaut']):,.2f} گرم)"
             + (f" — قیمت خرید {per_gold()}: {answers['buy_price']:,.2f} {cash()}"
                if answers["xaut"] > 0 else ""))
    L.append(f"- هدف: {GOALS[answers['goal']][1]}\n")

    L.append("## بخش ۱: واقعیت‌ها (قابل اعتماد)\n")
    if not f["book_live"]:
        L.append("⚠️ قیمت زنده گرفته نشد؛ آخرین قیمت بسته‌شده روزانه استفاده شد.\n")
    L.append(f"- **قیمتی که الان می‌توانید بفروشید:** {f['bid']:,.2f} {cash()}")
    L.append(f"- **قیمتی که الان می‌توانید بخرید:** {f['ask']:,.2f} {cash()}")
    L.append(f"- **هزینه واقعی فروش همین الان:** {pct(f['sell_cost'], False, 3)} "
             f"(کارمزد {FEE * 100:.1f}٪ + نصف فاصله قیمت خرید و فروش که {pct(f['spread'], False, 3)} است)")
    if answers["xaut"] > 0 and price_looks_wrong(answers["buy_price"], f["mid"]):
        L.append("- ⚠️ **قیمت خرید واردشده خیلی با قیمت امروز فرق دارد.** "
                 "اگر قیمت هر گرم را زده‌اید، عددهای سود و زیان درست نیستند.")
    if answers["xaut"] > 0:
        word = "سود" if f["pnl"] >= 0 else "زیان"
        L.append(f"- **اگر همین الان همه طلا را بفروشید:** {f['gold_cash_now']:,.2f} {cash()} "
                 f"می‌گیرید. شما {f['gold_paid']:,.2f} {cash()} پرداخت کرده بودید، پس "
                 f"**{word} {abs(f['pnl']):,.2f} {cash()} ({pct(f['pnl_pct'])})**.")
        L.append(f"- **قیمت سر به سر:** اگر قیمت فروش به {f['breakeven_bid']:,.2f} {cash()} برسد، "
                 f"بعد از کارمزد نه سود کرده‌اید نه ضرر.")
    L.append(f"- **کل دارایی الان:** {f['total_now']:,.2f} {cash()} "
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
        L.append(f"دارایی شما الان حدود {fa(f['total_now'])} {cash()} می‌ارزد؛ "
                 f"{fa(f['gold_share'] * 100)}٪ آن طلاست.")
        if f["pnl"] >= 0:
            L.append(f"اگر همین الان طلا را بفروشید، {fa(f['pnl'])} {cash()} سود می‌کنید "
                     f"({fa(f['pnl_pct'] * 100, 1)}٪).")
            L.append(f"تا وقتی قیمت بالای {fa(f['breakeven_bid'])} {cash()} باشد، فروختن ضرر ندارد.")
        else:
            L.append(f"اگر همین الان طلا را بفروشید، {fa(f['pnl'])} {cash()} ضرر می‌کنید "
                     f"({fa(f['pnl_pct'] * 100, 1)}٪).")
            L.append(f"برای اینکه بی‌ضرر بفروشید، قیمت باید به {fa(f['breakeven_bid'])} {cash()} برسد.")
        if f["sell_cost"] > 0:
            L.append(f"کارمزد فروش همه طلا الان حدود {fa(xaut * f['mid'] * f['sell_cost'])} {cash()} است.")
        if price_looks_wrong(answers["buy_price"], f["mid"]):
            L.append("⚠️ قیمت خریدی که دادید خیلی با قیمت امروز فرق دارد؛ شاید قیمت هر گرم بوده.")
    else:
        L.append(f"دارایی شما {fa(f['total_now'])} {cash()} است و طلا ندارید.")

    L += ["", f"قیمت طلا الان {fa(f['mid'])} {cash()} است."]
    if MARKET["cash_code"] == "USD":
        L.append("(قیمت نقد جهانی طلا به دلار است؛ قیمت کارگزار یا فروشنده شما کمی با آن فرق دارد.)")
    if MARKET["unit"] != MARKET["cash"]:
        L.append(f"(قیمت‌ها از {MARKET['name']} و به دلار است؛ هر دلار تقریباً ۱ {cash()} حساب شده. "
                 f"کارمزد و فاصله خرید و فروش هم مال {MARKET['name']} است.)")
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
        advice = f"هدف شما نقد کردن است و دارایی شما همین الان {cash()} است."
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
    return "داده کافی نیست" if v is None else ("طلا 🟡" if v else f"{cash()} 💵")


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
    return f"{who} می‌گویند الان بهتر است پول در {cash()} باشد، نه طلا."
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
        L.append(f"قیمت طلا: {fa(price)} {MARKET['unit']}")
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
        L.append(f"قیمت طلا: {fa(price)} {MARKET['unit']}")
    L.append("")
    for m, old, new in changes:
        L.append(f"روش «{SIGNAL_PLAIN[m]}» تا حالا می‌گفت {'طلا' if old else cash()}، "
                 f"حالا می‌گوید {'طلا' if new else cash()}.")
    L += ["", f"جمع‌بندی: {signal_vote(signals)}", "", SIGNAL_REMINDER]
    return "\n".join(L)

# ---------------------------------------------------------------- پیش‌بینی حدودی (برای بله)

FORECAST_LOOKBACK = 84        # روز تقویمی ≈ ۶۰ روز کاری، همان که forecast_check.py تست کرده
FORECAST_HORIZONS = ((7, "تا یک هفته دیگر"), (30, "تا یک ماه دیگر"))
FORECAST_HIT = (69, 94)       # درصد درستی بازه‌ها در ۲۱ سال (خروجی forecast_check.py)
FORECAST_LOG_PATH = "forecast_log.csv"
SHADOW_LOG_PATH = "forecast_shadow_log.csv"   # مدل آزمایشی GARCH (forecast_models.py)


def price_range(daily, price, days):
    """بازه احتمالی قیمت بعد از days روز: (پایین، بالا) برای «به احتمال زیاد» و «تقریباً حتماً».
    مرکز بازه قیمت امروز است؛ جهت را حدس نمی‌زنیم، فقط اندازه تکان‌های اخیر را می‌گیریم."""
    sig = float(np.log(daily).diff().dropna().iloc[-FORECAST_LOOKBACK:].std())
    s = sig * np.sqrt(days)
    return (price * np.exp(-s), price * np.exp(s)), (price * np.exp(-2 * s), price * np.exp(2 * s))


def forecast_text(daily, price, ai=False):
    """پیام «پیش‌بینی حدودی» + ثبت در لاگ برای سنجش بعدی. ai=True: جمع‌بندی هوش مصنوعی هم (news_ai.py)."""
    if price is None:
        price = float(daily.iloc[-1])
    unit = MARKET["unit"]
    L = [f"🔮 قیمت طلا در آینده نزدیک — {jalali_now()}", f"قیمت الان: {fa(price)} {unit}", "",
         "هیچ‌کس نمی‌داند طلا بالا می‌رود یا پایین. ولی می‌دانیم معمولاً چقدر تکان می‌خورد:", ""]
    rows, base_ranges = [], []
    now = pd.Timestamp.now(tz="Asia/Tehran").strftime("%Y-%m-%d %H:%M")
    for days, label in FORECAST_HORIZONS:
        (lo1, hi1), (lo2, hi2) = price_range(daily, price, days)
        L.append(f"• {label.replace('تا ', '')}: به احتمال زیاد بین {fa(lo1)} و {fa(hi1)} {unit}")
        base_ranges.append((days, label.replace('تا ', ''), lo1, hi1))
        rows.append({"made_at": now, "price": round(price, 2), "days": days,
                     "likely_low": round(lo1, 2), "likely_high": round(hi1, 2),
                     "sure_low": round(lo2, 2), "sure_high": round(hi2, 2)})
    L += ["", "«به احتمال زیاد» یعنی از هر ۳ بار، ۲ بار. در ۲۱ سال گذشته همین‌قدر درست بوده.",
          "گاهی هم حرکت بزرگ‌تری پیش می‌آید، ولی کم."]
    pd.DataFrame(rows).to_csv(FORECAST_LOG_PATH, mode="a", header=not os.path.exists(FORECAST_LOG_PATH),
                              index=False)
    trial = shadow_forecast(daily, price, now)
    if trial:
        L += ["", "🧪 آزمایشی — روش دوم (GARCH):"]
        for days, label in FORECAST_HORIZONS:
            lo, hi = trial[days]
            L.append(f"• {label.replace('تا ', '')}: به احتمال زیاد بین {fa(lo)} و {fa(hi)} {unit}")
        L += ["این روش زودتر می‌فهمد بازار آرام شده یا شلوغ. در ۲۰ سال خطایش کمی کمتر بود،",
              "ولی وقت‌های آرام بازه را بیش از لازم پهن می‌گرفت. هنوز ثابت نشده؛ فقط برای مقایسه است."]
    if ai:   # نظر هوش مصنوعی (news_ai.py)؛ آزمایشی. اگر کلید نباشد یا خطا بدهد، پیام بدون آن می‌رود
        try:
            import news_ai
            L += news_ai.forecast_section(price, base_ranges, now, fa, "\n".join(L))
        except Exception as e:
            print(f" AI section skipped: {e}")
    return "\n".join(L)


def shadow_forecast(daily, price, now):
    """بازه مدل آزمایشی GARCH(1,1): ثبت در لاگ و برگرداندن {روز: (پایین، بالا)} برای «به احتمال زیاد».
    در forecast_models.py خطای کمتری از روش فعلی داشت ولی طبق قانون برد قبول نشد؛
    این‌جا روی داده واقعی والکس سنجیده می‌شود. اگر خطا داد None برمی‌گرداند و ربات کارش را ادامه می‌دهد."""
    try:
        from forecast_models import garch_filter, garch_fit, garch_h
        r = np.log(daily).diff().dropna().values
        (a, b), v_long = garch_fit(r)
        v1 = garch_filter(r, a, b, v_long)[-1]
        rows = []
        for days, _ in FORECAST_HORIZONS:
            s = float(np.sqrt(garch_h(v1, a, b, v_long, days)))
            rows.append({"made_at": now, "price": round(price, 2), "days": days, "model": "GARCH",
                         "likely_low": round(price * np.exp(-s), 2), "likely_high": round(price * np.exp(s), 2),
                         "sure_low": round(price * np.exp(-2 * s), 2), "sure_high": round(price * np.exp(2 * s), 2)})
        pd.DataFrame(rows).to_csv(SHADOW_LOG_PATH, mode="a", header=not os.path.exists(SHADOW_LOG_PATH),
                                  index=False)
        return {x["days"]: (x["likely_low"], x["likely_high"]) for x in rows}
    except Exception as e:
        print(f" Trial forecast skipped: {e}")
        return None


# ---------------------------------------------------------------- پیش‌بینی ۲ دقیقه (برای بله)
#
# روش (از گزارش تحقیقاتی؛ جهت قابل پیش‌بینی نیست، اندازه تکان هست):
#   - نوسان کندل بعدی با EWMA (آزمایش‌شده در backtest_2min.py)، دور آخرین قیمت معامله
#   - بازه ۸۰٪ و ۹۵٪ از خطاهای واقعی گذشته، نه فرض توزیع نرمال (forecast_models.two_min_abs_z):
#     تا وقتی روی والکس کمتر از TWO_MIN_SELF_CALIB پیش‌بینی سنجیده شده، از طلای جهانی؛ بعد از آن از خود والکس
#   - مقایسه با هزینه رفت و برگشت (کارمزد + فاصله قیمت خرید و فروش)
#   - کنترل سلامت داده: اگر والکس کم‌معامله است، هشدار
#   - کارنامه زنده: هر پیش‌بینی بعداً با قیمت واقعی والکس سنجیده می‌شود
#   - روش آزمایشی: همان، با اثر ساعت روز (forecast_models.py: قبول نشد، آماره ‎-1.16)

TWO_MIN_LOG_PATH = "forecast_2min_v2.csv"          # هر پیش‌بینی (نسخه ۲: بازه ۸۰٪/۹۵٪)
TWO_MIN_SCORE_PATH = "forecast_2min_scores.csv"    # سنجش هر پیش‌بینی با قیمت واقعی ۲ دقیقه بعد
TWO_MIN_LEVELS = (0.80, 0.95)
TWO_MIN_SELF_CALIB = 200          # از این تعداد سنجش به بعد، بازه از خطاهای خود والکس ساخته می‌شود
TWO_MIN_SELF_WINDOW = 500         # آخرین چند سنجش والکس برای ساختن بازه
TWO_MIN_SCORECARD_MIN = 20        # کمتر از این، کارنامه هنوز معنایی ندارد
STALE_MINUTES = 6                 # آخرین کندل قدیمی‌تر از این = داده کهنه
MIN_FILL = 0.5                    # در یک ساعت اخیر، حداقل نیمی از کندل‌های ۲ دقیقه‌ای معامله داشته باشند
INDICATOR_HIT = {"RSI(14)": 51.3, "MACD(12,26,9)": 50.5, "Bollinger(20,2)": 50.6,
                 "SMA 9/21 cross": 50.5, "Momentum(1)": 49.5}   # درصد درستی جهت در backtest_2min.py
INDICATOR_LOG = {"RSI(14)": "rsi", "MACD(12,26,9)": "macd", "Bollinger(20,2)": "bollinger",
                 "SMA 9/21 cross": "sma_9_21", "Momentum(1)": "momentum"}
_CACHE = {}                       # چیزهایی که یک بار از data/GC_F_2m.csv ساخته می‌شوند


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
    bars.index.name = "time"                   # زمان شروع کندل به وقت UTC
    return bars.reset_index()


def _cached(name, make):
    if name not in _CACHE:
        _CACHE[name] = make()
    return _CACHE[name]


def data_health(bars, now_utc):
    """(سالم است؟، توضیح فارسی اگر نیست). کندل‌های بی‌معامله در fetch_minutes حذف شده‌اند."""
    t = pd.DatetimeIndex(bars.time)
    age = (now_utc - (t[-1] + pd.Timedelta(minutes=2))).total_seconds() / 60
    recent = bars[t >= t[-1] - pd.Timedelta(minutes=58)]
    fill = len(recent) / 30
    flat = float((recent.close.diff().iloc[1:] == 0).mean()) if len(recent) > 1 else 1.0
    if age > STALE_MINUTES:
        return False, (f"آخرین قیمت طلا در {MARKET['name']} حدود {fa(age)} دقیقه پیش بوده؛ "
                       "بازار الان تقریباً خواب است (یا بسته است).")
    if fill < MIN_FILL or flat > 0.5:
        return False, f"در یک ساعت اخیر معامله طلا در {MARKET['name']} کم بوده؛ قیمت‌ها تکه‌تکه‌اند."
    return True, ""


def append_csv(path, rows):
    """سطرها را به ته فایل CSV اضافه می‌کند و ستون‌ها را با سرستون فایل جور می‌کند.
    اگر ستون تازه‌ای آمده (مثلاً ستون‌های هوش مصنوعی)، فایل یک بار با سرستون کامل‌تر بازنویسی می‌شود."""
    new = pd.DataFrame(rows)
    if not os.path.exists(path):
        new.to_csv(path, index=False)
        return
    header = list(pd.read_csv(path, nrows=0).columns)
    extra = [c for c in new.columns if c not in header]
    if extra:
        pd.concat([pd.read_csv(path), new], ignore_index=True).reindex(columns=header + extra) \
            .to_csv(path, index=False)
    else:
        new.reindex(columns=header).to_csv(path, mode="a", header=False, index=False)


SCORE_COLUMNS = ["made_at", "actual", "z", "data_ok", "in_80", "in_95", "trial_in_80", "ai_in_80", "ai_dir_hit"]


def score_past(bars):
    """پیش‌بینی‌های قبلی را که ۲ دقیقه‌شان گذشته و قیمتشان در همین داده هست، با قیمت واقعی می‌سنجد."""
    if not os.path.exists(TWO_MIN_LOG_PATH):
        return
    log = pd.read_csv(TWO_MIN_LOG_PATH)
    done = set(pd.read_csv(TWO_MIN_SCORE_PATH).made_at) if os.path.exists(TWO_MIN_SCORE_PATH) else set()
    ends = pd.DatetimeIndex(bars.time) + pd.Timedelta(minutes=2)
    rows = []
    for _, x in log[~log.made_at.isin(done)].iterrows():
        target = pd.Timestamp(x.target_utc)
        if target > ends[-1] or target < ends[0]:
            continue                            # هنوز نرسیده، یا قدیمی‌تر از داده دریافتی
        actual = float(bars.close[ends <= target].iloc[-1])   # آخرین قیمت معامله تا آن لحظه
        z = np.log(actual / x.price) / x.sigma
        row = {"made_at": x.made_at, "actual": actual, "z": round(z, 4), "data_ok": x.data_ok,
               "in_80": x.low_80 <= actual <= x.high_80, "in_95": x.low_95 <= actual <= x.high_95}
        if pd.notna(x.get("trial_low_80")):
            row["trial_in_80"] = x.trial_low_80 <= actual <= x.trial_high_80
        if pd.notna(x.get("ai_low_80")):
            row["ai_in_80"] = x.ai_low_80 <= actual <= x.ai_high_80
            if x.ai_tone != 0 and actual != x.price:          # فقط وقتی لحن جهت داشت و قیمت تکان خورد
                row["ai_dir_hit"] = (x.ai_tone > 0) == (actual > x.price)
        rows.append(row)
    if rows:
        append_csv(TWO_MIN_SCORE_PATH, rows)


def two_min_scores():
    if not os.path.exists(TWO_MIN_SCORE_PATH):
        return pd.DataFrame(columns=SCORE_COLUMNS)
    return pd.read_csv(TWO_MIN_SCORE_PATH).reindex(columns=SCORE_COLUMNS)


def two_min_quantiles(scores):
    """چندک‌های بازه ۸۰٪ و ۹۵٪ و منبعشان. فقط از پیش‌بینی‌های گذشته (بدون نگاه به آینده)."""
    own = scores[scores.data_ok.astype(bool)].z.dropna().abs() if len(scores) else pd.Series(dtype=float)
    if len(own) >= TWO_MIN_SELF_CALIB:
        absz, source = np.sort(own.values[-TWO_MIN_SELF_WINDOW:]), "own"
    else:
        from forecast_models import two_min_abs_z
        absz, source = _cached("absz", two_min_abs_z), "global"
    return [float(np.quantile(absz, p)) for p in TWO_MIN_LEVELS], absz, source


TRADE_AMOUNTS = (100, 200, 300)   # اندازه معامله نمونه در «پیش‌بینی ۲ دقیقه» (دلار)


def trade_targets(book, amounts=TRADE_AMOUNTS, profit=1.0):
    """اگر الان با هر مبلغ معامله باز شود، قیمت بستن باید به کجا برسد که با کارمزد و فاصله خرید و فروش صفر نشود.
    خرید: باز با ask، بستن با bid آینده. فروش (شرط روی پایین آمدن): باز با bid، بستن با ask آینده.
    کارمزد FEE هر طرف درصدی از ارزش معامله است (مثل کمیسیون متاتریدر)."""
    ask, bid = book["ask"], book["bid"]
    rows = []
    for a in amounts:
        oz = a / ask
        rows.append({"amount": a,
                     "cost": a - oz * bid * (1 - FEE) + a * FEE,       # اگر همین الان باز و بسته شود
                     "buy_even": ask * (1 + FEE) / (1 - FEE),
                     "buy_profit": ask * (1 + FEE + profit / a) / (1 - FEE),
                     "sell_even": bid * (1 - FEE) / (1 + FEE),
                     "sell_profit": bid * (1 - FEE - profit / a) / (1 + FEE)})
    return rows


def trade_target_lines(book, price=None, sigma=0.0, absz=None):
    """بخش سریع پیام ۲ دقیقه: «بخر روی… بی‌ضرر روی…» برای خرید و فروش، و در بازار دلاری قیمت ۱ دلار سود با
    ۱۰۰/۲۰۰/۳۰۰ دلار. کوتاه، چون موقع معامله تمرکز کم است. شانس رسیدن تا ۲ دقیقه از همان خطاهای واقعی بازه."""
    if not book:
        return []
    fp = lambda x: fa(x, MARKET["digits"])
    price = (book["bid"] + book["ask"]) / 2 if price is None else price

    def chance(target):
        if absz is None or sigma <= 0:
            return None
        return float(np.mean(absz > abs(np.log(target / price)) / sigma)) / 2

    def pct(c):
        return "تقریباً صفر" if c < 0.001 else f"{fa(c * 100)}٪"

    rows = trade_targets(book)
    dollars = MARKET["unit"] == "دلار"
    L = []
    for icon, title, open_word, open_px, even_key, profit_key in (
            ("📈", "اگر فکر می‌کنید بالا می‌رود:", "بخر", book["ask"], "buy_even", "buy_profit"),
            ("📉", "اگر فکر می‌کنید پایین می‌آید:", "بفروش", book["bid"], "sell_even", "sell_profit")):
        even = rows[0][even_key]
        c = chance(even)
        L += ["", f"{icon} {title}",
              f"{open_word}: {fp(open_px)}",
              f"بی‌ضرر: {fp(even)}" + ("" if c is None else f" (شانس {pct(c)})")]
        if dollars:
            L.append("۱ دلار سود:")
            L += [f"• {fa(r['amount'])} دلار ← {fp(r[profit_key])}" for r in rows]
            cs = [chance(r[profit_key]) for r in rows]
            if cs[-1] is not None and max(cs) < 0.01:
                L.append("(در ۲ دقیقه تقریباً نمی‌رسد)")
    return L


def two_min_text(bars, book=None, now=None, ai=False):
    """پیام «پیش‌بینی ۲ دقیقه». book = {'bid','ask'} از fetch_book یا None. now فقط برای آزمایش.
    ai=True: نظر هوش مصنوعی هم گرفته، بازه «با اثر اخبار» ساخته، و در کارنامه زنده سنجیده می‌شود."""
    from backtest_2min import signals   # همان کدی که آزمایش شده
    from forecast_models import hour_factors, two_min_sigma, two_min_sigma_hour
    now = pd.Timestamp.now(tz="Asia/Tehran") if now is None else now.tz_convert("Asia/Tehran")
    fp = lambda x: fa(x, MARKET["digits"])          # قیمت با دقت بازار
    now_utc = now.tz_convert("UTC")
    try:
        score_past(bars)
    except Exception as e:
        print(f" 2-minute scoring skipped: {e}")
    scores = two_min_scores()
    (q80, q95), absz, source = two_min_quantiles(scores)
    price = float(bars.close.iloc[-1])                     # آخرین قیمت معامله
    sigma = two_min_sigma(bars.close.values, bars.time.values)
    band = lambda q, s=sigma: (price * np.exp(-q * s), price * np.exp(q * s))
    (lo80, hi80), (lo95, hi95) = band(q80), band(q95)
    ok, why = data_health(bars, now_utc)

    last = signals(bars).iloc[-1]
    votes = {m: int(v) for m, v in last.items()}
    up, down = sum(v > 0 for v in votes.values()), sum(v < 0 for v in votes.values())

    # بخش سریع: موقع معامله تمرکز کم است؛ فقط عددهای لازم، یک چیز در هر خط
    L = []
    if not ok:
        L += [f"⚠️ داده مشکل دارد — به عددها تکیه نکنید ({why})", ""]
    L += [f"⏱ ساعت {fa(now.hour)}:{fa(now.minute).rjust(2, '۰')} | قیمت الان: {fp(price)}"]
    L += trade_target_lines(book, price, sigma, absz)
    L += ["", f"🔮 ۲ دقیقه دیگر (۸۰٪): {fp(lo80)} تا {fp(hi80)}",
          "🎲 بالا یا پایین؟ هیچ روشی نمی‌داند (۵۰/۵۰)"]

    # جزئیات: برای وقتی که عجله ندارید
    said = (f"{fa(up)} تا از ۵ ابزار تریدرها می‌گویند بالا" if up > down else
            f"{fa(down)} تا از ۵ ابزار تریدرها می‌گویند پایین" if down > up else
            "ابزارهای تریدرها با هم موافق نیستند")
    fee = "صفر" if FEE == 0 else f"{fa(FEE * 100, 3)}٪"
    L += ["", "━━━━━━━━━━", "جزئیات (اگر وقت دارید):",
          f"• بازار: {MARKET['name']}، واحد: {MARKET['unit']}، کارمزد هر طرف: {fee}"
          + ("؛ مبلغ‌ها = کل ارزش معامله، بدون اهرم" if MARKET["unit"] == "دلار" else ""),
          f"• ۲ دقیقه دیگر (۹۵٪): {fp(lo95)} تا {fp(hi95)}",
          f"• این بازه‌ها از خطاهای واقعی خود ربات روی {MARKET['name']} ساخته شده‌اند." if source == "own" else
          "• این بازه‌ها از رفتار واقعی قیمت جهانی طلا ساخته شده‌اند، نه از فرمول.",
          f"• {said}؛ در ۱۷ هزار آزمایش نصف دفعه‌ها درست بودند (شیر یا خط)."]

    row = {"made_at": now.strftime("%Y-%m-%d %H:%M:%S"),
           "target_utc": (now_utc + pd.Timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S+00:00"),
           "price": round(price, 2), "sigma": round(sigma, 7), "calib": source, "data_ok": ok,
           "low_80": round(lo80, 2), "high_80": round(hi80, 2), "low_95": round(lo95, 2), "high_95": round(hi95, 2),
           "bid": book["bid"] if book else None, "ask": book["ask"] if book else None}

    try:   # روش آزمایشی؛ اگر خطا داد، پیام بدون آن فرستاده می‌شود
        fac = _cached("hours", hour_factors)
        s_h = two_min_sigma_hour(bars.close.values, bars.time.values, fac)
        (tlo, thi), (tlo95, thi95) = band(q80, s_h), band(q95, s_h)
        nxt_hour = (pd.Timestamp(bars.time.iloc[-1]) + pd.Timedelta(minutes=2)).hour
        busy = fac[nxt_hour] > 1.2
        row.update(trial_low_80=round(tlo, 2), trial_high_80=round(thi, 2),
                   trial_low_95=round(tlo95, 2), trial_high_95=round(thi95, 2), hour_factor=round(fac[nxt_hour], 2))
        L.append(f"• 🧪 آزمایشی با حساب ساعت روز (۸۰٪): {fp(tlo)} تا {fp(thi)}"
                 + ("؛ الان معمولاً ساعت پرتکانی است." if busy else ""))
    except Exception as e:
        print(f" 2-minute trial skipped: {e}")

    # کارنامه زنده روی والکس
    sc = scores[scores.data_ok.astype(bool)] if len(scores) else scores
    if len(sc) >= TWO_MIN_SCORECARD_MIN:
        line = (f"• 📋 کارنامه روی {MARKET['name']}: از {fa(len(sc))} پیش‌بینی قبلی، "
                f"{fa(sc.in_80.astype(bool).mean() * 100)}٪ داخل بازه ۸۰٪ بود "
                f"و {fa(sc.in_95.astype(bool).mean() * 100)}٪ داخل بازه ۹۵٪")
        tr = sc.trial_in_80.dropna() if "trial_in_80" in sc else []
        if len(tr) >= TWO_MIN_SCORECARD_MIN:
            line += f" (روش آزمایشی: {fa(tr.astype(bool).mean() * 100)}٪ داخل ۸۰٪)"
        L.append(line + ".")
    else:
        L.append(f"• 📋 کارنامه روی {MARKET['name']}: هنوز {fa(len(sc))} پیش‌بینی سنجیده شده؛ "
                 f"از {fa(TWO_MIN_SCORECARD_MIN)} به بعد درصد درستی این‌جا می‌آید.")

    if ai:
        L += two_min_ai(price, (lo80, hi80), (lo95, hi95), row, "\n".join(L), sc)

    row.update({INDICATOR_LOG[m]: v for m, v in votes.items()})
    append_csv(TWO_MIN_LOG_PATH, [row])
    return "\n".join(L)


def two_min_ai(price, band80, band95, row, report, sc):
    """بخش هوش مصنوعی پیش‌بینی ۲ دقیقه: بازه با اثر اخبار (قانون ثابت news_ai.ADJUST)، ثبت در row برای سنجش،
    و کارنامه زنده خودش. اگر کلید نباشد [] و اگر جواب نیاید پیام کوتاه."""
    try:
        import news_ai
        if not news_ai.configured():
            return []
        view = news_ai.advise("2min", report, price)
        if view is None:
            return news_ai.UNAVAILABLE
        alo80, ahi80 = news_ai.adjust(price, *band80, view)
        alo95, ahi95 = news_ai.adjust(price, *band95, view)
        row.update(ai_tone=view["tone"], ai_confidence=view["confidence"], ai_risk=view["risk"],
                   ai_low_80=round(alo80, 2), ai_high_80=round(ahi80, 2),
                   ai_low_95=round(alo95, 2), ai_high_95=round(ahi95, 2))
        fp = lambda x: fa(x, MARKET["digits"])
        L = news_ai.head_lines(view, fa) + [f"با اثر اخبار، با احتمال ۸۰٪ بین {fp(alo80)} و {fp(ahi80)}"]
        L += news_ai.tail_lines(view)
        ai80 = sc.ai_in_80.dropna() if "ai_in_80" in sc else []
        if len(ai80) >= TWO_MIN_SCORECARD_MIN:
            base80 = sc.loc[ai80.index, "in_80"].astype(bool).mean() * 100
            L.append(f"📋 کارنامه هوش مصنوعی: در {fa(len(ai80))} بار، بازه‌اش {fa(ai80.astype(bool).mean() * 100)}٪ "
                     f"درست بود (بازه خود ربات در همان دفعه‌ها {fa(base80)}٪).")
            hit = sc.ai_dir_hit.dropna() if "ai_dir_hit" in sc else []
            if len(hit) >= TWO_MIN_SCORECARD_MIN:
                L.append(f"وقتی لحن اخبار جهت داشت، {fa(hit.astype(bool).mean() * 100)}٪ مواقع جهت ۲ دقیقه بعد "
                         f"را درست گفت (شیر یا خط = ۵۰٪؛ {fa(len(hit))} بار).")
        else:
            L.append(f"📋 کارنامه هوش مصنوعی: هنوز {fa(len(ai80))} بار سنجیده شده؛ "
                     f"از {fa(TWO_MIN_SCORECARD_MIN)} بار به بعد درصد درستی‌اش این‌جا می‌آید.")
        return L
    except Exception as e:
        print(f" 2-minute AI skipped: {e}")
        return []


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
    import sys
    if "--global" in sys.argv[1:]:
        use_global()
    book = fetch_book()
    answers = ask_questions(None if book is None else (book["bid"] + book["ask"]) / 2)
    print(f"\n Fetching live data ({SYMBOL}, about 30 seconds)...")
    daily = fetch_daily()
    book = fetch_book()
    facts = compute_facts(answers, daily, book)
    opinion = algorithm_opinion(answers, facts)
    print_summary(answers, facts, opinion)
    write_report(answers, facts, opinion)
    write_log(answers, facts, opinion)


if __name__ == "__main__":
    main()
