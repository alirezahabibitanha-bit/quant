#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forecast_models.py - آیا مدل‌های نوسانِ پیشنهادشده در گزارش تحقیقاتی، بازه پیش‌بینی ربات را بهتر می‌کنند؟

پیش‌زمینه: گزارش تحقیقاتی می‌گوید جهت قیمت طلا قابل پیش‌بینی نیست ولی «اندازه تکان‌ها» (نوسان) هست،
و برای آن مدل‌های GARCH و HAR را پیشنهاد می‌کند. ربات الان بازه را با نوسان ساده ۶۰ روز کاری اخیر می‌سازد.

بخش ۱ - بازه ۱ هفته و ۱ ماه (داده روزانه GC=F از ۲۰۰۵):
  CURRENT : انحراف معیار ۶۰ روز کاری اخیر (همان که ربات الان استفاده می‌کند)
  EWMA    : میانگین وزنی نمایی مربع بازده‌ها، لامبدا ۰.۹۴ (استاندارد RiskMetrics، بدون تنظیم)
  GARCH   : GARCH(1,1) بولرسلف ۱۹۸۶، پارامترها هر سال فقط با داده گذشته دوباره تخمین زده می‌شوند
  HAR     : HAR کورسی ۲۰۰۹ (نوسان دیروز، هفته و ماه گذشته)، رگرسیون مستقیم برای هر افق،
            هر سال فقط با داده گذشته دوباره تخمین زده می‌شود
بخش ۲ - بازه ۲ دقیقه (داده ۲ دقیقه‌ای GC=F):
  EWMA     : همان که ربات الان استفاده می‌کند
  EWMA+HOUR: همان، ولی «اثر ساعت روز» حذف و دوباره اضافه می‌شود (ساعت‌های شلوغ لندن/نیویورک
             پرنوسان‌ترند). ضریب هر ساعت فقط از روزهای گذشته ساخته می‌شود.

معیار مقایسه: QLIKE (پاتون ۲۰۱۱) = r²/F + ln F  (هر چه کمتر، بهتر)؛ F = واریانس پیش‌بینی‌شده،
r = بازده واقعی بعدی. و درصد مواقعی که قیمت واقعی داخل بازه ±۱ و ±۲ بوده.

قانون برد (پیش از اجرا تعیین شد): مدل جدید فقط وقتی جای مدل فعلی را در ربات می‌گیرد که
  ۱. QLIKE آن در هر سه دوره (2006-2012، 2013-2019، 2020 تا حالا) و برای هر دو افق از CURRENT کمتر باشد
     (برای ۲ دقیقه: در هر دو نیمه داده)،
  ۲. آزمون دیبولد-ماریانو روی کل دوره معنادار باشد (|t| > 1.96)،
  ۳. درستی بازه‌اش در هر دوره قابل قبول بماند: ±۱ بین ۶۳ و ۷۳٪، ±۲ بین ۹۲ و ۹۷٪.

هیچ محاسبه‌ای از داده آینده استفاده نمی‌کند. اجرا:  python3 forecast_models.py
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY = os.path.join(HERE, "data", "GC_F_daily.csv")
TWO_MIN = os.path.join(HERE, "data", "GC_F_2m.csv")
REPORT = os.path.join(HERE, "forecast_models_report.md")
PERIODS = [("2006-2012", "2006", "2012"), ("2013-2019", "2013", "2019"), ("2020-now", "2020", None)]
HORIZONS = ((5, "1 week"), (21, "1 month"))
COVER_OK = ((0.63, 0.73), (0.92, 0.97))


# ---------------------------------------------------------------- مدل‌ها (روزانه)

def ewma_var(r, lam=0.94):
    """واریانس یک روز بعد، با اطلاعات تا امروز."""
    out = np.empty(len(r))
    v = np.nanmean(r[:30] ** 2)
    for i, x in enumerate(r):
        v = lam * v + (1 - lam) * x * x
        out[i] = v
    return out


def garch_filter(r, a, b, v_long):
    """واریانس یک روز بعد در GARCH(1,1) با هدف‌گیری واریانس (omega = V(1-a-b))."""
    w = v_long * (1 - a - b)
    out = np.empty(len(r))
    v = v_long
    for i, x in enumerate(r):
        v = w + a * x * x + b * v
        out[i] = v
    return out


