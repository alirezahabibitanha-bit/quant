#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_ai.py - خواندن «لحن اخبار» طلا با جیمینای و اثر دادن آزمایشی آن در بازه پیش‌بینی

جیمینای با جستجوی گوگل اخبار یکی دو روز اخیر را می‌خواند و یک جواب کوتاه و عددی می‌دهد:
  tone       از -2 (اخبار به ضرر طلا) تا +2 (به نفع طلا)
  confidence از 0 تا 1 (چقدر مطمئن است)
  risk       low / normal / high (آیا خبر مهمی مثل جلسه فدرال رزرو، تورم یا اشتغال آمریکا نزدیک است؟)
  events     رویدادهای مهم چند روز آینده
  summary_fa خلاصه دو سه جمله‌ای فارسی

⚠️ این روش تست نشده است. تست‌های قبلی (backtest_2min.py، forecast_models.py) نشان داد جهت قیمت
قابل پیش‌بینی نیست. برای همین:
  - بازه اصلی ربات عوض نمی‌شود؛ بازه «با اثر اخبار» جدا و با برچسب 🧪 آزمایشی نشان داده می‌شود.
  - اثر اخبار محدود است و قانونش از قبل ثابت شده (پایین، ADJUST)؛ برای جور شدن با گذشته دست‌کاری نشود.
  - هر بار در news_forecast_log.csv ثبت می‌شود تا بعداً با قیمت واقعی سنجیده شود.
  - اگر جیمینای جواب نداد یا جواب عجیب داد، ربات بدون این بخش کار می‌کند.

تنظیمات (روی سرور، هرگز در کد یا GitHub):
  ~/.gemini_key        کلید جیمینای (لازم)
  ~/.gemini_base_url   فقط روی سرور ایران (آدرس تونل). روی سرور آلمان نباید باشد؛ مستقیم به گوگل وصل می‌شود.
  ~/.gemini_model      اختیاری؛ اسم مدل اگر بخواهید عوضش کنید (پیش‌فرض پایین، DEFAULT_MODEL)

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
CACHE_PATH = "news_cache.json"            # آخرین جواب جیمینای، تا با هر دکمه دوباره پول/سهمیه خرج نشود
CACHE_SECONDS = 15 * 60                   # جواب تا ۱۵ دقیقه دوباره استفاده می‌شود
NEWS_LOG_PATH = "news_log.csv"            # هر جواب تازه جیمینای
NEWS_FORECAST_LOG_PATH = "news_forecast_log.csv"   # هر بازه «با اثر اخبار»، برای سنجش بعدی
TIMEOUT = 60                              # جستجو در گوگل کمی طول می‌کشد
RETRY_AFTER = 5 * 60                      # بعد از خطا، تا ۵ دقیقه دوباره امتحان نشود تا ربات معطل نماند
_FAILED_AT = [0.0]

# قانون اثر اخبار — از قبل و بدون نگاه به داده انتخاب شده؛ تست نشده است.
#   جابه‌جایی مرکز بازه = SHIFT × (tone / 2) × confidence × اندازه بازه «به احتمال زیاد»
#   یعنی حداکثر یک چهارم بازه، و فقط وقتی جیمینای کاملاً مطمئن است.
#   پهنای بازه: اگر خبر مهمی نزدیک است پهن‌تر، اگر خبری نیست کمی باریک‌تر.
ADJUST = {"SHIFT": 0.25, "WIDTH": {"low": 0.9, "normal": 1.0, "high": 1.3}}

