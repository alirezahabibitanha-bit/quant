#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
global_gold.py - قیمت طلای بازار جهانی به دلار (XAU/USD)، بدون والکس و بدون تتر

هیچ خرید و فروشی انجام نمی‌دهد و کلید لازم ندارد.

منبع‌ها:
  - قیمت روزانه: یاهو فایننس، طلای آتی نیویورک (GC=F). همان داده‌ای که همه تست‌های ۲۰ ساله روی آن انجام شد.
  - قیمت لحظه‌ای: api.gold-api.com، قیمت نقد طلا (XAU) به دلار، تقریباً هر دقیقه به‌روز می‌شود.
    قیمت آتی معمولاً کمی (حدود ۰٫۵ تا ۱٪) بالاتر از قیمت نقد است؛ برای اینکه میانگین‌ها و قیمت لحظه‌ای
    هم‌اندازه باشند، کل سری روزانه با نسبت «قیمت نقد ÷ قیمت آتی» همین لحظه کوچک می‌شود.
    درصدها (نوسان، تغییر سالانه) با این کار عوض نمی‌شوند.
  - داده ۱ دقیقه‌ای رایگان یاهو حدود ۱۰ دقیقه عقب است؛ برای همین «پیش‌بینی ۲ دقیقه» در این حالت خاموش است.

راه رسیدن از ایران:
  اگر ~/.gemini_base_url باشد (تونل سرور ایران به سرور خارج، relay/README.md)، اول از همان تونل می‌رود
  (‎/yahoo/ و ‎/goldapi/ روی nginx سرور خارج)؛ اگر نشد، مستقیم امتحان می‌کند.

کارمزد هر طرف: از ~/.global_fee (درصد، مثلاً 0.1). اگر نباشد صفر.

تست از ترمینال:
  python3 global_gold.py
"""
import os
import time

import pandas as pd
import requests

YAHOO = "https://query1.finance.yahoo.com"
GOLDAPI = "https://api.gold-api.com"
FUTURES = "GC=F"
BASE_URL_PATH = os.path.expanduser("~/.gemini_base_url")
FEE_PATH = os.path.expanduser("~/.global_fee")
HEADERS = {"User-Agent": "Mozilla/5.0 (personal gold advisor)"}
SPOT_CACHE_SECONDS = 20
_spot = {"at": 0.0, "price": None}


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except FileNotFoundError:
        return None


def _get_json(direct_url, relay_prefix, params=None, timeout=20):
    """اول از تونل (اگر هست)، بعد مستقیم. خطای آخر را بالا می‌دهد."""
    urls = []
    base = _read(BASE_URL_PATH)
    if base:
        host = YAHOO if direct_url.startswith(YAHOO) else GOLDAPI
        urls.append(base.rstrip("/") + "/" + relay_prefix + direct_url[len(host):])
    urls.append(direct_url)
    err = None
    for url in urls:
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            err = e
    raise RuntimeError(f"{direct_url.split('?')[0]}: {err}")


def fee():
    """کارمزد هر طرف (کسر، نه درصد)."""
    try:
        return float(_read(FEE_PATH) or 0) / 100
    except ValueError:
        return 0.0


def spot_price():
    """قیمت نقد طلا به دلار (هر اونس). اگر نشد None."""
    if time.time() - _spot["at"] < SPOT_CACHE_SECONDS and _spot["price"]:
        return _spot["price"]
    try:
        p = float(_get_json(f"{GOLDAPI}/price/XAU", "goldapi", timeout=15)["price"])
    except Exception as e:
        print(f" Spot gold price failed: {e}")
        return None
    if not 100 < p < 100000:
        return None
    _spot.update(at=time.time(), price=p)
    return p


def futures_daily():
    """(قیمت پایانی روزانه GC=F با برچسب تاریخ نیویورک، آخرین قیمت آتی همین الان)"""
    d = None
    for attempt in range(3):
        try:
            d = _get_json(f"{YAHOO}/v8/finance/chart/{FUTURES}", "yahoo",
                          params={"interval": "1d", "range": "2y"})
            break
        except RuntimeError:
            if attempt == 2:
                raise SystemExit("No daily gold prices received from Yahoo.")
            time.sleep(2 * (attempt + 1))
    res = d["chart"]["result"][0]
    close = res["indicators"]["quote"][0]["close"]
    t = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert("America/New_York")
    s = pd.Series(close, index=t.normalize().tz_localize(None), dtype="float64").dropna()
    s = s[~s.index.duplicated(keep="last")]
    return s, float(res["meta"].get("regularMarketPrice") or s.iloc[-1])


def fetch_daily(days=None):
    """قیمت پایانی روزانه به همان شکل advisor.fetch_daily (روزهای تقویمی، به وقت تهران، بدون امروز).
    آخر هفته بازار جهانی بسته است؛ مثل mt_bridge روزهای بسته با قیمت روز قبل پر می‌شوند تا دوره‌های
    تقویمی ۸۴، ۲۹۰ و ۳۶۵ روزه همان معنی XAUTUSDT را داشته باشند."""
    s, fut_now = futures_daily()
    s.index = s.index.tz_localize("Asia/Tehran")
    today = pd.Timestamp.now(tz="Asia/Tehran").normalize()
    s = s[s.index < today]
    if len(s) < 30:
        raise SystemExit("Not enough daily gold data.")
    s = s.reindex(pd.date_range(s.index[0], today - pd.Timedelta(days=1), freq="D")).ffill()
    spot = spot_price()
    if spot and fut_now and 0.9 < spot / fut_now < 1.1:   # هم‌اندازه کردن با قیمت نقد (توضیح بالای فایل)
        s = s * (spot / fut_now)
    return s if days is None else s.iloc[-days:]


def fetch_book():
    """قیمت لحظه‌ای به شکل advisor.fetch_book. بازار جهانی یک قیمت دارد؛ خرید = فروش. اگر نشد None."""
    p = spot_price()
    return None if p is None else {"bid": p, "ask": p}


def fetch_minutes(hours=4):
    raise SystemExit("No real-time minute data for the global market (Yahoo minute data is ~10 min late).")


def main():
    print(" Global gold price (no Wallex, no USDT)")
    print(f" Route: {'tunnel ' + _read(BASE_URL_PATH) + ' then direct' if _read(BASE_URL_PATH) else 'direct'}")
    p = spot_price()
    print(f" Spot XAU/USD now : {p:,.2f}" if p else " Spot XAU/USD now : FAILED")
    s, fut = futures_daily()
    print(f" GC=F futures now : {fut:,.2f}  (daily closes: {len(s)}, last {s.index[-1]:%Y-%m-%d})")
    d = fetch_daily()
    print(f" Daily series     : {len(d)} calendar days, last close {d.iloc[-1]:,.2f} USD"
          + ("  (scaled to spot)" if p else "  (not scaled: no spot price)"))
    print(f" Fee per side     : {fee() * 100:.3f}%  (from {FEE_PATH}; 0 if missing)")


if __name__ == "__main__":
    main()