def garch_fit(r):
    """تخمین a و b با جست‌وجوی شبکه‌ای روی درست‌نمایی (فقط numpy)."""
    v_long = r.var()
    best, ll_best = (0.05, 0.9), -np.inf
    for a in np.arange(0.02, 0.161, 0.01):
        for b in np.arange(0.78, 0.991, 0.01):
            if a + b >= 0.999:
                continue
            v = np.r_[v_long, garch_filter(r[:-1], a, b, v_long)]
            ll = -0.5 * np.sum(np.log(v) + r * r / v)
            if ll > ll_best:
                best, ll_best = (a, b), ll
    return best, v_long


def garch_h(v1, a, b, v_long, h):
    """جمع واریانس h روز بعد از روی واریانس روز بعد."""
    p = a + b
    return h * v_long + (v1 - v_long) * (1 - p ** h) / (1 - p)


def har_features(r2):
    s = pd.Series(r2)
    return np.c_[np.ones(len(s)), s, s.rolling(5).mean(), s.rolling(22).mean()]


def forecasts(r, dates, h):
    """واریانس پیش‌بینی‌شده جمع بازده h روز بعد، برای هر مدل. سطر i = پیش‌بینی در پایان روز i."""
    n = len(r)
    r2 = r * r
    out = {"CURRENT": pd.Series(r).rolling(60).std().values ** 2 * h,
           "EWMA": ewma_var(r) * h}
    X = har_features(r2)
    y = pd.Series(r2).rolling(h).sum().shift(-h).values          # مقدار واقعی h روز بعد (فقط برای آموزش)
    garch, har = np.full(n, np.nan), np.full(n, np.nan)
    years = pd.DatetimeIndex(dates).year
    for yr in range(years[0] + 1, years[-1] + 1):
        idx = np.where(years == yr)[0]
        start = idx[0]
        past = slice(0, start)                                   # فقط داده قبل از این سال
        (a, b), vl = garch_fit(r[past])
        v1 = garch_filter(r[: idx[-1] + 1], a, b, vl)[idx]
        garch[idx] = garch_h(v1, a, b, vl, h)
        train = np.arange(22, start - h)                         # برچسب آموزش هم باید قبل از این سال تمام شده باشد
        train = train[~np.isnan(X[train]).any(1)]
        coef = np.linalg.lstsq(X[train], y[train], rcond=None)[0]
        har[idx] = X[idx] @ coef
    floor = np.nanmin(r2[r2 > 0]) * h
    out["GARCH"] = garch
    out["HAR"] = np.maximum(har, floor)                          # رگرسیون خطی ممکن است منفی شود
    return out


# ---------------------------------------------------------------- معیارها

def qlike(real_sq, f):
    return real_sq / f + np.log(f)


def dm_stat(loss_new, loss_old, lag):
    d = np.asarray(loss_new - loss_old)
    mean = d.mean()
    c = d - mean
    n = len(c)
    lrv = c @ c / n
    for k in range(1, lag + 1):
        lrv += 2 * (1 - k / (lag + 1)) * (c[k:] @ c[:-k]) / n
    return mean / np.sqrt(lrv / n)


def daily_section():
    c = pd.read_csv(DAILY, parse_dates=["date"]).set_index("date").close
    r = np.log(c).diff().dropna()
    dates, rv = r.index, r.values
    rows, verdict = [], {}
    for h, hname in HORIZONS:
        f = forecasts(rv, dates, h)
        real = pd.Series(rv).rolling(h).sum().shift(-h).values           # بازده واقعی h روز بعد
        real_sq = pd.Series(rv ** 2).rolling(h).sum().shift(-h).values   # واریانس تحقق‌یافته h روز بعد
        df = pd.DataFrame({"real": real, "rsq": real_sq, **f}, index=dates).loc["2006":].dropna()
        for m in f:
            z = df.real / np.sqrt(df[m])
            loss = qlike(df.rsq, df[m])
            for pname, a, b in PERIODS + [("FULL", "2006", None)]:
                sel = slice(a, b)
                rows.append({"horizon": hname, "model": m, "period": pname,
                             "qlike": loss.loc[sel].mean(),
                             "in_1": (z.loc[sel].abs() <= 1).mean(), "in_2": (z.loc[sel].abs() <= 2).mean(),
                             "n": len(z.loc[sel])})
            if m != "CURRENT":
                verdict.setdefault(m, []).append(dm_stat(loss, qlike(df.rsq, df.CURRENT), h - 1))
    res = pd.DataFrame(rows)
    wins = {}
    for m in ("EWMA", "GARCH", "HAR"):
        why = []
        for hname in dict(HORIZONS).values():
            for pname, _, _ in PERIODS:
                sel = res[(res.horizon == hname) & (res.period == pname)].set_index("model")
                new, old = sel.loc[m], sel.loc["CURRENT"]
                if new.qlike >= old.qlike:
                    why.append(f"worse QLIKE {hname} {pname}")
                if not (COVER_OK[0][0] <= new.in_1 <= COVER_OK[0][1]):
                    why.append(f"+-1 coverage {new.in_1:.1%} {hname} {pname}")
                if not (COVER_OK[1][0] <= new.in_2 <= COVER_OK[1][1]):
                    why.append(f"+-2 coverage {new.in_2:.1%} {hname} {pname}")
        why += [f"DM not significant {h}" for t, (_, h) in zip(verdict[m], HORIZONS) if t >= -1.96]
        wins[m] = (not why, verdict[m], why)
    return res, wins


