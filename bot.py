# -*- coding: utf-8 -*-
"""
Бот-анкета для клієнтів косметолога.

Сценарій:
  1. Людина переходить за посиланням з Instagram → потрапляє в бота.
  2. Бот по черзі ставить питання (кнопки з варіантами + місце для своєї відповіді, фото).
  3. Формує анкету одним текстом і перепитує клієнта, чи все вірно.
  4. Після підтвердження надсилає анкету адміністратору(-ам) у Telegram.

Можливості:
  • кнопки дій під анкетою (написати клієнту / записати / передзвонити);
  • виправлення однієї відповіді на етапі підтвердження;
  • нагадування, якщо клієнт кинув анкету на півдорозі;
  • кілька адміністраторів + /stats;
  • збір відгуку через N днів після анкети (з фото результату).

Запуск:   python bot.py
Змінні оточення: BOT_TOKEN, ADMIN_CHAT_IDS (або ADMIN_CHAT_ID),
                 FEEDBACK_DAYS (за замовч. 10), REMIND_MINUTES (за замовч. 60)
"""

import os
import re
import sys
import json
import time
import html
import asyncio
import logging
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import store
import analyze
from questions import (
    QUESTIONS,
    GREETING,
    START_BUTTON,
    CONFIRM_INTRO,
    CONFIRM_YES,
    CONFIRM_REDO,
    DONE_CLIENT,
    MULTI_DONE_BUTTON,
    CONTACT_BUTTON,
)

# ── Налаштування з оточення ─────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()


def _parse_ids(s):
    return [int(x) for x in re.split(r"[ ,;]+", (s or "").strip())
            if x.strip().lstrip("-").isdigit()]


ADMIN_IDS = _parse_ids(os.environ.get("ADMIN_CHAT_IDS")) or _parse_ids(os.environ.get("ADMIN_CHAT_ID"))
# Кому дозволено надсилати фото боту для AI-аналізу (за замовч. — ті ж адміни)
ANALYST_IDS = _parse_ids(os.environ.get("ANALYST_CHAT_IDS")) or ADMIN_IDS
FEEDBACK_DELAY = float(os.environ.get("FEEDBACK_DAYS", "10")) * 86400
REMIND_AFTER = float(os.environ.get("REMIND_MINUTES", "60")) * 60

# Google Sheets (через Apps Script web app) — кожна анкета лягає рядком у таблицю
SHEETS_URL = os.environ.get("SHEETS_WEBHOOK_URL", "").strip()
SHEETS_SECRET = os.environ.get("SHEETS_SECRET", "").strip()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("anketa-bot")

# ── Стани розмови ───────────────────────────────────────────────────────────
ASKING, CONFIRM = range(2)

# ── Додаткові тексти UI ─────────────────────────────────────────────────────
EDIT_BUTTON = "✏️ Виправити один пункт"
EDIT_PROMPT = "Який пункт виправити? 👇"
EDIT_BACK = "⬅️ Назад до анкети"
REMIND_TEXT = ("Ви не завершили анкету 🌸 Давайте продовжимо — це займе хвилинку 💆‍♀️\n"
               "Натисніть /start, щоб почати заново.")
FEEDBACK_TEXT = ("Вітаю! 🌸 Минув час після візиту до Анни 💆‍♀️\n"
                 "Як ваші враження від процедури? Поділіться, будь ласка, відгуком 💬\n"
                 "За бажанням — надішліть фото результату 📸 (зі згодою на публікацію). "
                 "Нам це дуже важливо! 💖")
FEEDBACK_THANKS = "Дякуємо за ваш відгук! 💖🌸 Анні буде дуже приємно."


