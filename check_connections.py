#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_connections.py - بررسی این‌که سرور به همه جاهای لازم وصل می‌شود

روی سرور اجرا کنید:
  python3 ~/quant/check_connections.py          # همه چیز
  python3 ~/quant/check_connections.py --wallex # والکس را هم بررسی کن
  python3 ~/quant/check_connections.py --news   # همراه با یک تحلیل کامل اخبار با جیمینای (کمی طول می‌کشد)

برای هر مورد OK یا FAIL و دلیلش را چاپ می‌کند (انگلیسی، چون ترمینال فارسی را درست نشان نمی‌دهد):
  Telegram  - ربات تلگرام (با ~/.telegram_token اگر باشد)
  Bale      - ربات بله (فقط اگر ~/.bale_token باشد)
  Gold USD  - قیمت جهانی طلا به دلار برای ربات بله (فقط روی سرور ایران، یعنی اگر ~/.bale_token باشد)
  Wallex    - داده والکس (فقط با --wallex؛ ربات بله دیگر از والکس نمی‌خواند)
  Gemini    - جیمینای (با ~/.gemini_key)
  MetaTrader- گیرنده mt_bridge.py، آخرین قیمت، و داده روزانه
  MT5       - برنامه متاتریدر روی همین سرور (سرویس mt5)
"""
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)


def read(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except FileNotFoundError:
        return None


def show(name, ok, text):
    print(f" {'OK  ' if ok else 'FAIL'}  {name:<11} {text}")
    return ok


def check_bot(name, api, token_path, required):
    token = read(token_path)
    if not token:
        if required:
            try:
                requests.get(api.split("/bot")[0], timeout=10)
                return show(name, False, f"server reachable, but no token in {token_path} yet")
            except Exception as e:
                return show(name, False, f"cannot reach the server: {type(e).__name__}")
        return None
    try:
        d = requests.post(api.format(token=token, method="getMe"), timeout=15).json()
    except Exception as e:
        return show(name, False, f"cannot reach: {str(e).replace(token, '***')[:150]}")
    if not d.get("ok"):
        return show(name, False, f"token rejected: {d.get('description')}")
    return show(name, True, f"connected as @{d['result'].get('username')}")


def check_wallex():
    try:
        import advisor
        book = advisor.fetch_book()
    except (Exception, SystemExit) as e:
        return show("Wallex", False, f"no data ({str(e)[:120]}). Wallex buttons will not work from this server.")
    if not book:
        return show("Wallex", False, "empty order book. Wallex may block this server's country.")
    return show("Wallex", True, f"XAUTUSDT bid {book['bid']:.2f} / ask {book['ask']:.2f}")


def check_global():
    try:
        import global_gold
        spot = global_gold.spot_price()
        daily = global_gold.fetch_daily()
    except (Exception, SystemExit) as e:
        return show("Gold USD", False, f"no daily prices ({str(e)[:120]}). If this is the Iran server, run "
                                       "'bash foreign_setup.sh update' on the foreign server (relay/README.md).")
    if not spot:
        return show("Gold USD", False, f"daily OK ({len(daily)} days) but no live spot price; "
                                       "reports will use yesterday's close.")
    via = "tunnel" if global_gold._read(global_gold.BASE_URL_PATH) else "direct"
    return show("Gold USD", True, f"spot {spot:,.2f} USD, {len(daily)} daily prices ({via})")


def check_gemini(full):
    import news_ai
    key = read(news_ai.KEY_PATH)
    if not key:
        return show("Gemini", False, f"no key in {news_ai.KEY_PATH}")
    base = (read(news_ai.BASE_URL_PATH) or news_ai.DEFAULT_BASE).rstrip("/")
    model = read(news_ai.MODEL_PATH) or news_ai.DEFAULT_MODEL
    if read(news_ai.BASE_URL_PATH):
        print(f"       note: using {base} from {news_ai.BASE_URL_PATH} (needed only on the Iran server)")
    try:
        r = requests.get(f"{base}/v1beta/models/{model}", headers={"x-goog-api-key": key}, timeout=20)
    except Exception as e:
        return show("Gemini", False, f"cannot reach {base}: {type(e).__name__}")
    if r.status_code != 200:
        msg = ""
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        if "location" in msg.lower():
            msg += "  -> this server's country is blocked by Google"
        return show("Gemini", False, f"HTTP {r.status_code}: {msg[:200]}")
    if not full:
        return show("Gemini", True, f"model {model} available (run with --news for a full news test)")
    view = news_ai.advise("forecast", "(connection test: no bot report)")
    if view is None:
        return show("Gemini", False, "model found, but the news analysis failed (see message above)")
    return show("Gemini", True, f"news tone {view['tone']}, confidence {view['confidence']}, risk {view['risk']}, "
                                f"{view['seconds']} s")


def check_mt():
    import mt_bridge
    if not mt_bridge.read_secret():
        return show("MetaTrader", False, f"no secret in {mt_bridge.SECRET_PATH} (germany/setup.sh makes it)")
    try:
        requests.get(f"http://127.0.0.1:{mt_bridge.PORT}/", timeout=5)
    except Exception:
        return show("MetaTrader", False, "receiver is not running (systemctl status mt-bridge)")
    t = mt_bridge.latest()
    if t is None:
        return show("MetaTrader", False, "receiver is running, but nothing has arrived from MetaTrader yet")
    age = time.time() - t["received_at"]
    ok = show("MetaTrader", mt_bridge.is_fresh(t),
              f"{t['symbol']} bid {t['bid']} / ask {t['ask']}, last packet {age:.0f} s ago")
    try:
        days = len(mt_bridge.fetch_daily())
        show("MT daily", days >= 430, f"{days} days of daily prices (430+ needed for all signals)")
    except (Exception, SystemExit) as e:
        show("MT daily", False, f"{e}")
    return ok


def check_mt5_service():
    import subprocess
    try:
        r = subprocess.run(["systemctl", "is-active", "mt5"], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if r.stdout.strip() == "active":
        return show("MT5", True, "MetaTrader is running on this server (service mt5)")
    enabled = subprocess.run(["systemctl", "is-enabled", "mt5"], capture_output=True, text=True).stdout.strip()
    if enabled in ("enabled", "disabled"):
        return show("MT5", False, "service mt5 is not running (journalctl -u mt5 -n 30)")
    return None


def main():
    print("== Checking connections from this server...")
    results = [check_bot("Telegram", "https://api.telegram.org/bot{token}/{method}", "~/.telegram_token", True),
               check_bot("Bale", "https://tapi.bale.ai/bot{token}/{method}", "~/.bale_token", False),
               check_global() if read("~/.bale_token") else None,
               check_wallex() if "--wallex" in sys.argv[1:] else None,
               check_gemini("--news" in sys.argv[1:]), check_mt(), check_mt5_service()]
    bad = sum(r is False for r in results)
    print("== All good." if not bad else f"== {bad} problem(s). See the lines marked FAIL.")


if __name__ == "__main__":
    main()