# ---------------------------------------------------------------- بخش ۲: ۲ دقیقه

def two_min_section():
    df = pd.read_csv(TWO_MIN, parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    step = pd.Timedelta(minutes=2)
    lr = np.log(df.close).diff()
    lr[df.time.diff() != step] = np.nan                     # بازده روی فاصله تعطیلی حساب نشود
    hour, day = df.time.dt.hour, df.time.dt.normalize()

    # ضریب هر ساعت: میانگین مربع بازده آن ساعت تقسیم بر میانگین کل، فقط از روزهای قبل
    sq = lr ** 2
    by = pd.DataFrame({"day": day, "hour": hour, "sq": sq}).dropna()
    daily_hour = by.groupby(["day", "hour"]).sq.agg(["sum", "count"]).unstack("hour")
    cum_sum = daily_hour["sum"].fillna(0).cumsum().shift(1)
    cum_cnt = daily_hour["count"].fillna(0).cumsum().shift(1)
    hour_mean = cum_sum / cum_cnt
    tot = cum_sum.sum(axis=1) / cum_cnt.sum(axis=1)
    factor = hour_mean.div(tot, axis=0)                      # سطر = روز، ستون = ساعت
    fac = np.array([factor.at[d, h] if (d in factor.index and h in factor.columns) else np.nan
                    for d, h in zip(day, hour)])
    nxt_fac = pd.Series(fac).shift(-1).values                # ضریب ساعتِ کندل بعدی (ساعت از قبل معلوم است)

    r = lr.fillna(0).values
    plain = ewma_var(r, 0.94)
    des = np.where(np.isfinite(fac) & (fac > 0), r / np.sqrt(np.where(fac > 0, fac, 1)), r)
    seasonal = ewma_var(des, 0.94) * nxt_fac

    fut = lr.shift(-1)
    t = pd.DataFrame({"time": df.time, "hour": hour, "fut": fut, "EWMA": plain, "EWMA+HOUR": seasonal})
    first_day = day.iloc[0] + pd.Timedelta(days=7)           # هفته اول فقط برای ساختن ضرایب
    t = t[(df.time >= first_day)].dropna()
    t = t[(t.EWMA > 0) & (t["EWMA+HOUR"] > 0)]
    half = t.time.iloc[len(t) // 2]
    out = []
    for m in ("EWMA", "EWMA+HOUR"):
        z = t.fut / np.sqrt(t[m])
        loss = qlike(t.fut ** 2, t[m])
        busy = t.hour.between(12, 16)                        # ۱۲ تا ۱۶ UTC ≈ ۱۵:۳۰ تا ۱۹:۳۰ تهران
        out.append({"model": m, "q1": loss[t.time < half].mean(), "q2": loss[t.time >= half].mean(),
                    "in_1": (z.abs() <= 1).mean(), "in_2": (z.abs() <= 2).mean(),
                    "busy_in_1": (z[busy].abs() <= 1).mean(), "quiet_in_1": (z[~busy].abs() <= 1).mean(),
                    "loss": loss})
    dm = dm_stat(out[1]["loss"].values, out[0]["loss"].values, 0)
    a, b = out
    ok = (b["q1"] < a["q1"] and b["q2"] < a["q2"] and dm < -1.96
          and COVER_OK[0][0] <= b["in_1"] <= COVER_OK[0][1] and COVER_OK[1][0] <= b["in_2"] <= COVER_OK[1][1])
    return out, dm, ok, len(t), (t.time.iloc[0], t.time.iloc[-1])


# ---------------------------------------------------------------- برای ربات: ضریب ساعت (آزمایشی)

HOUR_FACTOR_LIMITS = (0.25, 4.0)   # ساعت تعطیلی بورس آمریکا تقریباً صفر درمی‌آید ولی والکس آن موقع هم باز است


def hour_factors(path=TWO_MIN):
    """شلوغی هر ساعت (به وقت جهانی UTC) نسبت به میانگین، از داده ۲ دقیقه‌ای طلای جهانی. {ساعت: ضریب}"""
    df = pd.read_csv(path, parse_dates=["time"]).sort_values("time")
    lr = np.log(df.close).diff()
    lr[df.time.diff() != pd.Timedelta(minutes=2)] = np.nan
    sq = lr ** 2
    f = (sq.groupby(df.time.dt.hour).mean() / sq.mean()).clip(*HOUR_FACTOR_LIMITS)
    return {h: float(f.get(h, 1.0)) for h in range(24)}


def gap_returns(close, times):
    """بازده هر کندل ۲ دقیقه‌ای. اگر چند کندل بی‌معامله وسطش افتاده باشد، بازده بر رادیکال
    تعداد بازه‌ها تقسیم می‌شود تا یک «پرش» چنددقیقه‌ای، نوسان ۲ دقیقه را بزرگ نشان ندهد."""
    t = pd.DatetimeIndex(times)
    steps = np.r_[1.0, np.diff(t.asi8) / 120e9]
    r = np.r_[0.0, np.diff(np.log(np.asarray(close, float)))]
    return r / np.sqrt(np.maximum(steps, 1.0))


def two_min_sigma(close, times, lam=0.94):
    """نوسان کندل ۲ دقیقه‌ای بعد با EWMA (همان مدل آزمایش‌شده در backtest_2min.py)."""
    return float(np.sqrt(ewma_var(gap_returns(close, times), lam)[-1]))


def two_min_sigma_hour(close, times, factors, lam=0.94):
    """همان، ولی با حذف و اضافه کردن اثر ساعت روز. times به وقت UTC."""
    hours = pd.DatetimeIndex(times).hour
    fac = np.array([factors.get(h, 1.0) for h in hours])
    r = gap_returns(close, times) / np.sqrt(fac)
    nxt = pd.Timestamp(times[-1]) + pd.Timedelta(minutes=2)
    return float(np.sqrt(ewma_var(r, lam)[-1] * factors.get(nxt.hour, 1.0)))


def two_min_abs_z(path=TWO_MIN):
    """خطاهای واقعی گذشته مدل EWMA روی طلای جهانی: |بازده بعدی / نوسان پیش‌بینی‌شده|، مرتب‌شده.
    بازه «۸۰٪» و «۹۵٪» از چندک‌های همین ساخته می‌شود، نه از فرض توزیع نرمال (دم‌های پهن)."""
    df = pd.read_csv(path, parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    lr = np.log(df.close).diff()
    sig = np.sqrt(ewma_var(lr.fillna(0).values))
    ok = (df.time.shift(-1) - df.time == pd.Timedelta(minutes=2)) & (sig > 0)
    z = (lr.shift(-1) / sig)[ok].dropna().iloc[100:]
    return np.sort(z.abs().values)


# ---------------------------------------------------------------- گزارش

FA_MODEL = {"CURRENT": "فعلی ربات (نوسان ساده ۶۰ روزه)", "EWMA": "EWMA", "GARCH": "GARCH(1,1)", "HAR": "HAR",
            "EWMA+HOUR": "EWMA + اثر ساعت روز"}


def main():
    res, wins = daily_section()
    print("=" * 96)
    print(" PART 1: 1-week and 1-month range (gold futures, daily). QLIKE: lower is better.")
    print("=" * 96)
    print(f" {'horizon':8s} {'period':10s} {'model':8s} {'QLIKE':>8s} {'in +-1':>7s} {'in +-2':>7s} {'tests':>6s}")
    for _, x in res.iterrows():
        print(f" {x.horizon:8s} {x.period:10s} {x.model:8s} {x.qlike:8.3f} {x.in_1:7.0%} {x.in_2:7.0%} {x.n:6d}")
    print("-" * 96)
    for m, (ok, ts, why) in wins.items():
        print(f" {m:6s}: Diebold-Mariano t vs CURRENT = {', '.join(f'{t:+.2f}' for t in ts)} (1w, 1m)"
              f"  -> {'WINS (replace current)' if ok else 'fails: ' + '; '.join(why)}")

    two, dm, ok2, n2, (t0, t1) = two_min_section()
    print("=" * 96)
    print(f" PART 2: 2-minute range ({t0:%Y-%m-%d} to {t1:%Y-%m-%d}, {n2} tests)")
    print("=" * 96)
    for x in two:
        print(f" {x['model']:10s} QLIKE 1st half {x['q1']:8.3f}  2nd half {x['q2']:8.3f}  in +-1 {x['in_1']:.0%}"
              f"  in +-2 {x['in_2']:.0%}  busy hours +-1 {x['busy_in_1']:.0%}  quiet hours +-1 {x['quiet_in_1']:.0%}")
    print(f" Diebold-Mariano t (EWMA+HOUR vs EWMA) = {dm:+.2f} -> {'WINS' if ok2 else 'does not pass the rule'}")
    print("=" * 96)

    L = ["# آیا مدل‌های نوسانِ گزارش تحقیقاتی، بازه پیش‌بینی ربات را بهتر می‌کنند؟", "",
         "- QLIKE: معیار استاندارد خطای پیش‌بینی نوسان؛ هر چه کمتر، بهتر.",
         "- «داخل ±۱»: چند درصد مواقع قیمت واقعی داخل بازه «به احتمال زیاد» بود (هدف ۶۸٪)؛ «داخل ±۲» هدف ۹۵٪.",
         "- قانون برد (از قبل): QLIKE کمتر از روش فعلی در هر سه دوره و هر دو افق، آزمون دیبولد-ماریانو معنادار، "
         "و درستی بازه قابل قبول (±۱ بین ۶۳ و ۷۳٪، ±۲ بین ۹۲ و ۹۷٪).", "",
         "## بخش ۱: بازه ۱ هفته و ۱ ماه (طلای جهانی، روزانه از ۲۰۰۶)", "",
         "| افق | دوره | مدل | QLIKE | داخل ±۱ | داخل ±۲ |", "|---|---|---|---|---|---|"]
    for _, x in res.iterrows():
        L.append(f"| {'۱ هفته' if x.horizon == '1 week' else '۱ ماه'} | {x.period} | {FA_MODEL[x.model]} | "
                 f"{x.qlike:.3f} | {x.in_1:.0%} | {x.in_2:.0%} |")
    L += ["", "| مدل | آماره دیبولد-ماریانو (۱ هفته، ۱ ماه) | نتیجه |", "|---|---|---|"]
    for m, (ok, ts, why) in wins.items():
        L.append(f"| {FA_MODEL[m]} | {', '.join(f'{t:+.2f}' for t in ts)} | "
                 f"{'برد' if ok else 'قبول نشد: ' + '؛ '.join(why)} |")
    L += ["", "(آماره منفی یعنی مدل جدید خطای کمتری داشته؛ کمتر از ‎-1.96 یعنی معنادار.)", "",
          f"## بخش ۲: بازه ۲ دقیقه ({t0:%Y-%m-%d} تا {t1:%Y-%m-%d}، {n2} آزمایش)", "",
          "| مدل | QLIKE نیمه اول | QLIKE نیمه دوم | داخل ±۱ | داخل ±۲ | ±۱ در ساعت‌های شلوغ | ±۱ در ساعت‌های آرام |",
          "|---|---|---|---|---|---|---|"]
    for x in two:
        L.append(f"| {FA_MODEL[x['model']]} | {x['q1']:.3f} | {x['q2']:.3f} | {x['in_1']:.0%} | {x['in_2']:.0%} | "
                 f"{x['busy_in_1']:.0%} | {x['quiet_in_1']:.0%} |")
    L += ["", f"آماره دیبولد-ماریانو: {dm:+.2f} — {'برد' if ok2 else 'قبول نشد'}", "", "## نتیجه", ""]
    winners = [FA_MODEL[m] for m, (ok, _, _) in wins.items() if ok] + (["EWMA + اثر ساعت روز"] if ok2 else [])
    L.append(f"مدل‌هایی که طبق قانون برد قبول شدند: {'، '.join(winners)}." if winners else
             "هیچ مدلی طبق قانون برد قبول نشد؛ ربات همان روش ساده فعلی را نگه می‌دارد. "
             "مدلی که خطای کمتری داشت ولی فقط به‌خاطر پهن بودن بازه در یک دوره رد شد، در ربات «در سایه» "
             "ثبت می‌شود (forecast_shadow_log.csv) تا روی داده والکس سنجیده شود.")
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f" Persian report written to {os.path.basename(REPORT)}")


if __name__ == "__main__":
    main()
