# -*- coding: utf-8 -*-
"""
Бот-анкета для клієнтів косметолога.

Сценарій:
  1. Людина переходить за посиланням з Instagram → потрапляє в бота.
  2. Бот по черзі ставить питання (кнопки з варіантами + місце для своєї відповіді).
  3. Формує анкету одним текстом і перепитує клієнта, чи все вірно.
  4. Після підтвердження надсилає анкету власнику в Telegram —
     з номером телефону клієнта та його @username для зворотного зв'язку.

Запуск:   python bot.py
Потрібні змінні оточення: BOT_TOKEN, ADMIN_CHAT_ID  (див. README.md та .env.example)
"""

import os
import sys
import html
import logging

# Windows: консоль за замовчуванням cp1251 — переводимо вивід у UTF-8,
# щоб емодзі в логах не валили програму.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from dotenv import load_dotenv
    load_dotenv()  # підхоплює .env при локальному запуску (у хмарі ігнорується)
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
try:
    ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
except ValueError:
    ADMIN_CHAT_ID = 0

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("anketa-bot")

# ── Стани розмови ───────────────────────────────────────────────────────────
ASKING, CONFIRM = range(2)


# ── Допоміжне ────────────────────────────────────────────────────────────────
def _build_keyboard(q, selected=None):
    """Повертає reply_markup для поточного питання."""
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

    # text
    return ReplyKeyboardRemove()


def _question_text(idx):
    q = QUESTIONS[idx]
    head = f"❓ Питання {idx + 1} з {len(QUESTIONS)}\n\n{q['text']}"
    if q["type"] in ("choice", "multichoice") and q.get("allow_custom"):
        head += "\n\n✍️ Або напишіть свій варіант повідомленням."
    return head


async def _send_question(context, chat_id):
    idx = context.user_data["idx"]
    q = QUESTIONS[idx]
    context.user_data["multi"] = set()
    await context.bot.send_message(
        chat_id=chat_id,
        text=_question_text(idx),
        reply_markup=_build_keyboard(q),
    )


def _record(context, value):
    """Зберігає відповідь на поточне питання."""
    idx = context.user_data["idx"]
    q = QUESTIONS[idx]
    context.user_data["answers"][q["key"]] = value
    if q["type"] == "contact":
        context.user_data["phone"] = value


async def _advance(context, chat_id):
    """Переходить до наступного питання або до підтвердження."""
    context.user_data["idx"] += 1
    if context.user_data["idx"] < len(QUESTIONS):
        await _send_question(context, chat_id)
        return ASKING
    return await _show_summary(context, chat_id)


def _summary_lines(context):
    answers = context.user_data["answers"]
    lines = []
    for q in QUESTIONS:
        val = answers.get(q["key"], "—")
        lines.append(f"<b>{html.escape(q['label'])}:</b> {html.escape(str(val))}")
    return lines


async def _show_summary(context, chat_id):
    text = CONFIRM_INTRO + "\n\n" + "\n".join(_summary_lines(context))
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(CONFIRM_YES, callback_data="ok")],
        [InlineKeyboardButton(CONFIRM_REDO, callback_data="redo")],
    ])
    await context.bot.send_message(chat_id=chat_id, text=text,
                                   reply_markup=kb, parse_mode=ParseMode.HTML)
    return CONFIRM