PROMPT = """You are a careful financial news analyst. Use Google Search to read the most important news
from the last 48 hours that can move the price of gold in US dollars (XAU/USD): US Federal Reserve and
interest-rate expectations, the US dollar, inflation, jobs data, geopolitics, central-bank gold buying,
ETF flows. Also check the economic calendar for the next 7 days (FOMC, CPI, NFP, PCE, etc.).

Judge the overall TONE of the news for gold, not your own price prediction. Be neutral when mixed.
Reply with ONLY one JSON object, no other text, exactly in this shape:
{"tone": <integer -2..2; -2 clearly negative for gold, 0 neutral/mixed, 2 clearly positive>,
 "confidence": <number 0..1>,
 "risk": "<low|normal|high: chance of an unusually big move in the coming days because of scheduled events or breaking news>",
 "events": ["<upcoming scheduled event with date, max 4 items>"],
 "headlines": [{"title": "<short English headline>", "effect": "<up|down|mixed>"}],
 "summary_fa": "<2-3 short, plain Persian sentences for a non-expert, no jargon>"}
Use at most 5 headlines. Today (UTC) is {today}."""


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


# ---------------------------------------------------------------- گرفتن جواب از جیمینای

def ask_gemini(prompt, use_search=True):
    """متن جواب جیمینای. کلید فقط در سربرگ درخواست است، نه در آدرس؛ پس در پیام خطا نمی‌آید."""
    key = _read(KEY_PATH)
    if not key:
        raise RuntimeError(f"No Gemini key in {KEY_PATH}")
    base = (_read(BASE_URL_PATH) or DEFAULT_BASE).rstrip("/")
    model = _read(MODEL_PATH) or DEFAULT_MODEL
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}}
    if use_search:
        body["tools"] = [{"google_search": {}}]
    r = requests.post(f"{base}/v1beta/models/{model}:generateContent", json=body, timeout=TIMEOUT,
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
    متن اخبار ممکن است حرف عجیبی به جیمینای زده باشد؛ برای همین فقط عددهای محدود استفاده می‌شود."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    d = json.loads(m.group(0))
    tone = int(round(float(d.get("tone", 0))))
    conf = float(d.get("confidence", 0))
    risk = str(d.get("risk", "normal")).strip().lower()
    out = {"tone": max(-2, min(2, tone)),
           "confidence": max(0.0, min(1.0, conf)) if np.isfinite(conf) else 0.0,
           "risk": risk if risk in ADJUST["WIDTH"] else "normal",
           "events": [str(x)[:120] for x in _as_list(d.get("events"))][:4],
           "headlines": [], "summary_fa": str(d.get("summary_fa", ""))[:600]}
    for h in _as_list(d.get("headlines"))[:5]:
        if isinstance(h, dict) and h.get("title"):
            eff = str(h.get("effect", "mixed")).lower()
            out["headlines"].append({"title": str(h["title"])[:160],
                                     "effect": eff if eff in ("up", "down", "mixed") else "mixed"})
    return out


def news_view(force=False):
    """آخرین «لحن اخبار». تا CACHE_SECONDS جواب قبلی استفاده می‌شود. اگر نشد None (ربات بدون آن ادامه می‌دهد)."""
    if not configured():
        return None
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            cached = json.load(fh)
        if not force and time.time() - cached.get("fetched_at", 0) < CACHE_SECONDS:
            return cached
    except (FileNotFoundError, ValueError):
        pass
    if not force and time.time() - _FAILED_AT[0] < RETRY_AFTER:
        return None
    try:
        today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M")
        view = parse_answer(ask_gemini(PROMPT.replace("{today}", today)))
    except Exception as e:
        _FAILED_AT[0] = time.time()
        print(f" News analysis skipped: {e}")
        return None
    view["fetched_at"] = time.time()
    view["model"] = _read(MODEL_PATH) or DEFAULT_MODEL
    with open(CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(view, fh, ensure_ascii=False)
    row = {"fetched_at": pd.Timestamp.now(tz="Asia/Tehran").strftime("%Y-%m-%d %H:%M"),
           "model": view["model"], "tone": view["tone"], "confidence": view["confidence"], "risk": view["risk"],
           "events": " | ".join(view["events"]),
           "headlines": " | ".join(f"{h['effect']}: {h['title']}" for h in view["headlines"])}
    pd.DataFrame([row]).to_csv(NEWS_LOG_PATH, mode="a", header=not os.path.exists(NEWS_LOG_PATH), index=False)
    return view


# ---------------------------------------------------------------- اثر روی بازه

def adjust(price, low, high, view):
    """بازه (low, high) دور price را با قانون ثابت ADJUST جابه‌جا و پهن/باریک می‌کند. (پایین، بالا)"""
    half = (np.log(high) - np.log(low)) / 2          # نصف پهنای بازه در مقیاس لگاریتمی
    center = np.log(price) + ADJUST["SHIFT"] * (view["tone"] / 2) * view["confidence"] * (2 * half)
    half *= ADJUST["WIDTH"][view["risk"]]
    return float(np.exp(center - half)), float(np.exp(center + half))


TONE_FA = {-2: "به ضرر طلا", -1: "کمی به ضرر طلا", 0: "خنثی یا قاطی", 1: "کمی به نفع طلا", 2: "به نفع طلا"}
RISK_FA = {"low": "خبر مهمی در راه نیست", "normal": "عادی",
           "high": "خبر مهمی نزدیک است؛ احتمال تکان بزرگ بیشتر است"}
EFFECT_FA = {"up": "⬆️", "down": "⬇️", "mixed": "↔️"}


def forecast_section(price, ranges, made_at, fa):
    """بخش «🧪 آزمایشی — با اثر اخبار» برای پیام پیش‌بینی + ثبت در لاگ.
    ranges = [(روز، برچسب، پایین، بالا)] از بازه «به احتمال زیاد» اصلی. fa = تابع نوشتن عدد فارسی.
    اگر جیمینای در دسترس نبود، [] برمی‌گرداند."""
    view = news_view()
    if view is None:
        return ["", "🧪 تحلیل اخبار با هوش مصنوعی الان در دسترس نبود."] if configured() else []
    L = ["", "🧪 آزمایشی — با اثر اخبار (جیمینای):",
         f"لحن اخبار: {TONE_FA[view['tone']]} (اطمینان {fa(view['confidence'] * 100)}٪)؛ "
         f"{RISK_FA[view['risk']]}."]
    if view["summary_fa"]:
        L.append(view["summary_fa"])
    rows = []
    for days, label, lo, hi in ranges:
        alo, ahi = adjust(price, lo, hi, view)
        L.append(f"• {label}: به احتمال زیاد بین {fa(alo)} و {fa(ahi)} تتر")
        rows.append({"made_at": made_at, "price": round(price, 2), "days": days,
                     "tone": view["tone"], "confidence": view["confidence"], "risk": view["risk"],
                     "base_low": round(lo, 2), "base_high": round(hi, 2),
                     "news_low": round(alo, 2), "news_high": round(ahi, 2)})
    if view["events"]:
        L.append("رویدادهای مهم پیش رو: " + "؛ ".join(view["events"]))
    L += ["هیچ‌وقت ثابت نشده که اخبار جهت قیمت را از قبل نشان بدهد. این بازه فقط برای مقایسه است",
          "و ثبت می‌شود تا بعداً ببینیم از بازه اصلی بهتر بوده یا نه."]
    pd.DataFrame(rows).to_csv(NEWS_FORECAST_LOG_PATH, mode="a",
                              header=not os.path.exists(NEWS_FORECAST_LOG_PATH), index=False)
    return L


def main():
    if not configured():
        raise SystemExit(f"No Gemini key. Put it in {KEY_PATH} (one line).")
    base = _read(BASE_URL_PATH) or DEFAULT_BASE
    print(f" Asking Gemini ({_read(MODEL_PATH) or DEFAULT_MODEL}) via {base} ...")
    view = news_view(force=True)
    if view is None:
        raise SystemExit("Gemini did not answer. See the message above.")
    print(f" tone={view['tone']}  confidence={view['confidence']}  risk={view['risk']}")
    for h in view["headlines"]:
        print(f"   [{h['effect']}] {h['title']}")
    for e in view["events"]:
        print(f"   event: {e}")
    print(" OK. (The Persian summary is in news_cache.json)")


if __name__ == "__main__":
    main()
