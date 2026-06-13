# -*- coding: utf-8 -*-
"""
AI-переданаліз для косметолога (Claude API).

З відповідей анкети (і фото обличчя, якщо є) формує ЧЕРНЕТКУ попереднього
аналізу для Анни: ймовірний стан шкіри, на що звернути увагу, можливі
процедури/догляд, протипоказання-прапорці та що уточнити в клієнта.

Це підказка фахівцю, а не діагноз і не рекомендація клієнту.

Потрібна змінна оточення ANTHROPIC_API_KEY (ключ з platform.claude.com).
Якщо ключа немає — функція тихо повертає None (бот працює без AI).
"""
import os
import base64
import logging

log = logging.getLogger("anketa-bot.analyze")

# Модель за замовчуванням — найкраща модель Anthropic (текст + зір)
MODEL = os.environ.get("AI_MODEL", "claude-opus-4-8")

_client = None


def _get_client():
    """Лінива ініціалізація async-клієнта. None, якщо немає ключа/бібліотеки."""
    global _client
    if _client is not None:
        return _client
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        log.warning("Пакет anthropic не встановлено — AI-аналіз вимкнено.")
        return None
    _client = AsyncAnthropic()  # читає ANTHROPIC_API_KEY з оточення
    return _client


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


async def build_analysis(answers_text, image_bytes=None, image_media_type="image/jpeg"):
    """Повертає текст аналізу або None (якщо AI недоступний / помилка)."""
    client = _get_client()
    if client is None:
        return None

    content = []
    if image_bytes:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_media_type,
                "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
            },
        })
    content.append({
        "type": "text",
        "text": (f"Анкета клієнта:\n\n{answers_text}\n\n"
                 "Сформуй чернетку попереднього аналізу за наведеною структурою."),
    })

    try:
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:
        log.warning("AI-аналіз не вдався: %s", e)
        return None