# ── Хендлери ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
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
    await update.message.reply_text("Поїхали! 🚀", reply_markup=ReplyKeyboardRemove())
    await _send_question(context, update.effective_chat.id)
    return ASKING


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    # Очікуємо натискання «Почати анкету»
    if context.user_data.get("awaiting_start"):
        if text == START_BUTTON:
            return await begin_survey(update, context)
        # будь-який текст теж починає анкету
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
            "Надішліть, будь ласка, саме фото 📷 або натисніть «Пропустити» 👇"
        )
        return ASKING

    if qtype in ("choice", "multichoice") and q.get("allow_custom"):
        _record(context, text)
        return await _advance(context, chat_id)

    # choice/multichoice без власного варіанту
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
    phone = update.message.contact.phone_number
    _record(context, phone)
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

    # пропустити фото
    if data == "photoskip" and q["type"] == "photo":
        _record(context, "— (клієнт надішле фото пізніше)")
        await query.edit_message_text(f"{_question_text(idx)}\n\n➡️ Пропущено (надішле пізніше)")
        return await _advance(context, chat_id)

    # одиночний вибір
    if data.startswith("c:") and q["type"] == "choice":
        opt = q["options"][int(data[2:])]
        _record(context, opt)
        await query.edit_message_text(f"{_question_text(idx)}\n\n➡️ {opt}")
        return await _advance(context, chat_id)

    # мультивибір — перемикання
    if data.startswith("m:") and q["type"] == "multichoice":
        i = int(data[2:])
        sel = context.user_data.setdefault("multi", set())
        sel.symmetric_difference_update({i})
        await query.edit_message_reply_markup(reply_markup=_build_keyboard(q, sel))
        return ASKING

    # мультивибір — готово
    if data == "mdone" and q["type"] == "multichoice":
        sel = context.user_data.get("multi", set())
        if not sel:
            await query.answer("Оберіть хоча б один варіант або напишіть свій 🙂",
                               show_alert=True)
            return ASKING
        chosen = ", ".join(q["options"][i] for i in sorted(sel))
        _record(context, chosen)
        await query.edit_message_text(f"{_question_text(idx)}\n\n➡️ {chosen}")
        return await _advance(context, chat_id)

    return ASKING


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "redo":
        await query.edit_message_text("Гаразд, починаємо спочатку 🔄")
        context.user_data["idx"] = 0
        context.user_data["answers"] = {}
        context.user_data["multi"] = set()
        context.user_data.pop("photo_file_id", None)
        await _send_question(context, query.message.chat.id)
        return ASKING

    # підтверджено — надсилаємо власнику
    await _send_to_admin(update, context)
    await query.edit_message_text(DONE_CLIENT)
    context.user_data.clear()
    return ConversationHandler.END


async def _send_to_admin(update, context):
    user = update.effective_user
    answers = context.user_data["answers"]
    phone = context.user_data.get("phone", answers.get("phone", "—"))

    username = f"@{user.username}" if user.username else "немає username"
    tg_name = html.escape(user.full_name or "—")

    # посилання для зворотного зв'язку
    if user.username:
        link = f'<a href="https://t.me/{user.username}">написати клієнту</a>'
    else:
        link = f'<a href="tg://user?id={user.id}">написати клієнту</a>'

    header = (
        "🆕 <b>НОВА АНКЕТА КЛІЄНТА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Телефон:</b> {html.escape(str(phone))}\n"
        f"👤 <b>Telegram:</b> {tg_name} ({html.escape(username)})\n"
        f"💬 <b>Зв'язок:</b> {link}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    )
    body = "\n".join(_summary_lines(context))

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=header + body,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    # фото обличчя клієнта (якщо надіслав)
    photo_id = context.user_data.get("photo_file_id")
    if photo_id:
        client = answers.get("name", "клієнт")
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=photo_id,
            caption=f"📷 Фото обличчя — {client}",
        )

    log.info("Анкету надіслано власнику (від user_id=%s)", user.id)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Анкету скасовано. Напишіть /start, щоб почати знову.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Допоміжна команда: показує ваш chat_id (щоб вписати в ADMIN_CHAT_ID)."""
    await update.message.reply_text(
        f"Ваш chat_id: {update.effective_chat.id}\n"
        "Впишіть це число у змінну ADMIN_CHAT_ID."
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Не задано BOT_TOKEN. Див. README.md")
    if not ADMIN_CHAT_ID:
        log.warning("⚠️ ADMIN_CHAT_ID не задано — анкети нікому не надсилатимуться. "
                    "Напишіть боту /whoami, щоб дізнатися свій chat_id.")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASKING: [
                CallbackQueryHandler(on_callback),
                MessageHandler(filters.CONTACT, on_contact),
                MessageHandler(filters.PHOTO, on_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_text),
            ],
            CONFIRM: [
                CallbackQueryHandler(on_confirm),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("whoami", whoami))

    log.info("✅ Бот запущено. Очікую повідомлення…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
