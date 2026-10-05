#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_ai.py - گرفتن نظر هوش مصنوعی (جیمینای) و یکی کردنش با گزارش خود ربات

هر بار که کسی در ربات «پیش‌بینی ۲ دقیقه»، «پیش‌بینی حدودی»، «گزارش» یا «سیگنال امروز» را می‌زند:
  1. ربات اول عددهای خودش را حساب می‌کند (همان روش‌های تست‌شده).
  2. همان گزارش + خلاصه نتیجه تست‌های ما یک بار برای جیمینای فرستاده می‌شود.
  3. جیمینای با جستجوی گوگل اخبار و تقویم اقتصادی را می‌خواند و یک جواب کوتاه و محدود می‌دهد:
       tone           لحن اخبار برای طلا، از -2 (به ضرر) تا +2 (به نفع)
       confidence     اطمینان، 0 تا 1
       risk           low / normal / high: احتمال تکان غیرعادی (مثلاً آمار مهم آمریکا در راه است)
       events         رویدادهای مهم پیش رو
       summary_fa     خلاصه اخبار به فارسی ساده
       suggestion_fa  پیشنهاد جمع‌بندی‌شده: گزارش ربات + اخبار، برای همین بخش
  4. ربات بخش «🤖 جمع‌بندی با هوش مصنوعی (آزمایشی)» را زیر گزارش خودش می‌گذارد. در پیش‌بینی‌ها
     بازه «با اثر اخبار» هم با یک قانون ثابت (ADJUST) ساخته می‌شود؛ هوش مصنوعی خودش عدد قیمت نمی‌سازد.

⚠️ آزمایشی است. تست‌های ما نشان داد جهت قیمت (مخصوصاً در ۲ دقیقه) قابل پیش‌بینی نیست. برای همین:
  - عددهای اصلی ربات عوض نمی‌شوند؛ نظر هوش مصنوعی جدا و با برچسب نشان داده می‌شود.
  - هر جواب در ai_log.csv و هر بازه در لاگ پیش‌بینی ثبت و بعداً با قیمت واقعی سنجیده می‌شود
    (۲ دقیقه: خودکار، در همان کارنامه زنده).
  - اگر جیمینای جواب نداد، ربات بدون این بخش کار می‌کند.

تنظیمات (روی سرور، هرگز در کد یا GitHub):
  ~/.gemini_key        کلید جیمینای (لازم)
  ~/.gemini_base_url   فقط روی سرور ایران (آدرس تونل). روی سرور آلمان نباید باشد.
  ~/.gemini_model      اختیاری؛ اسم مدل (پیش‌فرض DEFAULT_MODEL)

امتحان در ترمینال:
  python3 news_ai.py
"""
import json
import os
import re
import time

import numpy as np
import pandas as pd
import requests

KEY_PATH = os.path.expanduser("~/.gemini_key")
BASE_URL_PATH = os.path.expanduser("~/.gemini_base_url")
MODEL_PATH = os.path.expanduser("~/.gemini_model")
DEFAULT_BASE = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-2.5-flash"
AI_LOG_PATH = "ai_log.csv"                         # هر جواب جیمینای
NEWS_FORECAST_LOG_PATH = "news_forecast_log.csv"   # هر بازه «با اثر اخبار» پیش‌بینی حدودی
REUSE_SECONDS = 30            # دو بار زدن پشت سر هم یک دکمه = همان جواب (هزینه دوبار نشود)
TIMEOUTS = {"2min": 30, "forecast": 60, "report": 60, "signal": 60}
RETRY_AFTER = 2 * 60          # بعد از خطا، تا ۲ دقیقه دوباره امتحان نشود تا ربات معطل نماند
_LAST = {}                    # بخش -> (زمان، جواب)
_FAILED_AT = [0.0]

# قانون اثر اخبار روی بازه‌ها — از قبل و بدون نگاه به داده انتخاب شده؛ تست نشده است.
#   جابه‌جایی مرکز بازه = SHIFT × (tone / 2) × confidence × پهنای بازه
#   یعنی حداکثر یک چهارم پهنا، و فقط وقتی جیمینای کاملاً مطمئن است.
#   پهنا: اگر خبر مهمی نزدیک است پهن‌تر، اگر خبری نیست کمی باریک‌تر.
ADJUST = {"SHIFT": 0.25, "WIDTH": {"low": 0.9, "normal": 1.0, "high": 1.3}}

KINDS = {
    "2min": ("a forecast of the gold price range 2 minutes from now. The user mostly uses this for very "
             "short-term decisions",
             "chance of an unusually big move in the NEXT FEW MINUTES (e.g. a US data release or a speech "
             "within the hour, or breaking news right now)"),
    "forecast": ("a forecast of the gold price range 1 week and 1 month from now",
                 "chance of an unusually big move in the coming days because of scheduled events or breaking news"),
    "report": ("a personal report on the user's holdings (USDT and gold), profit/loss and goal",
               "chance of an unusually big move in the coming days"),
    "signal": ("today's long-term trend signals from three tested methods",
               "chance of an unusually big move in the coming days"),
}

EVIDENCE = """What the bot's own tests found (real data, costs included; keep your suggestion consistent with this):
- 2 minutes: RSI, MACD, Bollinger, moving-average cross and momentum guessed the next 2-minute direction right
  only 49.5-51.3% of the time on ~17,000 real gold candles (a coin flip). A typical 2-minute move is ~0.03%.
