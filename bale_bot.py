#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bale_bot.py - مشاور طلا و تتر روی پیام‌رسان «بله»

همان سؤال‌ها و همان گزارش advisor.py را در بله می‌دهد. هیچ خرید و فروشی انجام نمی‌دهد.

امنیت:
  - کلید ربات (توکن) در کد نیست. در فایل ~/.bale_token روی سرور نگه داشته می‌شود.
  - فقط یک نفر (صاحب ربات) جواب می‌گیرد. شناسه او در فایل ~/.bale_allowed است.
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
STATE_PATH = "bale_answers.json"     # آخرین جواب‌ها، برای «گزارش با جواب‌های قبلی»
API = "https://tapi.bale.ai/bot{token}/{method}"
DAILY_CACHE_SECONDS = 3600           # قیمت‌های روزانه هر یک ساعت یک بار تازه شوند

BTN_REPORT = "گزارش با جواب‌های قبلی"
BTN_NEW = "پرسش‌های تازه"
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
        r = requests.post(url, json=params or {}, timeout=wait)
        d = r.json()
        if not d.get("ok"):
            raise RuntimeError(f"{method} failed: {d.get('description', d)}")
        return d.get("result")

    def send(self, chat_id, text, buttons=None):
        params = {"chat_id": chat_id, "text": text}
        if buttons:
            params["reply_markup"] = {"keyboard": [[{"text": b}] for b in buttons],
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

    def report(self, answers):
        if self.daily is None or time.time() - self.daily_at > DAILY_CACHE_SECONDS:
            self.daily = advisor.fetch_daily()
            self.daily_at = time.time()
        book = advisor.fetch_book()
        facts = advisor.compute_facts(answers, self.daily, book)
        opinion = advisor.algorithm_opinion(answers, facts)
        advisor.write_log(answers, facts, opinion)
        return plain(advisor.report_text(answers, facts, opinion))

    @staticmethod
    def market_price():
        """قیمت وسط بازار همین الان؛ اگر نشد None."""
        book = advisor.fetch_book()
        return None if book is None else (book["bid"] + book["ask"]) / 2


Q_USDT = "۱) چند تتر (USDT) دارید؟\nهر ۱ تتر تقریباً ۱ دلار است. فقط عدد بنویسید، مثلاً 250"
Q_XAUT = ("۲) چقدر طلا دارید؟\n"
          "به واحد XAUT، همان عددی که در کیف پول والکس می‌بینید.\n"
          "هر ۱ XAUT = ۱ اونس ≈ ۳۱٫۱ گرم طلا. مثلاً 0.05\n"
          "اگر طلا ندارید 0 بنویسید.")


# ---------------------------------------------------------------- گفتگو

class Conversation:
    """گفتگو با صاحب ربات: سؤال‌ها را یکی‌یکی می‌پرسد و در آخر گزارش می‌دهد."""

    def __init__(self, bot, adv):
        self.bot, self.adv = bot, adv
        self.step, self.answers, self.market = {}, {}, {}

    def main_menu(self, chat_id, text):
        has_saved = str(chat_id) in load_saved()
        buttons = [BTN_REPORT, BTN_NEW] if has_saved else [BTN_NEW]
        self.bot.send(chat_id, text, buttons)

    def start_questions(self, chat_id):
        self.answers[chat_id] = {}
        self.step[chat_id] = "usdt"
        self.bot.send(chat_id, Q_USDT, [BTN_CANCEL])

    def ask_buy_price(self, chat_id):
        self.step[chat_id] = "buy_price"
        price = self.adv.market_price()
        self.market[chat_id] = price
        hint = f"\nقیمت الان حدود {price:,.0f} تتر است." if price else ""
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
            text = self.adv.report(answers)
        except BaseException as e:      # SystemExit هم از advisor می‌آید
            print(f" Report failed: {e}")
            self.main_menu(chat_id, "گرفتن داده از والکس نشد. چند دقیقه بعد دوباره امتحان کنید.")
            return
        self.main_menu(chat_id, text)

    def handle(self, chat_id, text):
        text = (text or "").strip()
        step = self.step.get(chat_id)

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
                self.bot.send(chat_id, "لطفاً فقط یک عدد مثبت بنویسید، مثلاً 250 یا 0.5", [BTN_CANCEL])
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
                self.bot.send(chat_id, f"⚠️ قیمت {value:,.2f} خیلی با قیمت امروز "
                                       f"({self.market[chat_id]:,.0f}) فرق دارد.\n"
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


# ---------------------------------------------------------------- حلقه اصلی

def main():
    token = read_file(TOKEN_PATH)
    if not token:
        raise SystemExit(f"Bot token not found. Put it in {TOKEN_PATH} (one line).")
    allowed = read_file(ALLOWED_PATH)
    bot = Bale(token)
    me = bot.call("getMe")
    print(f" Connected to Bale as @{me.get('username')}")
    if allowed:
        print(f" Only chat id {allowed} will get answers.")
    else:
        print(f" WARNING: {ALLOWED_PATH} not found. The bot will only tell people their chat id.")
    conv = Conversation(bot, Advisor())

    offset = None
    while True:
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
            msg = u.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            if chat_id is None:
                continue
            if not allowed:
                print(f" Message from chat id {chat_id}")
                bot.send(chat_id, f"شناسه شما: {chat_id}\n"
                                  f"اگر صاحب این ربات هستید، این عدد را در فایل {ALLOWED_PATH} بگذارید "
                                  f"و ربات را دوباره اجرا کنید.")
                continue
            if str(chat_id) != allowed:
                print(f" Ignored message from unknown chat id {chat_id}")
                continue
            try:
                conv.handle(chat_id, msg.get("text"))
            except Exception as e:
                print(f" Error while handling message: {e}")
                bot.send(chat_id, "خطایی پیش آمد. دوباره امتحان کنید.")


if __name__ == "__main__":
    main()
