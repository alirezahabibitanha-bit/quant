#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
تحلیل اولیه داده‌های تاریخی: آیا اصلاً جایی برای ربات نوسان‌گیر هست؟

سه سؤال را جواب می‌دهد:
  ۱. حرکت قیمت در افق‌های مختلف (۱ دقیقه تا ۱ روز) چقدر است، در مقایسه با هزینه معامله؟
  ۲. بازار در هر افق «برگشتی» است (حلقه تعادلی) یا «روندی» (حلقه تقویتی)؟
  ۳. کدام ساعت‌های روز پرنوسان‌ترند؟

نمونه اجرا:
    python3 analyze.py data/USDTTMN_1.csv --fee 0.0035
"""
import argparse

import numpy as np
import pandas as pd


def label(minutes):
    if minutes < 60:
        return f"{minutes} دقیقه"
    if minutes < 1440:
        return f"{minutes // 60} ساعت"
    return f"{minutes // 1440} روز"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("csv")
    p.add_argument("--fee", type=float, default=0.0035,
                   help="کارمزد یک طرف معامله؛ 0.0035 یعنی 0.35 درصد")
    args = p.parse_args()

    df = pd.read_csv(args.csv).sort_values("time").drop_duplicates("time")
    step = int(df.time.diff().median())                       # طول کندل به ثانیه
    df.index = pd.to_datetime(df.time.values, unit="s", utc=True).tz_convert("Asia/Tehran")

    # شبکه کامل زمانی تا دقیقه‌های بدون معامله هم دیده شوند
    full = df.reindex(pd.date_range(df.index[0], df.index[-1], freq=pd.Timedelta(seconds=step)))
    missing = full.close.isna().mean() * 100
    close = full.close.ffill()
    cost = 2 * args.fee                                       # هزینه رفت و برگشت (بدون اسپرد)

    print("=" * 70)
    print(f"📊 فایل: {args.csv}")
    print(f"🔹 بازه: {df.index[0]:%Y-%m-%d %H:%M} تا {df.index[-1]:%Y-%m-%d %H:%M}")
    print(f"🔹 طول کندل: {step // 60} دقیقه | کندل‌های بدون معامله: {missing:.1f}%")
    print(f"🔹 هزینه رفت و برگشت فرض‌شده: {cost * 100:.2f}% (+ اسپرد که در کندل دیده نمی‌شود)")
    print(f"🔹 تغییر کل قیمت در این بازه: {(close.iloc[-1] / close.iloc[0] - 1) * 100:+.2f}%")

    base_min = step // 60
    r1 = np.log(close).diff().dropna()
    var1 = r1.var()

    rows = []
    for h in [1, 5, 15, 60, 240, 1440]:
        if h < base_min:
            continue
        k = h // base_min
        r = np.log(close.iloc[::k]).diff().dropna()
        if len(r) < 30:
            continue
        a = r.abs()
        vr = r.var() / (k * var1) if k > 1 else 1.0
        rows.append({
            "افق": label(h),
            "میانگین|حرکت|%": round(a.mean() * 100, 3),
            "میانه%": round(a.median() * 100, 3),
            "صدک۹۰%": round(a.quantile(0.9) * 100, 3),
            "سهم>هزینه%": round((a > cost).mean() * 100, 1),
            "خودهمبستگی": round(r.autocorr(1), 3),
            "نسبت‌واریانس": round(vr, 2),
        })

    print("\n۱) اندازه حرکت قیمت در هر افق، در مقایسه با هزینه معامله:")
    print(pd.DataFrame(rows).to_string(index=False))

    print("\nراهنمای خواندن جدول:")
    print(f"  • «سهم>هزینه» = چند درصد از حرکت‌ها از {cost * 100:.2f}% بزرگ‌تر بوده‌اند.")
    print("    اگر این عدد در یک افق خیلی کم است، نوسان‌گیری در آن افق عملاً ممکن نیست.")
    print("  • «خودهمبستگی» منفی = بعد از حرکت، قیمت تمایل به برگشت دارد (حلقه تعادلی).")
    print("    مثبت = حرکت ادامه پیدا می‌کند (حلقه تقویتی). نزدیک صفر = تصادفی.")
    print("  • «نسبت‌واریانس» کمتر از ۱ = برگشتی، بیشتر از ۱ = روندی، حدود ۱ = گام تصادفی.")

    hourly = close.resample("60min").last()
    hr = np.log(hourly).diff().abs().dropna()
    by_hour = (hr.groupby(hr.index.hour).mean() * 100).round(3)
    print("\n۲) میانگین حرکت ساعتی بر اساس ساعت روز (به وقت تهران)، ۵ ساعت پرنوسان:")
    print(by_hour.sort_values(ascending=False).head(5).to_string())

    print("=" * 70)
    print("⚠️ این تحلیل فقط توصیف گذشته است، نه پیش‌بینی. قدم بعد: تست یک استراتژی مشخص.")


if __name__ == "__main__":
    main()