# ── Клавіатури / питання ─────────────────────────────────────────────────────
def _build_keyboard(q, selected=None):
    qtype = q["type"]

    if qtype == "choice":
        rows = [[InlineKeyboardButton(opt, callback_data=f"c:{i}")]
                for i, opt in enumerate(q["options"])]
        return InlineKeyboardMarkup(rows)

    if qtype == "multichoice":
        selected = selected or set()
        rows = []
        for i, opt in enumerate(q["options"]):
            mark = "☑️ " if i in selected else "⬜ "
            rows.append([InlineKeyboardButton(mark + opt, callback_data=f"m:{i}")])
        rows.append([InlineKeyboardButton(MULTI_DONE_BUTTON, callback_data="mdone")])
        return InlineKeyboardMarkup(rows)

    if qtype == "contact":
        kb = [[KeyboardButton(CONTACT_BUTTON, request_contact=True)]]
        return ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True)

    if qtype == "photo":
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("⏭️ Пропустити (надішлю пізніше)", callback_data="photoskip")]]
        )

    return ReplyKeyboardRemove()


def _question_text(context, idx):
    q = QUESTIONS[idx]
    num = len(context.user_data.get("answers", {})) + 1
    head = f"❓ Питання {num}\n\n{q['text']}"
    if q["type"] in ("choice", "multichoice") and q.get("allow_custom"):
        head += "\n\n✍️ Або напишіть свій варіант повідомленням."
    return head


def _should_ask(context, q):
    """Чи показувати питання (розгалуження): True, якщо немає умови ask_if
    або умова виконана попередньою відповіддю."""
    cond = q.get("ask_if")
    if not cond:
        return True
    ans = context.user_data.get("answers", {}).get(cond["key"], "")
    if "equals" in cond:
        return ans == cond["equals"]
    if "contains" in cond:
        return cond["contains"] in ans
    return True


async def _send_question(context, chat_id):
    idx = context.user_data["idx"]
    q = QUESTIONS[idx]
    context.user_data["multi"] = set()
    _schedule_reminder(context, chat_id)
    await context.bot.send_message(
        chat_id=chat_id,
        text=_question_text(context, idx),
        reply_markup=_build_keyboard(q),
    )


def _record(context, value):
    idx = context.user_data["idx"]
    q = QUESTIONS[idx]
    context.user_data["answers"][q["key"]] = value
    if q["type"] == "contact":
        context.user_data["phone"] = value


async def _advance(context, chat_id):
    # режим редагування одного пункту → одразу назад до підсумку
    if context.user_data.pop("editing", False):
        return await _show_summary(context, chat_id)
    idx = context.user_data["idx"] + 1
    # пропустити неактуальні питання (розгалуження ask_if)
    while idx < len(QUESTIONS) and not _should_ask(context, QUESTIONS[idx]):
        idx += 1
    context.user_data["idx"] = idx
    if idx < len(QUESTIONS):
        await _send_question(context, chat_id)
        return ASKING
    return await _show_summary(context, chat_id)


# ── Підсумок / підтвердження ──────────────────────────────────────────────────
def _summary_lines(context):
    answers = context.user_data["answers"]
    lines = []
    for q in QUESTIONS:
        if q["key"] not in answers:
            continue  # пропущене (розгалуження) — не показуємо
        val = answers[q["key"]]
        lines.append(f"<b>{html.escape(q['label'])}:</b> {html.escape(str(val))}")
    return lines


def _confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(CONFIRM_YES, callback_data="ok")],
        [InlineKeyboardButton(EDIT_BUTTON, callback_data="edit")],
        [InlineKeyboardButton(CONFIRM_REDO, callback_data="redo")],
    ])


def _edit_list_keyboard(context):
    answers = context.user_data.get("answers", {})
    rows = [[InlineKeyboardButton(f"{q['label']}", callback_data=f"edf:{i}")]
            for i, q in enumerate(QUESTIONS) if q["key"] in answers]
    rows.append([InlineKeyboardButton(EDIT_BACK, callback_data="editback")])
    return InlineKeyboardMarkup(rows)


async def _show_summary(context, chat_id):
    text = CONFIRM_INTRO + "\n\n" + "\n".join(_summary_lines(context))
    await context.bot.send_message(chat_id=chat_id, text=text,
                                   reply_markup=_confirm_keyboard(),
                                   parse_mode=ParseMode.HTML)
    return CONFIRM


