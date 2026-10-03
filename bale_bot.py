#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bale_bot.py - مشاور طلا و تتر روی پیام‌رسان «بله»

همان سؤال‌ها و همان گزارش advisor.py را در بله می‌دهد. هیچ خرید و فروشی انجام نمی‌دهد.

امنیت:
  - کلید ربات (توکن) در کد نیست. در فایل ~/.bale_token روی سرور نگه داشته می‌شود.
  - فقط کسانی جواب می‌گیرند که شناسه‌شان در فایل ~/.bale_allowed است (هر خط یک شناسه).
    خط اول = صاحب ربات. آدم جدید که پیام بدهد، برای صاحب ربات پیامی با دکمه «تأیید» و «رد» می‌آید.
    تأیید = شناسه خودکار به فایل اضافه می‌شود. رد = شناسه در ~/.bale_denied می‌رود و دیگر خبری نمی‌آید.
    فایل با هر پیام دوباره خوانده می‌شود؛ بعد از اضافه کردن شناسه، لازم نیست ربات را دوباره روشن کنید.
    اگر این فایل نباشد، ربات به هر کسی فقط شناسه خودش را می‌گوید و گزارشی نمی‌دهد.

اجرا:
  python3 bale_bot.py                 # برای امتحان
  دائمی: با فایل bale-bot.service (راهنما داخل همان فایل)
"""
import json
import os
import time

import pandas as pd
import requests

import advisor

TOKEN_PATH = os.path.expanduser("~/.bale_token")
ALLOWED_PATH = os.path.expanduser("~/.bale_allowed")
DENIED_PATH = os.path.expanduser("~/.bale_denied")
STATE_PATH = "bale_answers.json"     # آخرین جواب‌ها، برای «گزارش با جواب‌های قبلی»
SUBS_PATH = "bale_subs.json"         # کسانی که «خبرم کن» را روشن کرده‌اند
SIGNAL_STATE_PATH = "bale_signals.json"   # آخرین سیگنال‌های دیده‌شده، برای فهمیدن تغییر
SIGNAL_LOG_PATH = "signal_changes.csv"    # هر تغییر سیگنال با تاریخ و قیمت
SIGNAL_CHECK_SECONDS = 6 * 3600      # هر ۶ ساعت سیگنال‌ها چک شوند
API = "https://tapi.bale.ai/bot{token}/{method}"
DAILY_CACHE_SECONDS = 3600           # قیمت‌های روزانه هر یک ساعت یک بار تازه شوند

BTN_REPORT = "گزارش با جواب‌های قبلی"
BTN_NEW = "پرسش‌های تازه"
BTN_DETAILS = "جزئیات کامل"
BTN_SIGNAL = "📡 سیگنال امروز"
BTN_FORECAST = "🔮 پیش‌بینی حدودی"
BTN_2MIN = "⏱ پیش‌بینی ۲ دقیقه"
BTN_ALERT_ON = "🔔 خبرم کن وقتی سیگنال عوض شد"
BTN_ALERT_OFF = "🔕 دیگر خبرم نکن"
BTN_CANCEL = "لغو"
BTN_KEEP = "بله، همین عدد درست است"
BTN_RETRY = "نه، دوباره وارد می‌کنم"
GOAL_BUTTONS = {"1": "۱ - نگه داشتن بلندمدت", "2": "۲ - سود بیشتر", "3": "۳ - نقد کردن سریع"}

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")


# ---------------------------------------------------------------- تنظیمات

def read_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except FileNotFoundError:
        return None


def read_ids(path):
    """شناسه‌های یک فایل، به ترتیب. خط خالی و متن بعد از # نادیده گرفته می‌شود."""
    text = read_file(path) or ""
    ids = []
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if line:
            ids.append(line)
    return ids


def read_allowed():
    return read_ids(ALLOWED_PATH)


