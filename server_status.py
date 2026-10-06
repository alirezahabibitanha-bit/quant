#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server_status.py - مانیتورینگ خیلی سبک سرور، بدون نصب هیچ برنامه‌ای

فقط چند فایل سیستمی لینوکس (/proc) را می‌خواند؛ هیچ برنامه همیشه‌روشنی اضافه نمی‌کند و رمی نمی‌گیرد.
در ربات: دکمه «🖥 وضعیت سرور» (فقط صاحب ربات)، و هشدار خودکار به صاحب ربات وقتی:
  - رم و Swap با هم تقریباً پر شود (همان چیزی که قبلاً سرور را قفل کرد)
  - دیسک تقریباً پر شود
  - یکی از سرویس‌های نصب‌شده (مثلاً xray) از کار بیفتد
  - ربات تازه روشن شده باشد (مثلاً بعد از ریستارت سرور): «سرور و ربات بالا آمدند»

اجرا در ترمینال (همان گزارش، انگلیسی):
  python3 server_status.py
"""
import os
import shutil
import subprocess
import time

SERVICES = ["bale-bot", "telegram-bot", "xray", "gemini-tunnel", "mt-bridge", "mt5"]
CHECK_SECONDS = 5 * 60          # هر ۵ دقیقه چک
ALERT_REPEAT_SECONDS = 3 * 3600  # همان هشدار حداکثر هر ۳ ساعت یک بار
MEM_ALERT_PCT = 90               # رم + Swap با هم
DISK_ALERT_PCT = 90


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


def memory():
    """رم و Swap به مگابایت: (رم کل، رم مصرفی، Swap کل، Swap مصرفی)."""
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            info[parts[0].rstrip(":")] = int(parts[1]) / 1024
    total = info.get("MemTotal", 0)
    used = total - info.get("MemAvailable", info.get("MemFree", 0))
    s_total = info.get("SwapTotal", 0)
    s_used = s_total - info.get("SwapFree", 0)
    return total, used, s_total, s_used


def cpu_load():
    """میانگین بار ۱ و ۱۵ دقیقه، تقسیم بر تعداد هسته‌ها (۱ = پردازنده کامل مشغول)."""
    parts = _read("/proc/loadavg").split()
    cores = os.cpu_count() or 1
    if len(parts) < 3:
        return None, None
    return float(parts[0]) / cores, float(parts[2]) / cores


def uptime_seconds():
    parts = _read("/proc/uptime").split()
    return float(parts[0]) if parts else None


def disk():
    """دیسک اصلی به گیگابایت: (کل، مصرفی)."""
    d = shutil.disk_usage("/")
    return d.total / 1024 ** 3, d.used / 1024 ** 3


def top_processes(n=3):
    """n برنامه‌ای که بیشترین رم را گرفته‌اند: [(اسم، مگابایت)]."""
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        name, rss = None, 0
        for line in _read(f"/proc/{pid}/status").splitlines():
            if line.startswith("Name:"):
                name = line.split(None, 1)[1].strip() if len(line.split()) > 1 else "?"
            elif line.startswith("VmRSS:"):
                rss = int(line.split()[1]) / 1024
        if name and rss:
            # برای پایتون اسم فایل برنامه مهم‌تر است (bale_bot.py و ...)
            if name.startswith("python"):
                args = _read(f"/proc/{pid}/cmdline").split("\0")
                script = next((os.path.basename(a) for a in args[1:] if a.endswith(".py")), None)
                name = script or name
            out.append((name, rss))
    return sorted(out, key=lambda x: -x[1])[:n]


def services():
    """وضعیت سرویس‌هایی که روی همین سرور فعال (enabled) شده‌اند: {اسم: درست کار می‌کند؟}"""
    result = {}
    for svc in SERVICES:
        try:
            enabled = subprocess.run(["systemctl", "is-enabled", "--quiet", svc], timeout=5).returncode == 0
            if not enabled:
                continue
            active = subprocess.run(["systemctl", "is-active", "--quiet", svc], timeout=5).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return {}      # systemctl نیست (مثلاً روی کامپیوتر خودتان)
        result[svc] = active
    return result


def _fa(x, decimals=0):
    text = f"{abs(x):,.{decimals}f}"
    return text.translate(str.maketrans("0123456789,.", "۰۱۲۳۴۵۶۷۸۹٬٫"))


def _duration_fa(sec):
    d, h, m = int(sec // 86400), int(sec % 86400 // 3600), int(sec % 3600 // 60)
    if d:
        return f"{_fa(d)} روز و {_fa(h)} ساعت"
    if h:
        return f"{_fa(h)} ساعت و {_fa(m)} دقیقه"
    return f"{_fa(m)} دقیقه"


def _light(pct, warn=70, bad=90):
    return "🔴" if pct >= bad else "🟡" if pct >= warn else "🟢"


def status_text():
    """گزارش فارسی کوتاه برای دکمه «🖥 وضعیت سرور»."""
    total, used, s_total, s_used = memory()
    load1, load15 = cpu_load()
    up = uptime_seconds()
    d_total, d_used = disk()
    L = ["🖥 وضعیت سرور", ""]
    if up is not None:
        L.append(f"⏱ روشن از {_duration_fa(up)} پیش")
    if load1 is not None:
        L.append(f"{_light(load1 * 100)} پردازنده: الان {_fa(load1 * 100)}٪ مشغول "
                 f"(میانگین ۱۵ دقیقه {_fa(load15 * 100)}٪)")
    if total:
        pct = 100 * used / total
        L.append(f"{_light(pct, 80, 95)} رم: {_fa(used)} از {_fa(total)} مگ ({_fa(pct)}٪)")
    if s_total:
        pct = 100 * s_used / s_total
        L.append(f"{_light(pct, 50, 80)} Swap: {_fa(s_used)} از {_fa(s_total)} مگ ({_fa(pct)}٪)")
    else:
        L.append("⚪️ Swap: ندارد")
    pct = 100 * d_used / d_total
    L.append(f"{_light(pct, 80, DISK_ALERT_PCT)} دیسک: {_fa(d_used, 1)} از {_fa(d_total, 1)} گیگ ({_fa(pct)}٪)")
    top = top_processes()
    if top:
        L += ["", "پررم‌ترین برنامه‌ها:"] + [f"• {name}: {_fa(mb)} مگ" for name, mb in top]
    svc = services()
    if svc:
        L += ["", "سرویس‌ها:"] + [f"{'✅' if ok else '❌'} {name}" for name, ok in svc.items()]
    L += ["", "🟢 عادی  🟡 بالا  🔴 خطرناک. پردازنده بالای ۱۰۰٪ یعنی کارها در صف‌اند."]
    return "\n".join(L)


class Watcher:
    """هر ۵ دقیقه سرور را چک می‌کند و مشکل را (بدون تکرار زیاد) به صاحب ربات خبر می‌دهد."""

    def __init__(self):
        self.last_check = 0.0
        self.last_sent = {}      # نوع هشدار -> زمان آخرین ارسال
        self.started = False

    def startup_text(self):
        """یک بار، بعد از روشن شدن ربات."""
        if self.started:
            return None
        self.started = True
        up = uptime_seconds()
        if up is not None and up < 15 * 60:
            return f"🟢 سرور دوباره بالا آمد (حدود {_duration_fa(up)} پیش) و ربات روشن است."
        return None

    def due(self):
        return time.time() - self.last_check >= CHECK_SECONDS

    def check(self):
        """فهرست پیام‌های هشدار تازه (ممکن است خالی باشد)."""
        self.last_check = time.time()
        problems = {}
        total, used, s_total, s_used = memory()
        if total:
            pct = 100 * (used + s_used) / (total + s_total)
            if pct >= MEM_ALERT_PCT:
                problems["mem"] = (f"⚠️ رم و Swap سرور با هم {_fa(pct)}٪ پر شده‌اند. "
                                   "اگر به ۱۰۰٪ برسد سرور ممکن است قفل شود.")
        d_total, d_used = disk()
        pct = 100 * d_used / d_total
        if pct >= DISK_ALERT_PCT:
            problems["disk"] = f"⚠️ دیسک سرور {_fa(pct)}٪ پر است."
        for name, ok in services().items():
            if not ok:
                problems["svc:" + name] = (f"❌ سرویس {name} کار نمی‌کند.\n"
                                           f"روی سرور: systemctl restart {name}")
        now = time.time()
        out = []
        for key, text in problems.items():
            if now - self.last_sent.get(key, 0) >= ALERT_REPEAT_SECONDS:
                self.last_sent[key] = now
                out.append(text + "\n(جزئیات: دکمه «🖥 وضعیت سرور»)")
        for key in list(self.last_sent):      # مشکل حل شد: دفعه بعد دوباره خبر بده
            if key not in problems:
                del self.last_sent[key]
        return out


if __name__ == "__main__":
    total, used, s_total, s_used = memory()
    load1, load15 = cpu_load()
    d_total, d_used = disk()
    up = uptime_seconds() or 0
    print(f"Uptime:  {up / 3600:.1f} hours")
    if load1 is not None:
        print(f"CPU:     {load1 * 100:.0f}% now, {load15 * 100:.0f}% (15 min avg)")
    print(f"RAM:     {used:.0f} / {total:.0f} MB")
    print(f"Swap:    {s_used:.0f} / {s_total:.0f} MB")
    print(f"Disk:    {d_used:.1f} / {d_total:.1f} GB")
    print("Top RAM: " + ", ".join(f"{n} {mb:.0f} MB" for n, mb in top_processes()))
    for name, ok in services().items():
        print(f"Service {name}: {'running' if ok else 'NOT RUNNING'}")