# ── Нагадування про незавершену анкету ────────────────────────────────────────
def _schedule_reminder(context, chat_id):
    jq = getattr(context, "job_queue", None)
    if not jq:
        return
    for j in jq.get_jobs_by_name(f"r{chat_id}"):
        j.schedule_removal()
    jq.run_once(_reminder_job, REMIND_AFTER, chat_id=chat_id, name=f"r{chat_id}")


def _remove_reminder(context, chat_id):
    jq = getattr(context, "job_queue", None)
    if not jq:
        return
    for j in jq.get_jobs_by_name(f"r{chat_id}"):
        j.schedule_removal()


async def _reminder_job(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(context.job.chat_id, REMIND_TEXT)


# ── Хендлери розмови ──────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    u = update.effective_user
    log.info("START chat_id=%s name=%s username=%s", update.effective_chat.id,
             u.full_name, u.username)
    context.user_data["source"] = context.args[0] if context.args else "напряму"
    kb = ReplyKeyboardMarkup([[KeyboardButton(START_BUTTON)]],
                             resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(GREETING, reply_markup=kb)
    context.user_data["awaiting_start"] = True
    return ASKING


async def begin_survey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["idx"] = 0
    context.user_data["answers"] = {}
    context.user_data["multi"] = set()
    context.user_data.pop("awaiting_start", None)
    await update.message.reply_text("Поїхали! 🚀💖", reply_markup=ReplyKeyboardRemove())
    await _send_question(context, update.effective_chat.id)
    return ASKING


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    if context.user_data.get("awaiting_start"):
        return await begin_survey(update, context)

    if "idx" not in context.user_data:
        await update.message.reply_text("Напишіть /start, щоб почати анкету 🙂")
        return ASKING

    idx = context.user_data["idx"]
    q = QUESTIONS[idx]
    qtype = q["type"]

    if qtype == "text":
        _record(context, text)
        return await _advance(context, chat_id)

    if qtype == "contact":
        _record(context, text)
        await update.message.reply_text("Дякую 👍", reply_markup=ReplyKeyboardRemove())
        return await _advance(context, chat_id)

    if qtype == "photo":
        await update.message.reply_text(
            "Надішліть, будь ласка, саме фото 📷 або натисніть «Пропустити» 👇")
        return ASKING

    if qtype in ("choice", "multichoice") and q.get("allow_custom"):
        _record(context, text)
        return await _advance(context, chat_id)

    await update.message.reply_text("Будь ласка, скористайтеся кнопками вище 👆")
    return ASKING


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "idx" not in context.user_data:
        return ASKING
    idx = context.user_data["idx"]
    q = QUESTIONS[idx]
    if q["type"] != "photo":
        await update.message.reply_text("Дякую! Спершу дайте відповідь на поточне питання 🙂")
        return ASKING
    context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    _record(context, "📷 Фото надіслано")
    await update.message.reply_text("Дякую за фото! 📷✨")
    return await _advance(context, update.effective_chat.id)


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "idx" not in context.user_data:
        return ASKING
    _record(context, update.message.contact.phone_number)
    await update.message.reply_text("Дякую 👍", reply_markup=ReplyKeyboardRemove())
    return await _advance(context, update.effective_chat.id)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id

    if "idx" not in context.user_data:
        await query.edit_message_text("Сесію скинуто. Напишіть /start 🙂")
        return ASKING

    idx = context.user_data["idx"]
    q = QUESTIONS[idx]
    data = query.data

    if data == "photoskip" and q["type"] == "photo":
        _record(context, "— (клієнт надішле фото пізніше)")
        await query.edit_message_text(f"{_question_text(context, idx)}\n\n➡️ Пропущено (надішле пізніше)")
        return await _advance(context, chat_id)

    if data.startswith("c:") and q["type"] == "choice":
        opt = q["options"][int(data[2:])]
        _record(context, opt)
        await query.edit_message_text(f"{_question_text(context, idx)}\n\n➡️ {opt}")
        return await _advance(context, chat_id)

    if data.startswith("m:") and q["type"] == "multichoice":
        i = int(data[2:])
        sel = context.user_data.setdefault("multi", set())
        sel.symmetric_difference_update({i})
        await query.edit_message_reply_markup(reply_markup=_build_keyboard(q, sel))
        return ASKING

    if data == "mdone" and q["type"] == "multichoice":
        sel = context.user_data.get("multi", set())
        if not sel:
            await query.answer("Оберіть хоча б один варіант або напишіть свій 🙂", show_alert=True)
            return ASKING
        chosen = ", ".join(q["options"][i] for i in sorted(sel))
        _record(context, chosen)
        await query.edit_message_text(f"{_question_text(context, idx)}\n\n➡️ {chosen}")
        return await _advance(context, chat_id)

    return ASKING


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    if data == "redo":
        await query.edit_message_text("Гаразд, починаємо спочатку 🔄")
        context.user_data["idx"] = 0
        context.user_data["answers"] = {}
        context.user_data["multi"] = set()
        context.user_data.pop("photo_file_id", None)
        await _send_question(context, chat_id)
        return ASKING

    if data == "edit":
        await query.edit_message_text(EDIT_PROMPT, reply_markup=_edit_list_keyboard(context))
        return CONFIRM

    if data == "editback":
        text = CONFIRM_INTRO + "\n\n" + "\n".join(_summary_lines(context))
        await query.edit_message_text(text, reply_markup=_confirm_keyboard(),
                                      parse_mode=ParseMode.HTML)
        return CONFIRM

    if data.startswith("edf:"):
        context.user_data["idx"] = int(data[4:])
        context.user_data["editing"] = True
        await query.edit_message_text("Гаразд, виправимо цей пункт 👇")
        await _send_question(context, chat_id)
        return ASKING

    if data == "ok":
        _remove_reminder(context, chat_id)
        await _send_to_admins(update, context)
        await query.edit_message_text(DONE_CLIENT)
        await _send_ai_analysis(update, context)
        _store_anketa(update, context)
        await _save_to_sheets(update, context)
        context.user_data.clear()
        return ConversationHandler.END

    return CONFIRM


# ── Надсилання анкети адміністраторам ─────────────────────────────────────────
def _admin_keyboard(user):
    if user.username:
        write_url = f"https://t.me/{user.username}"
    else:
        write_url = f"tg://user?id={user.id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Написати клієнту", url=write_url)],
        [InlineKeyboardButton("✅ Записати", callback_data=f"adm:book:{user.id}"),
         InlineKeyboardButton("⏳ Передзвонити", callback_data=f"adm:call:{user.id}")],
    ])