- The bot's price ranges are calibrated: about 80% / 95% of real prices fell inside the 80% / 95% ranges.
- Over 20 years, no timing method (200-day average, 12-month momentum, 50/200 cross) beat simply holding gold
  after costs; they only reduced losses in the 2013-2019 bear market.
- Nobody has shown that news tone predicts direction; that is exactly what is being tested now."""

PROMPT = """You are a careful assistant to a personal gold advisor bot. The bot never trades; the user decides.
The bot just produced {what}. Its report (Persian) is below, made from live MetaTrader prices (XAU/USD).

--- BOT REPORT ---
{report}
--- END ---

{evidence}

Your task:
1. Use Google Search for the most important news of the last 48 hours that can move gold in US dollars
   (Fed and interest rates, US dollar, inflation, jobs, geopolitics, central-bank buying, ETF flows),
   and the economic calendar for the next 7 days. For a 2-minute forecast also look for anything in the last
   hour or scheduled in the next hour.
2. Judge the overall TONE of the news for gold (not your own price prediction). Neutral when mixed.
3. Write suggestion_fa: 2-4 short, plain Persian sentences for a non-expert that combine the bot's numbers
   with the news for THIS section. Use only numbers that appear in the report; do not invent prices.
   Be honest about uncertainty, never promise profit, and stay consistent with the tested evidence above.

Reply with ONLY one JSON object, no other text:
{{"tone": <integer -2..2>,
 "confidence": <number 0..1>,
 "risk": "<low|normal|high: {risk}>",
 "events": ["<upcoming scheduled event with date/time UTC, max 4>"],
 "headlines": [{{"title": "<short English headline>", "effect": "<up|down|mixed>"}}],
 "summary_fa": "<1-2 plain Persian sentences: what the news says>",
 "suggestion_fa": "<2-4 plain Persian sentences>"}}
