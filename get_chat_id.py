# -*- coding: utf-8 -*-
"""
Допоміжний скрипт: сам визначає ваш chat_id і вписує його в .env.

Як працює:
  1. Бере BOT_TOKEN з .env.
  2. Просить вас написати боту будь-яке повідомлення (наприклад "привіт").
  3. Через Telegram API (getUpdates) зчитує ваш chat_id.
  4. Записує ADMIN_CHAT_ID у файл .env.

Запуск:  python get_chat_id.py
"""
import sys
import time
import urllib.request
import json
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ENV_PATH = Path(__file__).with_name(".env")
TOKEN = os.environ.get("BOT_TOKEN", "").strip()

if not TOKEN:
    sys.exit("❌ У .env не задано BOT_TOKEN. Спочатку впишіть токен у файл .env")


def api(method):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def write_chat_id(chat_id):
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out, found = [], False
    for ln in lines:
        if ln.strip().startswith("ADMIN_CHAT_ID"):
            out.append(f"ADMIN_CHAT_ID={chat_id}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"ADMIN_CHAT_ID={chat_id}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    me = api("getMe")
    if not me.get("ok"):
        sys.exit(f"❌ Невірний токен? Відповідь: {me}")
    bot_username = me["result"]["username"]
    print(f"✅ Токен робочий. Бот: @{bot_username}")
    print(f"➡️  Відкрийте https://t.me/{bot_username} і напишіть боту будь-що (наприклад «привіт»).")
    print("⏳ Чекаю ваше повідомлення…")

    for _ in range(60):  # ~2 хвилини
        upd = api("getUpdates")
        if upd.get("ok") and upd["result"]:
            for item in reversed(upd["result"]):
                msg = item.get("message") or item.get("edited_message")
                if msg and msg.get("chat"):
                    chat_id = msg["chat"]["id"]
                    name = msg["chat"].get("first_name", "")
                    write_chat_id(chat_id)
                    print(f"\n🎉 Знайдено! Ваш chat_id = {chat_id} ({name})")
                    print("✅ Записано в .env (ADMIN_CHAT_ID). Тепер запускайте: python bot.py")
                    return
        time.sleep(2)

    print("⚠️ Повідомлення не надійшло за 2 хв. Напишіть боту і запустіть скрипт ще раз.")


if __name__ == "__main__":
    main()