async def _send_to_admins(update, context):
    user = update.effective_user
    answers = context.user_data["answers"]
    phone = context.user_data.get("phone", answers.get("phone", "—"))
    username = f"@{user.username}" if user.username else "немає username"
    tg_name = html.escape(user.full_name or "—")

    header = (
        "🆕 <b>НОВА АНКЕТА КЛІЄНТА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Телефон:</b> {html.escape(str(phone))}\n"
        f"👤 <b>Telegram:</b> {tg_name} ({html.escape(username)})\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    )
    body = "\n".join(_summary_lines(context))
    text = header + body
    photo_id = context.user_data.get("photo_file_id")
    kb = _admin_keyboard(user)

    if not ADMIN_IDS:
        log.warning("⚠️ ADMIN_IDS порожній — нікому надсилати анкету!")
        return

    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, text, parse_mode=ParseMode.HTML,
                                           reply_markup=kb, disable_web_page_preview=True)
            if photo_id:
                await context.bot.send_photo(
                    aid, photo_id,
                    caption=f"📷 Фото обличчя — {html.escape(answers.get('name', 'клієнт'))}")
        except Exception as e:
            log.warning("Не вдалося надіслати анкету адміну %s: %s", aid, e)
    log.info("Анкету надіслано адмінам %s (від user_id=%s)", ADMIN_IDS, user.id)