At most 5 headlines. Now (UTC): {now}."""


# ---------------------------------------------------------------- تنظیمات

def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except FileNotFoundError:
        return None


def configured():
    """آیا کلید جیمینای روی این سرور هست؟"""
    return _read(KEY_PATH) is not None


def model_name():
    return _read(MODEL_PATH) or DEFAULT_MODEL


# ---------------------------------------------------------------- گرفتن جواب از جیمینای

def ask_gemini(prompt, timeout=60, use_search=True):
    """متن جواب جیمینای. کلید فقط در سربرگ درخواست است، نه در آدرس؛ پس در پیام خطا نمی‌آید."""
    key = _read(KEY_PATH)
    if not key:
        raise RuntimeError(f"No Gemini key in {KEY_PATH}")
    base = (_read(BASE_URL_PATH) or DEFAULT_BASE).rstrip("/")
    model = model_name()
    gen = {"temperature": 0.2}
    if model.startswith("gemini-2.5-flash"):
        gen["thinkingConfig"] = {"thinkingBudget": 0}     # جواب سریع‌تر؛ برای ۲ دقیقه مهم است
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}
    if use_search:
        body["tools"] = [{"google_search": {}}]
    r = requests.post(f"{base}/v1beta/models/{model}:generateContent", json=body, timeout=timeout,
                      headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        d = r.json()
    except ValueError:
        raise RuntimeError(f"Gemini HTTP {r.status_code}: not JSON") from None
    if r.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {r.status_code}: {(d.get('error') or {}).get('message', '')[:200]}")
    parts = (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError("Gemini returned an empty answer")
    return text


def _as_list(x):
    return x if isinstance(x, list) else []


def parse_answer(text):
    """جواب جیمینای را می‌خواند و همه چیز را در محدوده مجاز نگه می‌دارد.
    متن اخبار ممکن است حرف عجیبی به جیمینای زده باشد؛ برای همین عددها محدود و متن‌ها کوتاه می‌شوند."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    d = json.loads(m.group(0))
    tone = float(d.get("tone", 0))
    conf = float(d.get("confidence", 0))
    risk = str(d.get("risk", "normal")).strip().lower()
    out = {"tone": max(-2, min(2, int(round(tone)))) if np.isfinite(tone) else 0,
           "confidence": max(0.0, min(1.0, conf)) if np.isfinite(conf) else 0.0,
           "risk": risk if risk in ADJUST["WIDTH"] else "normal",
           "events": [str(x)[:120] for x in _as_list(d.get("events"))][:4],
           "headlines": [],
           "summary_fa": str(d.get("summary_fa", ""))[:500],
           "suggestion_fa": str(d.get("suggestion_fa", ""))[:900]}
    for h in _as_list(d.get("headlines"))[:5]:
        if isinstance(h, dict) and h.get("title"):
            eff = str(h.get("effect", "mixed")).lower()
            out["headlines"].append({"title": str(h["title"])[:160],
                                     "effect": eff if eff in ("up", "down", "mixed") else "mixed"})
    return out


