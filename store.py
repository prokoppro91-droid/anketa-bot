# -*- coding: utf-8 -*-
"""
Просте файлове сховище (data.json) для бота-анкети.

Зберігає:
  anketas  — список заповнених анкет (для статистики /stats)
  feedback — черга відкладених запитів відгуку (через N днів після анкети)
  awaiting — id клієнтів, від яких зараз чекаємо текст/фото відгуку

Працює поверх звичайного JSON-файлу — нічого встановлювати не треба.
Файл data.json НЕ потрапляє в git (там персональні дані клієнтів).
"""
import json
import time
from pathlib import Path

DATA = Path(__file__).with_name("data.json")


def _load():
    if DATA.exists():
        try:
            return json.loads(DATA.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"anketas": [], "feedback": [], "awaiting": []}


def _save(d):
    DATA.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def add_anketa(uid, name, username, source, feedback_delay):
    """Зберігає анкету і ставить у чергу запит відгуку через feedback_delay секунд."""
    d = _load()
    now = time.time()
    d["anketas"].append({
        "ts": now, "uid": uid, "name": name,
        "username": username, "source": source,
    })
    d["feedback"].append({
        "uid": uid, "name": name, "due": now + feedback_delay, "sent": False,
    })
    _save(d)


def stats():
    d = _load()
    now = time.time()
    ank = d["anketas"]
    by_source = {}
    for a in ank:
        s = a.get("source") or "напряму"
        by_source[s] = by_source.get(s, 0) + 1
    return {
        "total": len(ank),
        "today": sum(1 for a in ank if a["ts"] >= now - 86400),
        "week": sum(1 for a in ank if a["ts"] >= now - 7 * 86400),
        "by_source": by_source,
    }


def due_feedback(now):
    """Запити відгуку, яким час прийшов і ще не надіслані."""
    d = _load()
    return [f for f in d["feedback"] if not f.get("sent") and f["due"] <= now]


def mark_feedback_sent(uid, due):
    d = _load()
    for f in d["feedback"]:
        if f["uid"] == uid and abs(f["due"] - due) < 1 and not f.get("sent"):
            f["sent"] = True
    if uid not in d["awaiting"]:
        d["awaiting"].append(uid)
    _save(d)


def is_awaiting_feedback(uid):
    return uid in _load().get("awaiting", [])


def clear_awaiting(uid):
    d = _load()
    if uid in d.get("awaiting", []):
        d["awaiting"].remove(uid)
        _save(d)