def _split_message(text, limit=3900):
    """Ділить текст на частини ≤ limit символів за межами рядків
    (тіло AI-аналізу екрановане, тегів усередині немає — розрив безпечний)."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # дуже довгий рядок без переносів
            if cur:
                parts.append(cur); cur = ""
            parts.append(line[:limit]); line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur); cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts or [text]


def _answers_plain(context):
    answers = context.user_data["answers"]
    return "\n".join(f"{q['label']}: {answers.get(q['key'], '—')}" for q in QUESTIONS)


async def _send_ai_analysis(update, context):
    """Формує AI-чернетку аналізу (з фото, якщо є) і надсилає адмінам."""
    if not ADMIN_IDS:
        return
    answers_text = _answers_plain(context)

    image_bytes = None
    photo_id = context.user_data.get("photo_file_id")
    if photo_id:
        try:
            f = await context.bot.get_file(photo_id)
            image_bytes = bytes(await f.download_as_bytearray())
        except Exception as e:
            log.warning("Не вдалося завантажити фото для AI: %s", e)

    analysis = await analyze.build_analysis(answers_text, image_bytes=image_bytes)
    if not analysis:
        return  # ключа немає або помилка — мовчки пропускаємо

    name = context.user_data["answers"].get("name", "клієнт")
    text = (f"🤖 <b>AI-аналіз (чернетка для Анни)</b>\nКлієнт: {html.escape(str(name))}\n"
            "━━━━━━━━━━━━━━━━━━━━\n" + html.escape(analysis))
    # Telegram обмежує повідомлення 4096 символами — ріжемо на частини
    chunks = _split_message(text, 3900)
    for aid in ADMIN_IDS:
        for ch in chunks:
            try:
                await context.bot.send_message(aid, ch, parse_mode=ParseMode.HTML,
                                               disable_web_page_preview=True)
            except Exception as e:
                log.warning("Не вдалося надіслати AI-аналіз адміну %s: %s", aid, e)
    log.info("AI-аналіз надіслано адмінам (%d част.)", len(chunks))


async def _save_to_sheets(update, context):
    """Надсилає анкету рядком у Google Sheets (через Apps Script). Тихо пропускає, якщо не налаштовано."""
    if not SHEETS_URL:
        return
    answers = context.user_data.get("answers", {})
    user = update.effective_user
    headers = ["Дата"] + [q["label"] for q in QUESTIONS] + ["Джерело", "Username", "Telegram ID"]
    row = ([time.strftime("%Y-%m-%d %H:%M")]
           + [str(answers.get(q["key"], "")) for q in QUESTIONS]
           + [context.user_data.get("source", "напряму"),
              ("@" + user.username) if user.username else "",
              str(user.id)])
    payload = json.dumps({"secret": SHEETS_SECRET, "headers": headers, "row": row}).encode("utf-8")

    def _post():
        req = urllib.request.Request(SHEETS_URL, data=payload,
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=20).read().decode()[:50]

    try:
        res = await asyncio.to_thread(_post)
        log.info("Анкету збережено в Google Sheets: %s", res)
    except Exception as e:
        log.warning("Не вдалося зберегти в Google Sheets: %s", e)


def _store_anketa(update, context):
    user = update.effective_user
    name = context.user_data["answers"].get("name", "—")
    source = context.user_data.get("source", "напряму")
    try:
        store.add_anketa(user.id, name, user.username, source, FEEDBACK_DELAY,
                         answers_text=_answers_plain(context))
    except Exception as e:
        log.warning("Не вдалося зберегти анкету у store: %s", e)


# ── Кнопки дій адміна під анкетою ─────────────────────────────────────────────
async def on_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        _, action, _uid = query.data.split(":")
    except ValueError:
        return
    status = {"book": "✅ Записаний", "call": "⏳ Передзвонити"}.get(action, "")
    who = html.escape(update.effective_user.full_name or "адмін")
    stamp = time.strftime("%d.%m %H:%M")

    base = query.message.text_html or query.message.text or ""
    marker = "\n\n📌 "
    if marker in base:
        base = base[:base.index(marker)]
    new_text = f"{base}{marker}<b>{status}</b> · {who} · {stamp}"
    try:
        await query.edit_message_text(new_text, parse_mode=ParseMode.HTML,
                                      reply_markup=query.message.reply_markup,
                                      disable_web_page_preview=True)
    except Exception as e:
        log.warning("admin action edit fail: %s", e)


# ── Відгуки через N днів ──────────────────────────────────────────────────────
async def feedback_checker(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    for entry in store.due_feedback(now):
        try:
            await context.bot.send_message(entry["uid"], FEEDBACK_TEXT)
            store.mark_feedback_sent(entry["uid"], entry["due"])
            log.info("Надіслано запит відгуку клієнту %s", entry["uid"])
        except Exception as e:
            log.warning("Не вдалося надіслати запит відгуку %s: %s", entry["uid"], e)


async def _push_analysis_to_admins(context, client_label, answers_text, image_bytes):
    """Будує AI-аналіз (фото + контекст анкети) і шле адмінам частинами."""
    analysis = await analyze.build_analysis(
        answers_text or "Аналіз лише за фото (анкета недоступна). Оціни стан шкіри за зображенням.",
        image_bytes=image_bytes)
    if not analysis:
        return False
    head = (f"🤖 <b>AI-аналіз за фото</b>\nКлієнт: {html.escape(str(client_label))}\n"
            "━━━━━━━━━━━━━━━━━━━━\n")
    for aid in ADMIN_IDS:
        for ch in _split_message(head + html.escape(analysis), 3900):
            try:
                await context.bot.send_message(aid, ch, parse_mode=ParseMode.HTML,
                                                disable_web_page_preview=True)
            except Exception as e:
                log.warning("AI-аналіз адміну %s не надіслано: %s", aid, e)
    return True


async def on_free_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повідомлення поза анкетою: фото від аналітика → AI-аналіз;
    відгук від клієнта → пересилаємо адмінам; інше → підказка."""
    user = update.effective_user

    # Аналітик (Анна/власник) надіслав фото → AI-аналіз цього фото
    if update.message.photo and user.id in ANALYST_IDS:
        await update.message.reply_text("🔎 Аналізую фото… (кілька секунд)")
        try:
            f = await context.bot.get_file(update.message.photo[-1].file_id)
            img = bytes(await f.download_as_bytearray())
        except Exception as e:
            log.warning("Аналітик: не вдалося завантажити фото: %s", e)
            await update.message.reply_text("😕 Не вдалося завантажити фото. Спробуйте ще раз.")
            return
        ctx_text = (update.message.caption or "").strip() or \
            "Аналіз лише за фото (анкета недоступна). Оціни стан шкіри за зображенням."
        analysis = await analyze.build_analysis(ctx_text, image_bytes=img)
        if not analysis:
            await update.message.reply_text("AI зараз недоступний — спробуйте трохи пізніше.")
            return
        head = "🤖 <b>AI-аналіз за фото</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        for ch in _split_message(head + html.escape(analysis), 3900):
            await update.message.reply_text(ch, parse_mode=ParseMode.HTML,
                                            disable_web_page_preview=True)
        return
    if store.is_awaiting_feedback(user.id):
        username = f"@{user.username}" if user.username else "немає username"
        head = (f"💬 <b>ВІДГУК від клієнта</b>\n"
                f"👤 {html.escape(user.full_name or '—')} ({html.escape(username)})")
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(aid, head, parse_mode=ParseMode.HTML)
                await context.bot.copy_message(aid, update.effective_chat.id,
                                                update.message.message_id)
            except Exception as e:
                log.warning("Не вдалося переслати відгук адміну %s: %s", aid, e)
        store.clear_awaiting(user.id)
        await update.message.reply_text(FEEDBACK_THANKS)
    elif update.message.photo:
        # Клієнт надіслав фото ПІСЛЯ анкети (напр. тиснув «надішлю пізніше»)
        await update.message.reply_text("Дякую! Передаю фото Анні… 📷")
        rec = store.last_anketa(user.id)
        name = (rec or {}).get("name") or user.full_name or "клієнт"
        uname = f"@{user.username}" if user.username else "—"
        fid = update.message.photo[-1].file_id
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_photo(
                    aid, fid,
                    caption=(f"📷 Фото клієнта (надіслане після анкети)\n"
                             f"👤 {html.escape(name)} ({html.escape(uname)})"))
            except Exception as e:
                log.warning("Не вдалося переслати фото клієнта адміну %s: %s", aid, e)
        try:
            f = await context.bot.get_file(fid)
            img = bytes(await f.download_as_bytearray())
            await _push_analysis_to_admins(context, name, (rec or {}).get("answers", ""), img)
        except Exception as e:
            log.warning("AI для пізнього фото не вдався: %s", e)
        await update.message.reply_text("Готово — фото передала Анні 💖✨")
    else:
        await update.message.reply_text("Напишіть /start, щоб заповнити анкету 🙂")


