#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mt_bridge.py - گرفتن قیمت زنده از متاتریدر ۵

متاتریدر فقط روی ویندوز درست کار می‌کند و کتابخانه پایتونش هم فقط ویندوزی است. برای همین راه ساده و
قابل اعتماد این است: یک «اکسپرت» کوچک (metatrader/PriceBridge.mq5) داخل خود متاتریدر هر ۱۰ ثانیه
قیمت‌ها را به این برنامه روی سرور می‌فرستد. فرقی نمی‌کند متاتریدر روی کامپیوتر خودتان باشد،
روی یک سرور ویندوزی، یا روی همین سرور با Wine.

    متاتریدر (اکسپرت PriceBridge) ──HTTP + امضا──► mt_bridge.py روی سرور ──► فایل‌ها ──► ربات

فقط قیمت می‌گیرد. هیچ دستور خرید و فروشی به متاتریدر نمی‌رود و راهی هم برایش ساخته نشده.

امنیت:
  - هر بسته یک «امضا» دارد که با رمز مشترک ~/.mt_bridge_secret ساخته می‌شود (رمز خودش فرستاده نمی‌شود).
    بسته بدون امضای درست، قدیمی‌تر از ۲ دقیقه، یا تکراری رد می‌شود. پس کسی نمی‌تواند قیمت جعلی بفرستد.
  - داده‌ها (قیمت طلا) محرمانه نیستند، برای همین رمزگذاری HTTPS لازم نشد.
  - حداکثر اندازه هر بسته ۱ مگابایت.

فایل‌هایی که می‌سازد (در پوشه پروژه):
  mt_tick.json   آخرین قیمت خرید و فروش (bid/ask) و زمانش
  mt_bars.csv    کندل‌های ۱ دقیقه‌ای ۳ روز اخیر (زمان به UTC)
  mt_daily.csv   قیمت پایانی روزانه (حدود ۲ سال؛ برای پیش‌بینی حدودی، گزارش و سیگنال‌ها)

اجرا:
  python3 mt_bridge.py                    # گوش دادن روی 127.0.0.1:8787 (متاتریدر روی همین سرور)
  python3 mt_bridge.py 8787 0.0.0.0       # اگر متاتریدر روی کامپیوتر دیگری است (درگاه را در دیوار آتش باز کنید)
  دائمی: با فایل mt-bridge.service