def add_id(path, chat_id, note=""):
    """یک شناسه را در خط تازه به ته فایل اضافه می‌کند (اگر از قبل نباشد)."""
    if str(chat_id) in read_ids(path):
        return
    prefix = ""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                prefix = "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{prefix}{chat_id}" + (f"   # {note}" if note else "") + "\n")


def who(msg):
    """نام و نام کاربری فرستنده، برای خبر دادن به صاحب ربات."""
    u = msg.get("from") or {}
    name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or "بی‌نام"
    return name + (f" (@{u['username']})" if u.get("username") else "")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def is_subscribed(chat_id):
    return str(chat_id) in load_json(SUBS_PATH, [])


def set_subscribed(chat_id, on):
    subs = [x for x in load_json(SUBS_PATH, []) if x != str(chat_id)]
    if on:
        subs.append(str(chat_id))
    save_json(SUBS_PATH, subs)


def load_saved():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def save_answers(chat_id, answers):
    data = load_saved()
    data[str(chat_id)] = answers
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# ---------------------------------------------------------------- ارتباط با بله

class Bale:
    def __init__(self, token):
        self.token = token

    def call(self, method, params=None, wait=40):
        url = API.format(token=self.token, method=method)
        try:
            r = requests.post(url, json=params or {}, timeout=wait)
            d = r.json()
        except Exception as e:
            # پیام خطا آدرس را دارد و آدرس کلید ربات را؛ کلید نباید در لاگ بیاید
            raise RuntimeError(f"{method}: {str(e).replace(self.token, '***')}") from None
        if not d.get("ok"):
            raise RuntimeError(f"{method} failed: {d.get('description', d)}")
        return d.get("result")

    def send(self, chat_id, text, buttons=None, inline=None, cols=1):
        """buttons = دکمه‌های پایین صفحه؛ inline = دکمه‌های زیر خود پیام [(متن، داده)، ...]"""
        params = {"chat_id": chat_id, "text": text}
        if inline:
            params["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d}
                                                           for t, d in inline]]}
        elif buttons:
            params["reply_markup"] = {"keyboard": [[{"text": b} for b in buttons[i:i + cols]]
                                                   for i in range(0, len(buttons), cols)],
                                      "resize_keyboard": True}
        try:
            self.call("sendMessage", params)
        except Exception as e:
            print(f" Could not send message: {e}")


# ---------------------------------------------------------------- ورودی کاربر

