#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
دریافت داده‌های تاریخی کندل از صرافی والکس (اندپوینت عمومی /v1/udf/history)
این اسکریپت هیچ معامله‌ای انجام نمی‌دهد و به کلید API نیاز ندارد.

نمونه اجرا:
    python3 fetch_history.py --symbol USDTTMN --resolution 1 --days 30
    python3 fetch_history.py --symbol USDTTMN --resolution 60 --days 365
"""
import argparse
import os
import sys
import time

import pandas as pd
import requests

URL = "https://api.wallex.ir/v1/udf/history"
HEADERS = {"User-Agent": "quant-research/1.0"}

# طول هر کندل به ثانیه
RES_SECONDS = {"1": 60, "5": 300, "15": 900, "30": 1800, "60": 3600,
               "180": 10800, "240": 14400, "360": 21600, "720": 43200,
               "1D": 86400, "D": 86400}


def get_chunk(symbol, resolution, t_from, t_to, retries=4):
    """یک بازه زمانی را از سرور می‌گیرد؛ در صورت خطا چند بار دوباره تلاش می‌کند."""
    params = {"symbol": symbol, "resolution": resolution,
              "from": int(t_from), "to": int(t_to)}
    for attempt in range(retries):
        try:
            r = requests.get(URL, params=params, headers=HEADERS, timeout=20)
            if r.status_code == 429:          # محدودیت تعداد درخواست
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  ⚠️ تلاش {attempt + 1}: {e}")
            time.sleep(2 * (attempt + 1))
    return None


def to_frame(data):
    """پاسخ استاندارد UDF (s, t, o, h, l, c, v) را به جدول تبدیل می‌کند."""
    if not isinstance(data, dict):
        return None
    if "s" not in data and isinstance(data.get("result"), dict):
        data = data["result"]
    if data.get("s") != "ok" or not data.get("t"):
        return None
    n = len(data["t"])
    df = pd.DataFrame({
        "time": data["t"], "open": data["o"], "high": data["h"],
        "low": data["l"], "close": data["c"], "volume": data.get("v", [0] * n),
    })
    df = df.apply(pd.to_numeric, errors="coerce")
    return df.dropna(subset=["time", "close"])


def fmt(ts):
    return pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Tehran").strftime("%Y-%m-%d %H:%M")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="USDTTMN")
    p.add_argument("--resolution", default="1", help="1, 5, 15, 60, 240, 1D, ...")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--chunk", type=int, default=500, help="تعداد کندل در هر درخواست")
    p.add_argument("--sleep", type=float, default=0.7, help="مکث بین درخواست‌ها (ثانیه)")
    args = p.parse_args()

    if args.resolution not in RES_SECONDS:
        sys.exit(f"resolution نامعتبر. مقادیر مجاز: {list(RES_SECONDS)}")
    step = RES_SECONDS[args.resolution]

    end = int(time.time())
    start = end - args.days * 86400

    # تست اولیه: اگر فرمت پاسخ با انتظار ما فرق داشت، پاسخ خام را نشان بده
    probe = get_chunk(args.symbol, args.resolution, end - step * 100, end)
    if to_frame(probe) is None:
        print("❌ پاسخ سرور قابل خواندن نبود. پاسخ خام:")
        print(str(probe)[:800])
        sys.exit(1)

    print(f"📥 دریافت {args.symbol} | کندل {args.resolution} | {args.days} روز اخیر")
    frames, t_to, empty = [], end, 0
    while t_to > start:
        t_from = max(start, t_to - step * args.chunk)
        df = to_frame(get_chunk(args.symbol, args.resolution, t_from, t_to))
        if df is None or df.empty:
            empty += 1
            if empty >= 3:
                print("  ⏹ داده قدیمی‌تری روی سرور نیست.")
                break
        else:
            empty = 0
            frames.append(df)
            print(f"  ✅ {fmt(df.time.min())} → {fmt(df.time.max())}  ({len(df)} کندل)")
        t_to = t_from
        time.sleep(args.sleep)

    if not frames:
        sys.exit("❌ هیچ داده‌ای دریافت نشد.")

    data = pd.concat(frames).drop_duplicates("time").sort_values("time")
    data["datetime"] = pd.to_datetime(data.time, unit="s", utc=True).dt.tz_convert("Asia/Tehran")

    os.makedirs("data", exist_ok=True)
    path = f"data/{args.symbol}_{args.resolution}.csv"
    data.to_csv(path, index=False)

    expected = int((data.time.max() - data.time.min()) / step) + 1
    print("=" * 55)
    print(f"💾 ذخیره شد: {path}")
    print(f"🔹 بازه: {fmt(data.time.min())} تا {fmt(data.time.max())}")
    print(f"🔹 تعداد کندل: {len(data):,} از {expected:,} کندل ممکن "
          f"(پوشش {100 * len(data) / expected:.1f}%)")
    print("   کندل‌های غایب معمولاً یعنی در آن بازه معامله‌ای انجام نشده.")


if __name__ == "__main__":
    main()