"""
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SECRET_PATH = os.path.expanduser("~/.mt_bridge_secret")
TICK_PATH = os.path.join(HERE, "mt_tick.json")
BARS_PATH = os.path.join(HERE, "mt_bars.csv")
DAILY_PATH = os.path.join(HERE, "mt_daily.csv")
PORT = 8787
MAX_BODY = 1_000_000
MAX_SKEW = 120                 # ثانیه؛ بسته قدیمی‌تر یا جلوتر از این رد می‌شود
KEEP_DAYS = 3
STALE_SECONDS = 120            # آخرین قیمت قدیمی‌تر از این = متاتریدر وصل نیست
_LOCK = threading.Lock()
_LAST_TS = [0]                 # زمان آخرین بسته قبول‌شده (برای رد بسته تکراری)


def read_secret():
    try:
        with open(SECRET_PATH, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except FileNotFoundError:
        return None


def signature(secret, ts, body):
    """sha256(رمز + زمان + متن + رمز). همان چیزی که اکسپرت در متاتریدر حساب می‌کند."""
    return hashlib.sha256(secret.encode() + b"\n" + str(ts).encode() + b"\n" + body + b"\n"
                          + secret.encode()).hexdigest()


def _write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def store(packet):
    """یک بسته از متاتریدر را ذخیره می‌کند. زمان کندل‌ها در متاتریدر به وقت سرور کارگزار است؛
    اکسپرت اختلافش با UTC را می‌فرستد (gmt_offset) و این‌جا به UTC برگردانده می‌شود."""
    offset = int(packet.get("gmt_offset", 0))
    tick = {"symbol": str(packet.get("symbol", ""))[:32], "bid": float(packet["bid"]), "ask": float(packet["ask"]),
            "digits": int(packet.get("digits", 2)), "broker": str(packet.get("broker", ""))[:64],
            "tick_utc": int(packet.get("tick_time", 0)) - offset, "received_at": time.time()}
    if not (tick["bid"] > 0 and tick["ask"] >= tick["bid"]):
        raise ValueError("bad bid/ask")
    bars = pd.DataFrame(packet.get("bars") or [], columns=["t", "o", "h", "l", "c", "v"])
    _write_atomic(TICK_PATH, json.dumps(tick))
    store_daily(packet.get("daily") or [])
    if bars.empty:
        return 0
    bars = bars.apply(pd.to_numeric, errors="coerce").dropna()
    bars["time"] = bars.t.astype("int64") - offset
    bars = bars.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "tick_volume"})
    bars = bars[["time", "open", "high", "low", "close", "tick_volume"]]
    if os.path.exists(BARS_PATH):
        bars = pd.concat([pd.read_csv(BARS_PATH), bars])
    bars = bars.drop_duplicates("time", keep="last").sort_values("time")
    bars = bars[bars.time >= bars.time.max() - KEEP_DAYS * 86400]
    _write_atomic(BARS_PATH, bars.to_csv(index=False))
    return len(bars)


def store_daily(rows):
    """کندل‌های روزانه [زمان سرور کارگزار، قیمت پایانی]. تاریخ = تاریخ روز در ساعت کارگزار."""
    if not rows:
        return
    d = pd.DataFrame(rows, columns=["t", "close"]).apply(pd.to_numeric, errors="coerce").dropna()
    d["date"] = pd.to_datetime(d.t.astype("int64"), unit="s").dt.strftime("%Y-%m-%d")
    d = d[["date", "close"]]
    if os.path.exists(DAILY_PATH):
        d = pd.concat([pd.read_csv(DAILY_PATH), d])
    d = d.drop_duplicates("date", keep="last").sort_values("date")
    _write_atomic(DAILY_PATH, d.to_csv(index=False))


class Handler(BaseHTTPRequestHandler):
    server_version = "mt-bridge"

    def _reply(self, code, text):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply(200, "ok\n")       # برای تست ساده از مرورگر یا curl

    def do_POST(self):
        if self.path != "/mt/push":
            return self._reply(404, "not found\n")
        secret = read_secret()
        if not secret:
            return self._reply(503, "no secret on server\n")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            ts = int(self.headers.get("X-Time", "0"))
        except ValueError:
            return self._reply(400, "bad headers\n")
        if not 0 < size <= MAX_BODY:
            return self._reply(413, "bad size\n")
        body = self.rfile.read(size)
        if not hmac.compare_digest(signature(secret, ts, body), self.headers.get("X-Sign", "")):
            print(f" Rejected packet from {self.client_address[0]}: bad signature")
            return self._reply(403, "bad signature\n")
        with _LOCK:
            if abs(time.time() - ts) > MAX_SKEW or ts <= _LAST_TS[0]:
                return self._reply(403, "old or repeated packet (check the PC clock)\n")
            _LAST_TS[0] = ts
            try:
                n = store(json.loads(body))
            except Exception as e:
                print(f" Bad packet: {e}")
                return self._reply(400, "bad packet\n")
        return self._reply(200, f"ok {n}\n")

    def log_message(self, fmt, *args):
        pass                            # هر ۱۰ ثانیه یک خط لاگ لازم نیست


# ---------------------------------------------------------------- برای ربات و برنامه‌های دیگر

def latest():
    """آخرین قیمت متاتریدر (dict) یا None اگر هنوز چیزی نرسیده."""
    try:
        with open(TICK_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return None


def has_data():
    return latest() is not None


def is_fresh(tick=None):
    tick = tick or latest()
    return tick is not None and time.time() - tick["received_at"] < STALE_SECONDS


def fetch_book():
    """بهترین قیمت خرید و فروش متاتریدر به همان شکل advisor.fetch_book. اگر کهنه یا نبود None."""
    t = latest()
    if t is None or not is_fresh(t):
        return None
    return {"bid": t["bid"], "ask": t["ask"]}


def fetch_daily(days=None):
    """قیمت پایانی روزانه به همان شکل advisor.fetch_daily (روزهای تقویمی، به وقت تهران، بدون امروز).
    طلای جهانی آخر هفته بسته است؛ روزهای بسته با قیمت روز قبل پر می‌شوند. این همان تبدیلی است که
    برای XAUTUSDT (۷ روز در هفته) انجام شده بود: دوره‌های ۸۴، ۲۹۰ و ۳۶۵ روزه تقویمی می‌مانند و
    نوسان روزانه با تکان صفر آخر هفته‌ها، در افق‌های تقویمی (۷ و ۳۰ روز) همان نوسان واقعی می‌شود."""
    if not os.path.exists(DAILY_PATH):
        raise SystemExit("No MetaTrader daily data yet.")
    d = pd.read_csv(DAILY_PATH)
    s = pd.Series(d.close.values, index=pd.DatetimeIndex(pd.to_datetime(d.date)).tz_localize("Asia/Tehran"))
    today = pd.Timestamp.now(tz="Asia/Tehran").normalize()
    s = s[s.index < today]
    if len(s) < 30:
        raise SystemExit("Not enough MetaTrader daily data.")
    s = s.reindex(pd.date_range(s.index[0], today - pd.Timedelta(days=1), freq="D")).ffill()
    return s if days is None else s.iloc[-days:]


def fetch_minutes(hours=4):
    """کندل‌های ۲ دقیقه‌ای چند ساعت اخیر، به همان شکل advisor.fetch_minutes (time به UTC، open، close)."""
    if not os.path.exists(BARS_PATH):
        raise SystemExit("No MetaTrader data yet.")
    df = pd.read_csv(BARS_PATH)
    df = df[df.time >= time.time() - hours * 3600]
    df.index = pd.to_datetime(df.time.values, unit="s", utc=True)
    bars = df.resample("2min").agg({"open": "first", "close": "last"}).dropna()
    if len(bars) and bars.index[-1] + pd.Timedelta("2min") > pd.Timestamp.now(tz="UTC"):
        bars = bars.iloc[:-1]           # کندل نیمه‌کاره
    if len(bars) < 40:
        raise SystemExit("Not enough MetaTrader minute data.")
    bars.index.name = "time"
    return bars.reset_index()


def status_text():
    """پیام کوتاه فارسی برای دکمه «قیمت متاتریدر» در ربات."""
    import advisor
    t = latest()
    if t is None:
        return "هنوز قیمتی از متاتریدر نرسیده."
    mid = (t["bid"] + t["ask"]) / 2
    age = time.time() - t["received_at"]
    spread = (t["ask"] - t["bid"]) / mid * 100
    L = [f"📈 متاتریدر — {t['symbol']}" + (f" ({t['broker']})" if t["broker"] else ""),
         f"قیمت فروش (bid): {advisor.fa(t['bid'], 2)} دلار",
         f"قیمت خرید (ask): {advisor.fa(t['ask'], 2)} دلار",
         f"فاصله خرید و فروش: {advisor.fa(spread, 3)}٪"]
    quiet = time.time() - t["tick_utc"] if t.get("tick_utc") else 0
    if age < STALE_SECONDS and quiet > 15 * 60:
        L.append(f"متاتریدر وصل است ولی قیمت {advisor.fa(quiet / 60)} دقیقه است عوض نشده؛ "
                 "احتمالاً بازار بسته است (آخر هفته یا تعطیلی).")
    elif age < STALE_SECONDS:
        L.append(f"به‌روز است ({advisor.fa(age)} ثانیه پیش).")
    else:
        L.append(f"⚠️ آخرین قیمت {advisor.fa(age / 60)} دقیقه پیش رسیده؛ متاتریدر الان وصل نیست "
                 "یا بازار بسته است (آخر هفته).")
    L += ["", "فقط قیمت است؛ ربات هیچ معامله‌ای در متاتریدر انجام نمی‌دهد."]
    return "\n".join(L)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    if not read_secret():
        raise SystemExit(f"No secret. Put a long random password in {SECRET_PATH} (one line).")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f" MetaTrader bridge listening on {host}:{port} (POST /mt/push)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
