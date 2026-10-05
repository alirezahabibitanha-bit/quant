"""net_proxy.py - پروکسی محلی برای رسیدن به سایت‌های خارجی از سرور ایران (فقط جیمینای و قیمت جهانی طلا).

اگر فایل ~/.proxy_url باشد (مثلاً http://127.0.0.1:10809 که relay/xray_setup.sh می‌سازد)، news_ai و
global_gold اول از همین پروکسی می‌روند. بقیه درخواست‌ها (بله، تلگرام، والکس) هیچ‌وقت از آن رد نمی‌شوند،
چون پروکسی فقط به درخواست‌هایی داده می‌شود که خودشان proxies() را بخواهند.
روی سرور آلمان این فایل نباید باشد.
"""
import os

PROXY_PATH = os.path.expanduser("~/.proxy_url")


def proxy_url():
    try:
        with open(PROXY_PATH, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except FileNotFoundError:
        return None


def proxies():
    """برای requests: {'http','https'} یا None اگر پروکسی تنظیم نشده."""
    url = proxy_url()
    return {"http": url, "https": url} if url else None
