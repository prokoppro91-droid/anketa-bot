# -*- coding: utf-8 -*-
"""
AI-переданаліз для косметолога.

З відповідей анкети (і фото обличчя, якщо є) формує ЧЕРНЕТКУ попереднього
аналізу для Анни: ймовірний стан шкіри, прапорці безпеки, можливі процедури,
що уточнити. Це підказка фахівцю, а не діагноз.

Провайдери (за наявності ключа, у порядку пріоритету):
  • GEMINI_API_KEY    → Google Gemini (БЕЗКОШТОВНИЙ тариф) — за замовчуванням
  • ANTHROPIC_API_KEY → Claude API (платний) — запасний варіант

Якщо жодного ключа немає — повертає None, і бот працює без AI.
Безкоштовний ключ Gemini: https://aistudio.google.com/apikey
"""
import os
import logging

log = logging.getLogger("anketa-bot.analyze")

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
CLAUDE_MODEL = os.environ.get("AI_MODEL", "claude-opus-4-8")

SYSTEM = (
    "Ти — досвідчений лікар-косметолог-консультант. На основі анкети клієнта "
    "(і фото обличчя, якщо воно надане) сформуй стислу ЧЕРНЕТКУ попереднього "
    "аналізу для косметолога Анни. Це підказка фахівцю перед консультацією, "
    "а НЕ остаточний діагноз і не пряма рекомендація клієнту.\n\n"
    "Відповідай українською, структуровано, по пунктах:\n"
    "1) 🔎 Ймовірний стан шкіри (на основі анкети та фото)\n"
    "2) ⚠️ Прапорці безпеки / протипоказання з анкети — виділи окремо\n"
    "3) 💡 Можливі напрямки догляду та процедур (кілька варіантів, обережно)\n"
    "4) ❓ Що варто уточнити в клієнта на консультації\n\n"
    "Будь конкретною, але обережною: уникай категоричних діагнозів, "
    "познач невпевненість там, де її варто позначити. Якщо фото немає — "
    "так і зазнач, що висновок лише за анкетою. Наприкінці додай рядок: "
    "«🤖 Це AI-чернетка. Рішення приймає косметолог.»"
)


def _user_prompt(answers_text):
    return (f"Анкета клієнта:\n\n{answers_text}\n\n"
            "Сформуй чернетку попереднього аналізу за наведеною структурою.")


async def build_analysis(answers_text, image_bytes=None, image_media_type="image/jpeg"):
    """Повертає текст аналізу або None (якщо AI недоступний / помилка)."""
    if os.environ.get("GEMINI_API_KEY"):
        return await _gemini(answers_text, image_bytes, image_media_type)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return await _claude(answers_text, image_bytes, image_media_type)
    return None


# ── Google Gemini (безкоштовно) ──────────────────────────────────────────────
async def _gemini(answers_text, image_bytes, mime):
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.warning("Пакет google-genai не встановлено — Gemini вимкнено.")
        return None
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        contents = [_user_prompt(answers_text)]
        if image_bytes:
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime))
        resp = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=SYSTEM),
        )
        return (resp.text or "").strip() or None
    except Exception as e:
        log.warning("Gemini-аналіз не вдався: %s", e)
        return None


# ── Claude (платний, запасний) ────────────────────────────────────────────────
async def _claude(answers_text, image_bytes, mime):
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        return None
    try:
        import base64
        client = AsyncAnthropic()
        content = []
        if image_bytes:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime,
                           "data": base64.standard_b64encode(image_bytes).decode("utf-8")},
            })
        content.append({"type": "text", "text": _user_prompt(answers_text)})
        resp = await client.messages.create(
            model=CLAUDE_MODEL, max_tokens=2000, system=SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
    except Exception as e:
        log.warning("Claude-аналіз не вдався: %s", e)
        return None