# ── Команди ───────────────────────────────────────────────────────────────────
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Ця команда лише для адміністратора 🔒")
        return
    s = store.stats()
    lines = ["📊 <b>Статистика анкет</b>",
             f"Усього: <b>{s['total']}</b>",
             f"Сьогодні: <b>{s['today']}</b>",
             f"За 7 днів: <b>{s['week']}</b>"]
    if s["by_source"]:
        lines.append("\n<b>Джерела:</b>")
        for src, n in sorted(s["by_source"].items(), key=lambda x: -x[1]):
            lines.append(f"• {html.escape(src)}: {n}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _remove_reminder(context, update.effective_chat.id)
    context.user_data.clear()
    await update.message.reply_text("Анкету скасовано. Напишіть /start, щоб почати знову.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Ваш chat_id: {update.effective_chat.id}\nВпишіть це число у ADMIN_CHAT_IDS.")


def _maybe_start_health_server():
    """У хмарі (Render) піднімає мінімальний HTTP-сервер на $PORT,
    щоб сервіс вважався «живим» і UptimeRobot міг його пінгувати.
    Локально (без PORT) нічого не робить."""
    port = os.environ.get("PORT")
    if not port:
        return
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"anketa-bot alive")

        def log_message(self, *args):
            pass

    def _serve():
        HTTPServer(("0.0.0.0", int(port)), _H).serve_forever()

    threading.Thread(target=_serve, daemon=True).start()
    log.info("🌐 Health-сервер на порту %s (для Render/UptimeRobot)", port)


