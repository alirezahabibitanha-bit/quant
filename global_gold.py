#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
global_gold.py - قیمت طلای بازار جهانی به دلار (XAU/USD)، بدون والکس و بدون تتر

هیچ خرید و فروشی انجام نمی‌دهد و کلید لازم ندارد.

منبع‌ها:
  - قیمت روزانه: یاهو فایننس، طلای آتی نیویورک (GC=F). همان داده‌ای که همه تست‌های ۲۰ ساله روی آن انجام شد.
  - قیمت لحظه‌ای: Swissquote (بانک و کارگزار سوئیسی)، قیمت خرید و فروش نقد طلا (XAU/USD)، کمتر از ۱ ثانیه تأخیر.
    آدرسش عمومی است ولی رسماً اعلام نشده؛ اگر جواب نداد، api.gold-api.com (هر ۳۰ تا ۶۰ ثانیه به‌روز، یک قیمت).
    قیمت آتی معمولاً کمی (حدود ۰٫۵ تا ۱٪) بالاتر از قیمت نقد است؛ برای اینکه میانگین‌ها و قیمت لحظه‌ای
    هم‌اندازه باشند، کل سری روزانه با نسبت «قیمت نقد ÷ قیمت آتی» همین لحظه کوچک می‌شود.
    درصدها (نوسان، تغییر سالانه) با این کار عوض نمی‌شوند.
  - کندل دقیقه‌ای: داده دقیقه‌ای رایگان یاهو حدود ۱۰ دقیقه عقب است، و Swissquote فقط قیمت همین لحظه را می‌دهد.
    پس ربات خودش (start_collector، یک رشته پس‌زمینه) هر ۱۰ ثانیه قیمت Swissquote را می‌گیرد و هر دقیقه
    یک کندل (قیمت وسط خرید و فروش) در global_bars.csv می‌نویسد (UTC، ۳ روز آخر). «پیش‌بینی ۲ دقیقه» از این فایل
    کار می‌کند و حدود ۸۰ دقیقه داده لازم دارد. قیمتی که بیش از ۲ دقیقه کهنه باشد (بازار بسته) ثبت نمی‌شود.
    Swissquote از ایران مستقیم باز نمی‌شود؛ اگر نرسید، کندل‌ها از gold-api ساخته می‌شوند (دقیقه‌ای یک قیمت تازه؛
    برای کندل ۲ دقیقه‌ای کافی است، ولی قیمت تا حدود ۱ دقیقه عقب است). live_source() می‌گوید الان کدام است.

راه رسیدن از ایران:
  اگر ~/.proxy_url باشد (پروکسی Xray روی خود سرور ایران، relay/xray_setup.sh)، اول از آن می‌رود.
  بعد اگر ~/.gemini_base_url باشد (تونل سرور ایران به سرور خارج، relay/README.md)، اول از همان تونل می‌رود
  (‎/yahoo/، ‎/swissquote/ و ‎/goldapi/ روی nginx سرور خارج)؛ اگر نشد، مستقیم امتحان می‌کند.

کارمزد هر طرف: از ~/.global_fee (درصد، مثلاً 0.1). اگر نباشد صفر.

تست از ترمینال:
  python3 global_gold.py
