#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forecast_check.py - بازه پیش‌بینی ربات در گذشته چقدر درست بوده؟

روش (همان که advisor.price_range استفاده می‌کند):
  نوسان روزانه ۶۰ روز کاری گذشته را حساب کن، بازه ±۱ و ±۲ برابر آن را برای ۱ هفته و ۱ ماه بعد بساز.
  مرکز بازه قیمت امروز است (حدس جهت نمی‌زنیم).
این برنامه می‌شمارد قیمت واقعی بعداً چند درصد مواقع داخل بازه بوده. فقط از داده گذشته استفاده می‌شود.

اجرا:  python3 forecast_check.py
"""
import os

import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "GC_F_daily.csv")


def main():
    c = pd.read_csv(DATA, parse_dates=["date"]).set_index("date").close
    sig = np.log(c).diff().rolling(60).std()
    print(" How often the real price later fell inside the range (gold futures, 2006-now)")
    for h, name in ((5, "1 week"), (21, "1 month")):
        z = (np.log(c.shift(-h) / c) / (sig * np.sqrt(h))).dropna().loc["2006":]
        print(f" {name:8s}: inside 'likely' range {np.mean(z.abs() <= 1):.0%} (target 68%), "
              f"inside 'almost sure' range {np.mean(z.abs() <= 2):.0%} (target 95%), {len(z)} tests")


if __name__ == "__main__":
    main()