def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Не задано BOT_TOKEN. Див. README.md")
    if not ADMIN_IDS:
        log.warning("⚠️ ADMIN_IDS не задано — анкети нікому не надсилатимуться. "
                    "Напишіть боту /whoami і впишіть число у ADMIN_CHAT_IDS.")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASKING: [
                CallbackQueryHandler(on_callback, pattern=r"^(c:|m:|mdone|photoskip)"),
                MessageHandler(filters.CONTACT, on_contact),
                MessageHandler(filters.PHOTO, on_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_text),
            ],
            CONFIRM: [
                CallbackQueryHandler(on_confirm, pattern=r"^(ok|redo|edit|editback|edf:)"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CallbackQueryHandler(on_admin_action, pattern=r"^adm:"))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, on_free_message))

    # періодична перевірка черги відгуків (щогодини)
    if app.job_queue:
        app.job_queue.run_repeating(feedback_checker, interval=3600, first=15)

    external = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")
    port = int(os.environ.get("PORT", "0") or 0)

    if external and port:
        # Хмарний режим (Render): webhook — вхідні апдейти будять сервіс,
        # пінгер не потрібен, конфлікту з локальною копією немає.
        import hashlib
        secret = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:40]
        log.info("✅ Бот запущено у WEBHOOK-режимі: %s. Адміни: %s", external, ADMIN_IDS)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="hook",
            webhook_url=f"{external.rstrip('/')}/hook",
            secret_token=secret,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )
    else:
        # Локальний режим: звичайний polling.
        _maybe_start_health_server()
        log.info("✅ Бот запущено (polling). Адміни: %s. Очікую повідомлення…", ADMIN_IDS)
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