def parse_number(text):
    """عدد را از متن کاربر می‌خواند؛ رقم فارسی و جداکننده هزارگان هم قبول است."""
    t = text.strip().translate(FA_DIGITS).replace(",", "").replace("٬", "").replace(" ", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v >= 0 else None


def parse_goal(text):
    t = text.strip().translate(FA_DIGITS)
    for key, label in GOAL_BUTTONS.items():
        if t.startswith(key) or t == label:
            return key
    return None


def plain(markdown):
    """علامت‌های قالب‌بندی فایل گزارش را برای پیام ساده بله حذف می‌کند."""
    lines = []
    for line in markdown.replace("**", "").splitlines():
        for prefix in ("## ", "# ", "> "):
            if line.startswith(prefix):
                line = line[len(prefix):]
        lines.append(line)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- گزارش

class Advisor:
    def __init__(self):
        self.daily, self.daily_at = None, 0.0

    def daily_prices(self):
        if self.daily is None or time.time() - self.daily_at > DAILY_CACHE_SECONDS:
            self.daily = advisor.fetch_daily()
            self.daily_at = time.time()
        return self.daily

    def signals(self):
        """(سیگنال و خط تصمیم هر روش، قیمت فعلی یا None)"""
        return advisor.signal_levels(self.daily_prices()), self.market_price()

    def report(self, answers):
        daily = self.daily_prices()
        book = advisor.fetch_book()
        facts = advisor.compute_facts(answers, daily, book)
        opinion = advisor.algorithm_opinion(answers, facts)
        advisor.write_log(answers, facts, opinion)
        return (advisor.short_text(answers, facts, opinion),
                plain(advisor.report_text(answers, facts, opinion)))

    @staticmethod
    def market_price():
        """قیمت وسط بازار همین الان؛ اگر نشد None."""
        book = advisor.fetch_book()
        return None if book is None else (book["bid"] + book["ask"]) / 2


Q_USDT = "۱) چند تتر (USDT) دارید؟\nهر ۱ تتر تقریباً ۱ دلار است. فقط عدد بنویسید، مثلاً ۲۵۰"
Q_XAUT = ("۲) چقدر طلا دارید؟\n"
          "به واحد XAUT، همان عددی که در کیف پول والکس می‌بینید.\n"
          "هر ۱ XAUT = ۱ اونس ≈ ۳۱٫۱ گرم طلا. مثلاً ۰٫۰۵\n"
          "اگر طلا ندارید ۰ بنویسید.")


# ---------------------------------------------------------------- گفتگو

class Conversation:
    """گفتگو با صاحب ربات: سؤال‌ها را یکی‌یکی می‌پرسد و در آخر گزارش می‌دهد."""

    def __init__(self, bot, adv):
        self.bot, self.adv = bot, adv
        self.step, self.answers, self.market = {}, {}, {}
        self.details = {}       # گزارش کامل آخرین گزارش هر نفر، برای دکمه «جزئیات کامل»

    def main_menu(self, chat_id, text):
        has_saved = str(chat_id) in load_saved()
        buttons = [BTN_REPORT, BTN_NEW] if has_saved else [BTN_NEW]
        if chat_id in self.details:
            buttons = [BTN_DETAILS] + buttons
        buttons += [BTN_SIGNAL, BTN_FORECAST, BTN_2MIN,
                    BTN_ALERT_OFF if is_subscribed(chat_id) else BTN_ALERT_ON]
        self.bot.send(chat_id, text, buttons, cols=2)

    def send_forecast(self, chat_id):
        self.bot.send(chat_id, "در حال گرفتن قیمت‌ها از والکس... (تا ۳۰ ثانیه)")
        try:
            text = advisor.forecast_text(self.adv.daily_prices(), self.adv.market_price())
        except (Exception, SystemExit) as e:   # SystemExit هم از advisor می‌آید
            print(f" Forecast failed: {e}")
            self.main_menu(chat_id, "گرفتن داده از والکس نشد. چند دقیقه بعد دوباره امتحان کنید.")
            return
        self.main_menu(chat_id, text)

    def send_2min(self, chat_id):
        try:
            text = advisor.two_min_text(advisor.fetch_minutes(), advisor.fetch_book())
        except (Exception, SystemExit) as e:   # SystemExit هم از advisor می‌آید
            print(f" 2-minute forecast failed: {e}")
            self.main_menu(chat_id, "گرفتن قیمت دقیقه‌ای از والکس نشد. کمی بعد دوباره امتحان کنید.")
            return
        self.main_menu(chat_id, text)

    def send_signal(self, chat_id):
        self.bot.send(chat_id, "در حال گرفتن قیمت‌ها از والکس... (تا ۳۰ ثانیه)")
        try:
            levels, price = self.adv.signals()
        except (Exception, SystemExit) as e:   # SystemExit هم از advisor می‌آید
            print(f" Signal failed: {e}")
            self.main_menu(chat_id, "گرفتن داده از والکس نشد. چند دقیقه بعد دوباره امتحان کنید.")
            return
        self.main_menu(chat_id, advisor.signal_text(levels, price))

    def start_questions(self, chat_id):
        self.answers[chat_id] = {}
        self.step[chat_id] = "usdt"
        self.bot.send(chat_id, Q_USDT, [BTN_CANCEL])

    def ask_buy_price(self, chat_id):
        self.step[chat_id] = "buy_price"
        price = self.adv.market_price()
        self.market[chat_id] = price
        hint = f"\nقیمت الان حدود {advisor.fa(price)} تتر است." if price else ""
        self.bot.send(chat_id, "میانگین قیمت خرید طلا چقدر بوده؟\n"
                               "به تتر، برای هر ۱ XAUT (یعنی قیمت هر اونس، نه هر گرم)." + hint,
                      [BTN_CANCEL])

    def ask_goal(self, chat_id):
        self.step[chat_id] = "goal"
        self.bot.send(chat_id, "۳) هدف شما چیست؟", list(GOAL_BUTTONS.values()) + [BTN_CANCEL])

    def finish(self, chat_id, answers):
        self.step.pop(chat_id, None)
        save_answers(chat_id, answers)
        self.send_report(chat_id, answers)

    def send_report(self, chat_id, answers):
        self.bot.send(chat_id, "در حال گرفتن قیمت زنده از والکس... (تا ۳۰ ثانیه)")
        try:
            short, full = self.adv.report(answers)
        except (Exception, SystemExit) as e:   # SystemExit هم از advisor می‌آید
            print(f" Report failed: {e}")
            self.main_menu(chat_id, "گرفتن داده از والکس نشد. چند دقیقه بعد دوباره امتحان کنید.")
            return
        self.details[chat_id] = full
        self.main_menu(chat_id, short)

    def handle(self, chat_id, text):
        text = (text or "").strip()
        step = self.step.get(chat_id)

        if text == BTN_2MIN:
            self.step.pop(chat_id, None)
            self.send_2min(chat_id)
            return
        if text == BTN_FORECAST:
            self.step.pop(chat_id, None)
            self.send_forecast(chat_id)
            return
        if text == BTN_SIGNAL:
            self.step.pop(chat_id, None)
            self.send_signal(chat_id)
            return
        if text in (BTN_ALERT_ON, BTN_ALERT_OFF):
            self.step.pop(chat_id, None)
            on = text == BTN_ALERT_ON
            set_subscribed(chat_id, on)
            self.main_menu(chat_id, "🔔 روشن شد. هر وقت یکی از سیگنال‌ها عوض شد، همین‌جا خبرتان می‌کنم "
                                    "(معمولاً چند بار در سال)." if on else
                                    "🔕 خاموش شد. دیگر خبر تغییر سیگنال نمی‌فرستم.")
            return
        if text == BTN_DETAILS and chat_id in self.details:
            self.step.pop(chat_id, None)
            self.main_menu(chat_id, self.details[chat_id])
            return
        if text in ("/start", BTN_CANCEL) or (step is None and text not in (BTN_REPORT, BTN_NEW)):
            self.step.pop(chat_id, None)
            self.main_menu(chat_id, "سلام. من مشاور طلا و تتر هستم و هیچ خرید و فروشی انجام نمی‌دهم.\n"
                                    "یکی از دکمه‌ها را انتخاب کنید.")
            return
        if text == BTN_NEW:
            self.start_questions(chat_id)
            return
        if text == BTN_REPORT:
            saved = load_saved().get(str(chat_id))
            if saved:
                self.send_report(chat_id, saved)
            else:
                self.start_questions(chat_id)
            return

        a = self.answers.setdefault(chat_id, {})
        if step in ("usdt", "xaut", "buy_price"):
            value = parse_number(text)
            if value is None or (step == "buy_price" and value == 0):
                self.bot.send(chat_id, "لطفاً فقط یک عدد مثبت بنویسید، مثلاً ۲۵۰ یا ۰٫۵", [BTN_CANCEL])
                return
            a[step] = value
            if step == "usdt":
                self.step[chat_id] = "xaut"
                self.bot.send(chat_id, Q_XAUT, [BTN_CANCEL])
            elif step == "xaut" and value > 0:
                self.ask_buy_price(chat_id)
            elif step == "xaut":
                a["buy_price"] = None
                self.ask_goal(chat_id)
            elif advisor.price_looks_wrong(value, self.market.get(chat_id)):
                self.step[chat_id] = "confirm_price"
                self.bot.send(chat_id, f"⚠️ قیمت {advisor.fa(value, 2)} خیلی با قیمت امروز "
                                       f"({advisor.fa(self.market[chat_id])}) فرق دارد.\n"
                                       "شاید قیمت هر گرم را زده‌اید؟ قیمت باید برای هر ۱ XAUT (هر اونس) باشد.",
                              [BTN_KEEP, BTN_RETRY, BTN_CANCEL])
            else:
                self.ask_goal(chat_id)
            return
        if step == "confirm_price":
            if text == BTN_KEEP:
                self.ask_goal(chat_id)
            elif text == BTN_RETRY:
                self.ask_buy_price(chat_id)
            else:
                self.bot.send(chat_id, "لطفاً یکی از دکمه‌ها را بزنید.", [BTN_KEEP, BTN_RETRY, BTN_CANCEL])
            return
        if step == "goal":
            goal = parse_goal(text)
            if goal is None:
                self.bot.send(chat_id, "لطفاً یکی از دکمه‌ها را بزنید.",
                              list(GOAL_BUTTONS.values()) + [BTN_CANCEL])
                return
            a["goal"] = goal
            self.finish(chat_id, a)


# ---------------------------------------------------------------- دکمه تأیید / رد

PENDING = {}    # درخواست‌های در انتظار: شناسه -> نام (فقط برای نوشتن کنار شناسه در فایل)


def handle_button(bot, conv, cq):
    """صاحب ربات روی «تأیید» یا «رد» زده است."""
    try:
        bot.call("answerCallbackQuery", {"callback_query_id": cq["id"]})
    except Exception:
        pass                                   # فقط برای بستن حالت «در حال بارگذاری» است
    allowed = read_allowed()
    presser = str((cq.get("from") or {}).get("id"))
    if not allowed or presser != allowed[0]:
        print(f" Ignored button from {presser} (not the owner)")
        return
    parts = (cq.get("data") or "").split(":")
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        return
    action, target = parts
    name = PENDING.pop(target, "") if action == "approve" else PENDING.get(target, "")
    if action == "approve":
        if target in allowed:
            bot.send(allowed[0], f"شناسه {target} از قبل مجاز است.")
            return
        add_id(ALLOWED_PATH, target, name)
        print(f" Owner approved chat id {target}")
        bot.send(allowed[0], f"✅ تأیید شد: {name or target}")
        conv.main_menu(int(target), "درخواست شما تأیید شد. حالا می‌توانید از ربات استفاده کنید.")
    elif action == "deny":
        add_id(DENIED_PATH, target)
        print(f" Owner denied chat id {target}")
        bot.send(allowed[0], f"❌ رد شد: {name or target} (شناسه {target})\n(اگر نظرتان عوض شد، این شناسه را از فایل "
                             f"{DENIED_PATH} پاک کنید.)")


# ---------------------------------------------------------------- خبر تغییر سیگنال

def check_signal_change(bot, adv):
    """سیگنال‌ها را با آخرین بار مقایسه می‌کند؛ اگر عوض شده بود به مشترک‌ها خبر می‌دهد."""
    levels, price = adv.signals()
    signals = {m: v["signal"] for m, v in levels.items()}
    before = load_json(SIGNAL_STATE_PATH, None)
    changes = []
    if before is not None:
        for m, new in signals.items():
            old = before.get(m)
            if old is not None and new is not None and old != new:
                changes.append((m, old, new))
    # سیگنالی که الان «داده کافی نیست» است، مقدار قبلی‌اش نگه داشته شود
    state = {m: (v if v is not None else (before or {}).get(m)) for m, v in signals.items()}
    save_json(SIGNAL_STATE_PATH, state)
    if not changes:
        print(f" {pd.Timestamp.now():%Y-%m-%d %H:%M} signal check: no change")
        return
    now = pd.Timestamp.now(tz="Asia/Tehran").strftime("%Y-%m-%d %H:%M")
    rows = [{"changed_at": now, "price": None if price is None else round(price, 2), "method": m,
             "old": "GOLD" if old else "USDT", "new": "GOLD" if new else "USDT"} for m, old, new in changes]
    pd.DataFrame(rows).to_csv(SIGNAL_LOG_PATH, mode="a", header=not os.path.exists(SIGNAL_LOG_PATH),
                              index=False)
    allowed = read_allowed()
    subs = [c for c in load_json(SUBS_PATH, []) if c in allowed]
    print(f" Signal changed: {[r['method'] + ' -> ' + r['new'] for r in rows]}; notifying {len(subs)} people")
    text = advisor.signal_change_text(changes, signals, price)
    for c in subs:
        bot.send(int(c), text)


# ---------------------------------------------------------------- حلقه اصلی

def main():
    token = read_file(TOKEN_PATH)
    if not token:
        raise SystemExit(f"Bot token not found. Put it in {TOKEN_PATH} (one line).")
    bot = Bale(token)
    me = bot.call("getMe")
    print(f" Connected to Bale as @{me.get('username')}")
    allowed = read_allowed()
    if allowed:
        print(f" Allowed chat ids: {len(allowed)} (owner: {allowed[0]})")
    else:
        print(f" WARNING: {ALLOWED_PATH} not found. The bot will only tell people their chat id.")
    conv = Conversation(bot, Advisor())
    told = set()        # غریبه‌هایی که یک بار جوابشان داده شده (برای جلوگیری از پیام تکراری)

    offset = None
    last_signal_check = 0.0
    while True:
        if time.time() - last_signal_check >= SIGNAL_CHECK_SECONDS:
            last_signal_check = time.time()
            try:
                check_signal_change(bot, conv.adv)
            except (Exception, SystemExit) as e:   # SystemExit هم از advisor می‌آید
                print(f" Signal check failed: {e}")
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            updates = bot.call("getUpdates", params, wait=45) or []
        except Exception as e:
            print(f" {pd.Timestamp.now():%H:%M:%S} getUpdates error: {e}")
            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            if u.get("callback_query"):
                try:
                    handle_button(bot, conv, u["callback_query"])
                except Exception as e:
                    print(f" Error while handling button: {e}")
                continue
            msg = u.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            if chat_id is None:
                continue
            allowed = read_allowed()
            if not allowed:
                print(f" Message from chat id {chat_id}")
                bot.send(chat_id, f"شناسه شما: {chat_id}\n"
                                  f"اگر صاحب این ربات هستید، این عدد را در فایل {ALLOWED_PATH} بگذارید "
                                  f"و ربات را دوباره اجرا کنید.")
                continue
            if str(chat_id) not in allowed:
                print(f" Message from unknown chat id {chat_id}")
                if chat_id not in told and str(chat_id) not in read_ids(DENIED_PATH):
                    told.add(chat_id)
                    bot.send(chat_id, "این ربات خصوصی است. درخواست شما برای صاحب ربات فرستاده شد.\n"
                                      "اگر تأیید کند، همین‌جا خبرتان می‌کنم.")
                    PENDING[str(chat_id)] = who(msg)
                    bot.send(allowed[0], f"کسی می‌خواهد از ربات استفاده کند:\n{who(msg)}\n"
                                         f"شناسه: {chat_id}",
                             inline=[("✅ تأیید", f"approve:{chat_id}"),
                                     ("❌ رد", f"deny:{chat_id}")])
                continue
            try:
                conv.handle(chat_id, msg.get("text"))
            except Exception as e:
                print(f" Error while handling message: {e}")
                bot.send(chat_id, "خطایی پیش آمد. دوباره امتحان کنید.")


if __name__ == "__main__":
    main()