"""
import os
import threading
import time

import pandas as pd
import requests

import net_proxy

YAHOO = "https://query1.finance.yahoo.com"
GOLDAPI = "https://api.gold-api.com"
SWISSQUOTE = "https://forex-data-feed.swissquote.com"
FUTURES = "GC=F"
BASE_URL_PATH = os.path.expanduser("~/.gemini_base_url")
FEE_PATH = os.path.expanduser("~/.global_fee")
HEADERS = {"User-Agent": "Mozilla/5.0 (personal gold advisor)"}
SPOT_CACHE_SECONDS = 20
QUOTE_MAX_AGE = 120          # قیمت Swissquote کهنه‌تر از این = بازار بسته؛ ثبت نمی‌شود
COLLECT_SECONDS = 10
BARS_PATH = "global_bars.csv"   # نسبی: در حالت --global، پوشه global/
KEEP_SECONDS = 3 * 86400
MIN_BARS = 40                # همان حداقل advisor.fetch_minutes (کندل ۲ دقیقه‌ای)
_spot = {"at": 0.0, "price": None, "age": None, "told_at": 0.0}
GOLDAPI_MAX_AGE = 180        # gold-api دقیقه‌ای یک بار به‌روز می‌شود؛ کهنه‌تر از این = بازار بسته
_source = {"name": None}
_quote = {"at": 0.0, "q": None, "failed_at": 0.0, "told_at": 0.0}
FAIL_PAUSE = 60              # بعد از خطای Swissquote، این‌قدر ثانیه سراغش نرو (گزارش‌ها منتظر نمانند)
FAIL_LOG_EVERY = 600         # خطای Swissquote حداکثر هر ۱۰ دقیقه یک بار در لاگ
_lock = threading.Lock()


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except FileNotFoundError:
        return None


def _get_json(direct_url, relay_prefix, params=None, timeout=20):
    """اول از پروکسی (اگر هست)، بعد از تونل (اگر هست)، بعد مستقیم. خطای آخر را بالا می‌دهد."""
    tries = []
    px = net_proxy.proxies()
    if px:
        tries.append((direct_url, px))
    base = _read(BASE_URL_PATH)
    if base:
        host = next(h for h in (YAHOO, SWISSQUOTE, GOLDAPI) if direct_url.startswith(h))
        tries.append((base.rstrip("/") + "/" + relay_prefix + direct_url[len(host):], None))
    tries.append((direct_url, None))
    err = None
    for url, proxies in tries:
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout, proxies=proxies)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            err = e
    raise RuntimeError(f"{direct_url.split('?')[0]}: {err}")


def route():
    """به چه ترتیبی امتحان می‌شود (برای چاپ در ترمینال)."""
    steps = []
    if net_proxy.proxy_url():
        steps.append("proxy " + net_proxy.proxy_url())
    if _read(BASE_URL_PATH):
        steps.append("tunnel " + _read(BASE_URL_PATH))
    return " then ".join(steps + ["direct"])


def fee():
    """کارمزد هر طرف (کسر، نه درصد)."""
    try:
        return float(_read(FEE_PATH) or 0) / 100
    except ValueError:
        return 0.0


def swissquote():
    """{'bid','ask','age'} قیمت نقد طلا از Swissquote (کم‌فاصله‌ترین گروه قیمت). اگر نشد None.
    age = چند ثانیه از آخرین قیمت گذشته (آخر هفته بزرگ است)."""
    with _lock:
        if time.time() - _quote["at"] < 5 and _quote["q"]:
            return _quote["q"]
        if time.time() - _quote["failed_at"] < FAIL_PAUSE:
            return None
    try:
        d = _get_json(f"{SWISSQUOTE}/public-quotes/bboquotes/instrument/XAU/USD", "swissquote", timeout=10)
        prices = d[0]["spreadProfilePrices"]
        best = min(prices, key=lambda x: float(x["ask"]) - float(x["bid"]))
        q = {"bid": float(best["bid"]), "ask": float(best["ask"]), "age": time.time() - d[0]["ts"] / 1000}
    except Exception as e:
        with _lock:
            _quote["failed_at"] = time.time()
            tell = time.time() - _quote["told_at"] > FAIL_LOG_EVERY
            if tell:
                _quote["told_at"] = time.time()
        if tell:
            print(f" Swissquote price failed (next report in 10 min): {str(e)[:300]}")
        return None
    if not (100 < q["bid"] <= q["ask"] < 100000 and q["ask"] / q["bid"] < 1.01):
        return None
    with _lock:
        _quote.update(at=time.time(), q=q)
    return q


def spot_price():
    """قیمت نقد طلا به دلار (هر اونس): وسط خرید و فروش Swissquote، وگرنه gold-api. اگر نشد None."""
    q = swissquote()
    if q:
        return (q["bid"] + q["ask"]) / 2
    g = goldapi()
    return g["price"] if g else None


def goldapi():
    """{'price','age'} از gold-api (یک قیمت، دقیقه‌ای یک بار به‌روز). age = ثانیه از آخرین به‌روزرسانی. اگر نشد None."""
    if time.time() - _spot["at"] < SPOT_CACHE_SECONDS and _spot["price"]:
        return {"price": _spot["price"], "age": _spot["age"]}
    try:
        d = _get_json(f"{GOLDAPI}/price/XAU", "goldapi", timeout=15)
        p = float(d["price"])
        try:
            age = time.time() - pd.Timestamp(d["updatedAt"]).timestamp()
        except Exception:
            age = None
    except Exception as e:
        if time.time() - _spot["told_at"] > FAIL_LOG_EVERY:
            _spot["told_at"] = time.time()
            print(f" gold-api price failed (next report in 10 min): {str(e)[:300]}")
        return None
    if not 100 < p < 100000:
        return None
    _spot.update(at=time.time(), price=p, age=age)
    return {"price": p, "age": age}


def live_source():
    """منبعی که جمع‌آوری کندل دقیقه‌ای الان از آن می‌خواند: 'swissquote'، 'gold-api' یا None (هیچ‌کدام / بازار بسته)."""
    return _source["name"]


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
    """قیمت لحظه‌ای به شکل advisor.fetch_book: خرید و فروش Swissquote؛ اگر نشد gold-api با خرید = فروش.
    اگر هیچ‌کدام نشد None."""
    q = swissquote()
    if q:
        return {"bid": q["bid"], "ask": q["ask"]}
    p = spot_price()
    return None if p is None else {"bid": p, "ask": p}


# ---------------------------------------------------------------- کندل دقیقه‌ای (جمع‌آوری خود ربات)

def _append_bar(minute, o, c):
    new = not os.path.exists(BARS_PATH)
    with open(BARS_PATH, "a", encoding="utf-8") as fh:
        if new:
            fh.write("time,open,close\n")
        fh.write(f"{minute},{o:.3f},{c:.3f}\n")


def _prune():
    """فقط ۳ روز آخر در فایل بماند."""
    if not os.path.exists(BARS_PATH):
        return
    df = pd.read_csv(BARS_PATH)
    df = df[df.time >= time.time() - KEEP_SECONDS].drop_duplicates("time", keep="last")
    tmp = BARS_PATH + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, BARS_PATH)


def _collect_loop():
    minute, o, c = None, None, None
    last_prune = 0.0
    while True:
        t0 = time.time()
        try:
            q, mid = swissquote(), None
            if q and q["age"] < QUOTE_MAX_AGE:
                mid, _source["name"] = (q["bid"] + q["ask"]) / 2, "swissquote"
            else:
                g = goldapi()
                if g and (g["age"] is None or g["age"] < GOLDAPI_MAX_AGE):
                    mid, _source["name"] = g["price"], "gold-api"
                else:
                    _source["name"] = None
            if mid is not None:
                m = int(t0 // 60) * 60
                if minute is not None and m != minute:
                    _append_bar(minute, o, c)
                    o = None
                minute = m
                o = mid if o is None else o
                c = mid
            if t0 - last_prune > 3600:
                last_prune = t0
                _prune()
        except Exception as e:     # رشته جمع‌آوری نباید با یک خطا برای همیشه بایستد
            print(f" Minute collector error: {e}")
        time.sleep(max(1.0, COLLECT_SECONDS - (time.time() - t0)))


def start_collector():
    """رشته پس‌زمینه جمع‌آوری کندل دقیقه‌ای را روشن می‌کند (یک بار، در bale_bot --global)."""
    threading.Thread(target=_collect_loop, name="gold-minutes", daemon=True).start()


def minutes_status():
    """(تعداد کندل ۱ دقیقه‌ای در ۸۰ دقیقه اخیر، چند دقیقه از آخرین کندل گذشته یا None)"""
    if not os.path.exists(BARS_PATH):
        return 0, None
    t = pd.read_csv(BARS_PATH).time
    if t.empty:
        return 0, None
    return int((t >= time.time() - 80 * 60).sum()), (time.time() - t.max() - 60) / 60


def fetch_minutes(hours=4):
    """کندل‌های ۲ دقیقه‌ای چند ساعت اخیر، به همان شکل advisor.fetch_minutes (time به UTC، open، close)."""
    if not os.path.exists(BARS_PATH):
        raise SystemExit("No collected minute data yet.")
    df = pd.read_csv(BARS_PATH)
    df = df[df.time >= time.time() - hours * 3600].drop_duplicates("time", keep="last").sort_values("time")
    df.index = pd.to_datetime(df.time.values, unit="s", utc=True)
    bars = df.resample("2min").agg({"open": "first", "close": "last"}).dropna()
    if len(bars) and bars.index[-1] + pd.Timedelta("2min") > pd.Timestamp.now(tz="UTC"):
        bars = bars.iloc[:-1]           # کندل نیمه‌کاره
    if len(bars) < MIN_BARS:
        raise SystemExit("Not enough collected minute data.")
    bars.index.name = "time"
    return bars.reset_index()


def main():
    print(" Global gold price (no Wallex, no USDT)")
    print(f" Route: {route()}")
    q = swissquote()
    print(f" Swissquote       : bid {q['bid']:,.2f} / ask {q['ask']:,.2f} ({q['age']:.0f} s old)" if q else
          " Swissquote       : FAILED (will use gold-api)")
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
