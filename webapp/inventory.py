#!/usr/bin/env python3
"""
Личный инвентарь ("какие предметы у меня есть") — ботом пользуется один
человек, поэтому это не хранилище per-Telegram-user, а один общий список
{product_id: количество}, такой же простой JSON-файл рантайм-состояния, как
alerted_drops.json (не версионируется, см. .gitignore).
"""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_REPO_DIR = Path(__file__).resolve().parent.parent
INVENTORY_FILE = str(_REPO_DIR / "inventory.json")


def load():
    """{product_id: количество}."""
    if not os.path.exists(INVENTORY_FILE):
        return {}
    try:
        with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save(data):
    try:
        with open(INVENTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("Не удалось сохранить inventory.json")


def set_quantity(pid, quantity):
    """quantity <= 0 удаляет предмет из инвентаря."""
    data = load()
    if quantity <= 0:
        data.pop(pid, None)
    else:
        data[pid] = quantity
    save(data)
    return data
