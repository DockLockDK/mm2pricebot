#!/usr/bin/env python3
"""
Избранное ("за какими предметами хочу следить, не обязательно владея ими") —
такой же общий список, как inventory.py (один человек пользуется ботом, не
per-Telegram-user хранилище). В отличие от инвентаря тут нет количества —
просто набор product_id.
"""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_REPO_DIR = Path(__file__).resolve().parent.parent
FAVORITES_FILE = str(_REPO_DIR / "favorites.json")


def load():
    """[product_id, ...]."""
    if not os.path.exists(FAVORITES_FILE):
        return []
    try:
        with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save(data):
    try:
        with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("Не удалось сохранить favorites.json")


def add(pid):
    data = load()
    if pid not in data:
        data.append(pid)
        save(data)
    return data


def remove(pid):
    data = load()
    if pid in data:
        data.remove(pid)
        save(data)
    return data
