#!/usr/bin/env python3
"""
Telegram-бот DreamPets MM2: постоянно работающий процесс (не GitHub Actions!).

Что делает:
  1. Интерактивное меню — команда /start или /menu показывает инлайн-кнопки
     по категориям (Godly / Ancient / Unique). По нажатию бот показывает
     текущие цены предметов этой категории, отсортированные от дорогих
     к дешёвым, с постраничной навигацией.
  2. Фоновая проверка цен (как в mm2_price_tracker.py) — раз в
     CHECK_INTERVAL_SEC сравнивает текущий каталог с прошлым снапшотом и
     присылает автоматическое уведомление о подешевевших/подорожавших
     предметах (тех же трёх редкостей).

Требует постоянно запущенный процесс (VPS / Render / Railway / systemd и т.п.) —
в отличие от mm2_price_tracker.py, этот скрипт НЕ предназначен для запуска
через GitHub Actions с --once, потому что кнопки должны отвечать в реальном
времени, а не раз в 5 минут.

Переменные окружения:
  TELEGRAM_BOT_TOKEN  — токен бота (обязательно)
  TELEGRAM_CHAT_ID    — куда слать автоматические уведомления (опционально;
                        без него фоновая проверка просто не будет их слать,
                        а меню по кнопкам работает всё равно)
  CHECK_INTERVAL_SEC  — интервал фоновой проверки цен в секундах (по умолчанию 300)

Запуск:
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python mm2_telegram_bot.py
"""

import asyncio
import logging
import os
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import mm2_price_tracker as tracker

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("mm2-bot")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "300"))

# Сколько секунд кэшируем каталог, чтобы клики по кнопкам не долбили API
CATALOG_CACHE_TTL_SEC = 60

PAGE_SIZE = 15

CATEGORIES = [
    ("godly", "🟡 Godly"),
    ("ancient", "🔵 Ancient"),
    ("unique", "🟣 Unique"),
]
CATEGORY_LABELS = dict(CATEGORIES)

# ---------- Кэш каталога ----------

_catalog_cache = {"snapshot": None, "fetched_at": 0.0}


def get_cached_snapshot(force=False):
    now = time.time()
    if force or _catalog_cache["snapshot"] is None or now - _catalog_cache["fetched_at"] > CATALOG_CACHE_TTL_SEC:
        products = tracker.fetch_all_products()
        if products:
            _catalog_cache["snapshot"] = tracker.normalize(products)
            _catalog_cache["fetched_at"] = now
    return _catalog_cache["snapshot"] or {}


def items_for_category(rarity):
    snapshot = get_cached_snapshot()
    items = [
        item for item in snapshot.values()
        if (item.get("rare") or "").lower() == rarity and item.get("price") is not None
    ]
    items.sort(key=lambda x: x["price"], reverse=True)
    return items


# ---------- Клавиатуры ----------

def menu_keyboard():
    rows = [[InlineKeyboardButton(label, callback_data=f"cat:{key}:0")] for key, label in CATEGORIES]
    return InlineKeyboardMarkup(rows)


def category_keyboard(rarity, page, total_pages):
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"cat:{rarity}:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{max(total_pages, 1)}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"cat:{rarity}:{page + 1}"))

    rows = [nav]
    rows.append([InlineKeyboardButton("📋 Меню", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


# ---------- Форматирование ----------

def format_category_page(rarity, page):
    items = items_for_category(rarity)
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * PAGE_SIZE: (page + 1) * PAGE_SIZE]

    label = CATEGORY_LABELS.get(rarity, rarity)
    lines = [f"{label} — {total} предметов\n"]
    start_num = page * PAGE_SIZE + 1
    for i, item in enumerate(chunk, start=start_num):
        lines.append(f"{i}. {item['name_en']} — <b>{item['price']:.2f}₽</b>")

    if not chunk:
        lines.append("Пусто.")

    return "\n".join(lines), page, total_pages


# ---------- Хендлеры ----------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выбери категорию:", reply_markup=menu_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    if data == "noop":
        return

    if data == "menu":
        await query.edit_message_text("Выбери категорию:", reply_markup=menu_keyboard())
        return

    if data.startswith("cat:"):
        _, rarity, page_str = data.split(":")
        page = int(page_str)
        try:
            text, page, total_pages = await asyncio.to_thread(format_category_page, rarity, page)
        except Exception as e:
            log.exception("Не удалось получить каталог")
            await query.edit_message_text(f"Не удалось получить данные: {e}", reply_markup=menu_keyboard())
            return

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=category_keyboard(rarity, page, total_pages),
        )


# ---------- Фоновая проверка цен (замена GitHub Actions cron) ----------

def run_price_check_once():
    log.info("Проверяю цены...")
    products = tracker.fetch_all_products()
    if not products:
        log.warning("Не удалось получить каталог с API.")
        return

    new_snapshot = tracker.normalize(products)
    old_data = tracker.load_history()

    if old_data is not None:
        old_snapshot = old_data.get("products", {})
        drops, rises = tracker.compare(old_snapshot, new_snapshot)
        message = tracker.build_telegram_message(drops, rises)
        if message:
            tracker.send_telegram_message(message)
            log.info("Уведомление отправлено (%d подешевело, %d подорожало)", len(drops), len(rises))

    tracker.save_history(new_snapshot)
    tracker.append_price_log(new_snapshot)
    tracker.update_meta(new_snapshot)

    _catalog_cache["snapshot"] = new_snapshot
    _catalog_cache["fetched_at"] = time.time()


async def price_check_loop():
    while True:
        try:
            await asyncio.to_thread(run_price_check_once)
        except Exception:
            log.exception("Ошибка в фоновой проверке цен")
        await asyncio.sleep(CHECK_INTERVAL_SEC)


async def post_init(app: Application):
    app.create_task(price_check_loop())


def main():
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler(["start", "menu"], start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))

    log.info("Бот запущен. Проверка цен каждые %d сек.", CHECK_INTERVAL_SEC)
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