def advise(kind, report, price=None):
    """نظر هوش مصنوعی برای یک بخش ربات (kind یکی از KINDS). report = متن گزارش خود ربات.
    هر درخواست یک بار جیمینای را صدا می‌زند (فقط دو بار زدن پشت سر هم در REUSE_SECONDS یک جواب می‌گیرد).
    اگر کلید نباشد یا خطا بدهد None."""
    if not configured():
        return None
    last = _LAST.get(kind)
    if last and time.time() - last[0] < REUSE_SECONDS:
        return last[1]
    if time.time() - _FAILED_AT[0] < RETRY_AFTER:
        return None
    what, risk = KINDS[kind]
    prompt = PROMPT.format(what=what, report=report[:6000], evidence=EVIDENCE, risk=risk,
                           now=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M"))
    started = time.time()
    try:
        view = parse_answer(ask_gemini(prompt, timeout=TIMEOUTS.get(kind, 60)))
    except Exception as e:
        _FAILED_AT[0] = time.time()
        print(f" AI advice skipped ({kind}): {e}")
        return None
    view["model"] = model_name()
    view["seconds"] = round(time.time() - started, 1)
    _LAST[kind] = (time.time(), view)
    row = {"made_at": pd.Timestamp.now(tz="Asia/Tehran").strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
           "price": None if price is None else round(price, 2), "model": view["model"],
           "seconds": view["seconds"], "tone": view["tone"], "confidence": view["confidence"],
           "risk": view["risk"], "events": " | ".join(view["events"]),
           "headlines": " | ".join(f"{h['effect']}: {h['title']}" for h in view["headlines"]),
           "suggestion": view["suggestion_fa"].replace("\n", " ")}
    pd.DataFrame([row]).to_csv(AI_LOG_PATH, mode="a", header=not os.path.exists(AI_LOG_PATH), index=False)
    return view


# ---------------------------------------------------------------- اثر روی بازه و متن پیام

def adjust(price, low, high, view):
    """بازه (low, high) دور price را با قانون ثابت ADJUST جابه‌جا و پهن/باریک می‌کند. (پایین، بالا)"""
    half = (np.log(high) - np.log(low)) / 2          # نصف پهنای بازه در مقیاس لگاریتمی
    center = np.log(price) + ADJUST["SHIFT"] * (view["tone"] / 2) * view["confidence"] * (2 * half)
    half *= ADJUST["WIDTH"][view["risk"]]
    return float(np.exp(center - half)), float(np.exp(center + half))


TONE_FA = {-2: "به ضرر طلا", -1: "کمی به ضرر طلا", 0: "خنثی یا قاطی", 1: "کمی به نفع طلا", 2: "به نفع طلا"}
RISK_FA = {"low": "خبر مهمی در راه نیست", "normal": "عادی",
           "high": "خبر مهمی نزدیک است؛ احتمال تکان بزرگ بیشتر است"}
TITLE = "🤖 جمع‌بندی با هوش مصنوعی (آزمایشی):"
UNAVAILABLE = ["", "🤖 نظر هوش مصنوعی این بار نرسید؛ گزارش بالا کامل است."]


def head_lines(view, fa):
    L = ["", TITLE,
         f"لحن اخبار: {TONE_FA[view['tone']]} (اطمینان {fa(view['confidence'] * 100)}٪)؛ {RISK_FA[view['risk']]}."]
    if view["summary_fa"]:
        L.append(view["summary_fa"])
    return L


def tail_lines(view):
    L = []
    if view["events"]:
        L.append("رویدادهای مهم پیش رو: " + "؛ ".join(view["events"]))
    if view["suggestion_fa"]:
        L.append("💡 " + view["suggestion_fa"])
    return L


def text_section(kind, report, fa, price=None):
    """بخش هوش مصنوعی برای گزارش و سیگنال (بدون عدد تازه). [] اگر کلید نیست."""
    if not configured():
        return []
    view = advise(kind, report, price)
    if view is None:
        return UNAVAILABLE
    return head_lines(view, fa) + tail_lines(view)


def forecast_section(price, ranges, made_at, fa, report):
    """بخش هوش مصنوعی پیش‌بینی حدودی + ثبت بازه‌ها در لاگ.
    ranges = [(روز، برچسب، پایین، بالا)] از بازه «به احتمال زیاد» اصلی. [] اگر کلید نیست."""
    if not configured():
        return []
    view = advise("forecast", report, price)
    if view is None:
        return UNAVAILABLE
    L = head_lines(view, fa) + ["با اثر اخبار:"]
    rows = []
    for days, label, lo, hi in ranges:
        alo, ahi = adjust(price, lo, hi, view)
        L.append(f"• {label}: به احتمال زیاد بین {fa(alo)} و {fa(ahi)}")
        rows.append({"made_at": made_at, "price": round(price, 2), "days": days,
                     "tone": view["tone"], "confidence": view["confidence"], "risk": view["risk"],
                     "base_low": round(lo, 2), "base_high": round(hi, 2),
                     "news_low": round(alo, 2), "news_high": round(ahi, 2)})
    pd.DataFrame(rows).to_csv(NEWS_FORECAST_LOG_PATH, mode="a",
                              header=not os.path.exists(NEWS_FORECAST_LOG_PATH), index=False)
    return L + tail_lines(view)


def main():
    if not configured():
        raise SystemExit(f"No Gemini key. Put it in {KEY_PATH} (one line).")
    base = _read(BASE_URL_PATH) or DEFAULT_BASE
    print(f" Asking Gemini ({model_name()}) via {base} ...")
    view = advise("forecast", "(test from the terminal: no bot report)")
    if view is None:
        raise SystemExit("Gemini did not answer. See the message above.")
    print(f" tone={view['tone']}  confidence={view['confidence']}  risk={view['risk']}  "
          f"time={view['seconds']} s")
    for h in view["headlines"]:
        print(f"   [{h['effect']}] {h['title']}")
    for e in view["events"]:
        print(f"   event: {e}")
    print(f" OK. (The Persian text is in {AI_LOG_PATH})")


if __name__ == "__main__":
    main()
