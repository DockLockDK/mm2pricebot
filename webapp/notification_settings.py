#!/usr/bin/env python3
"""
Настройки push-алертов о цене — тот же общий JSON-файл, что и
inventory.py/favorites.py (бот однопользовательский, ни одно хранилище тут
не разделено по Telegram-аккаунтам). Два независимых типа алерта (падение
цены / рост цены, см. webapp/alerts.py) — у каждого свой признак вкл/выкл и
область действия: все предметы (godly/ancient, как и раньше) или только
выбранные вручную.
"""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_REPO_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = str(_REPO_DIR / "notification_settings.json")

DEFAULTS = {
    "drop_enabled": True,
    "drop_scope": "all",  # "all" | "selected"
    "drop_items": [],
    "rise_enabled": True,
    "rise_scope": "all",
    "rise_items": [],
    # Сколько последних алертов о цене хранить в чате одновременно — при
    # отправке нового, если их накопилось больше, самые старые удаляются из
    # чата (см. alerts._record_and_trim_alert_message), чтобы уведомления не
    # захламляли чат бесконечно.
    "max_alert_messages": 30,
}


def load():
    """Настройки, всегда со всеми ключами (недостающие/лишние — не проблема,
    см. update)."""
    if not os.path.exists(SETTINGS_FILE):
        return dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    return merged


def save(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("Не удалось сохранить notification_settings.json")


def update(patch):
    """Частичное обновление — принимает только известные ключи из DEFAULTS,
    остальное из текущих настроек остаётся как было."""
    data = load()
    data.update({k: v for k, v in patch.items() if k in DEFAULTS})
    save(data)
    return data


def allows(settings, kind, pid):
    """Разрешает ли текущая настройка алерт вида kind ('drop'/'rise') для
    предмета pid — учитывает и вкл/выкл, и область действия (все/выбранные)."""
    if not settings.get(f"{kind}_enabled", True):
        return False
    if settings.get(f"{kind}_scope") == "selected":
        return pid in (settings.get(f"{kind}_items") or [])
    return True
