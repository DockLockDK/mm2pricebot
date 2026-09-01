#!/usr/bin/env python3
"""
Постоянно работающий сервис DreamPets MM2 (НЕ для GitHub Actions — нужен
процесс, живущий 24/7: VPS, Render, Railway, systemd и т.п.).

Делает три вещи в одном asyncio event loop'е:
  1. Telegram-бот: команда /start (и /app, /menu) присылает кнопку
     "Открыть каталог", которая открывает мини-приложение (Telegram Web App).
  2. HTTP API + статика мини-приложения (см. webapp/static/index.html):
     карточки предметов по категориям (Godly/Ancient/Unique), список "сильно
     изменившихся в цене" на главном экране (по всем редкостям), у каждого
     предмета — фото, текущая/прошлая цена, график цены свечками и ссылка
     "Купить" на DreamPets.
  3. Фоновая проверка цен (как mm2_price_tracker.py --once, но по циклу) —
     раз в CHECK_INTERVAL_SEC сравнивает каталог с прошлым снапшотом и шлёт
     push-уведомления в Telegram о godly/ancient/unique (как раньше).

Переменные окружения:
  TELEGRAM_BOT_TOKEN  — токен бота (обязательно для команды /start)
  TELEGRAM_CHAT_ID    — куда слать автоматические push-уведомления (опционально)
  WEBAPP_URL          — публичный https-адрес этого сервиса (обязательно для
                        кнопки "Открыть каталог" — Telegram Web App требует
                        настоящий https-домен, localhost не подходит)
  CHECK_INTERVAL_SEC  — интервал фоновой проверки цен, сек (по умолчанию 300)
  PORT                — порт HTTP-сервера (по умолчанию 8000)

Запуск:
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... WEBAPP_URL=https://your-host.example \
    python webapp/server.py
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
sys.path.insert(0, str(REPO_DIR))  # чтобы видеть mm2_api.py / mm2_price_tracker.py из корня репо

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import mm2_api
import mm2_price_tracker as tracker

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("mm2-webapp")

STATIC_DIR = BASE_DIR / "static"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").rstrip("/")
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "300"))
PORT = int(os.environ.get("PORT", "8000"))

CATEGORIES = [
    ("godly", "🟡 Godly"),
    ("ancient", "🔵 Ancient"),
    ("unique", "🟣 Unique"),
]
CATEGORY_LABELS = dict(CATEGORIES)

MOVERS_LIMIT = 20
CANDLE_BUCKET_SEC = 3600  # часовые свечи
PRICE_LOG_READ_LIMIT = 20000  # сколько последних строк price_history.jsonl читать за раз

app = FastAPI(title="MM2 Price Bot API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- Чтение накопленных данных ----------

def _read_price_log(limit_lines=PRICE_LOG_READ_LIMIT):
    if not os.path.exists(tracker.PRICE_LOG_FILE):
        return []
    with open(tracker.PRICE_LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    for line in lines[-limit_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _load_meta():
    if not os.path.exists(tracker.META_FILE):
        return {}
    try:
        with open(tracker.META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _current_snapshot():
    return (tracker.load_history() or {}).get("products", {})


def _previous_snapshot():
    """Снапшот на один цикл проверки раньше текущего — используется для 'было'/движения цен."""
    log_lines = _read_price_log()
    if len(log_lines) < 2:
        return {}
    prev_prices = log_lines[-2].get("prices", {})
    meta = _load_meta()
    snap = {}
    for pid, price in prev_prices.items():
        m = meta.get(pid, {})
        snap[pid] = {
            "name": m.get("name", "?"),
            "rare": m.get("rare"),
            "category": m.get("category"),
            "chroma": m.get("chroma", False),
            "price": price,
        }
    return snap


def item_view(pid, item, old_snapshot):
    old_item = old_snapshot.get(pid) or {}
    old_price = old_item.get("price")
    price = item.get("price")
    change_percent = None
    if price is not None and old_price not in (None, 0):
        change_percent = (price - old_price) / old_price * 100
    return {
        "id": pid,
        "name": item.get("name"),
        "rare": item.get("rare"),
        "category": item.get("category"),
        "chroma": item.get("chroma", False),
        "price": price,
        "prev_price": old_price,
        "change_percent": change_percent,
        "image": mm2_api.image_url(pid),
        "buy_url": mm2_api.buy_url(pid, item.get("name")),
    }


def build_candles(pid, bucket_sec=CANDLE_BUCKET_SEC):
    log_lines = _read_price_log()
    points = []
    for entry in log_lines:
        price = entry.get("prices", {}).get(pid)
        if price is None:
            continue
        try:
            dt = datetime.fromisoformat(entry["timestamp"])
        except (KeyError, ValueError):
            continue
        points.append((dt, price))

    if not points:
        return []

    points.sort(key=lambda x: x[0])

    buckets = {}
    order = []
    for dt, price in points:
        bucket_ts = int(dt.timestamp() // bucket_sec) * bucket_sec
        if bucket_ts not in buckets:
            buckets[bucket_ts] = {"open": price, "high": price, "low": price, "close": price}
            order.append(bucket_ts)
        else:
            b = buckets[bucket_ts]
            b["close"] = price
            b["high"] = max(b["high"], price)
            b["low"] = min(b["low"], price)

    return [
        {"time": ts, **buckets[ts]}
        for ts in order
    ]


# ---------- API ----------

@app.get("/api/menu")
def api_menu():
    snapshot = _current_snapshot()
    old_snapshot = _previous_snapshot()

    drops, rises = tracker.compare(old_snapshot, snapshot, rarities=None)
    movers = drops + rises
    movers.sort(key=lambda x: abs(x["change_percent"]), reverse=True)
    movers = movers[:MOVERS_LIMIT]

    movers_view = []
    for m in movers:
        item = snapshot.get(m["product_id"])
        if item:
            movers_view.append(item_view(m["product_id"], item, old_snapshot))

    categories = []
    for key, label in CATEGORIES:
        count = sum(
            1 for it in snapshot.values()
            if (it.get("rare") or "").lower() == key and it.get("price") is not None
        )
        categories.append({"key": key, "label": label, "count": count})

    return {"categories": categories, "movers": movers_view}


@app.get("/api/category/{rarity}")
def api_category(rarity: str):
    rarity = rarity.lower()
    snapshot = _current_snapshot()
    old_snapshot = _previous_snapshot()

    items = [
        item_view(pid, item, old_snapshot)
        for pid, item in snapshot.items()
        if (item.get("rare") or "").lower() == rarity and item.get("price") is not None
    ]
    items.sort(key=lambda x: x["price"], reverse=True)

    return {"rarity": rarity, "label": CATEGORY_LABELS.get(rarity, rarity), "items": items}


@app.get("/api/item/{pid}")
def api_item(pid: str):
    snapshot = _current_snapshot()
    item = snapshot.get(pid)
    if not item:
        raise HTTPException(status_code=404, detail="item not found")

    old_snapshot = _previous_snapshot()
    view = item_view(pid, item, old_snapshot)
    view["candles"] = build_candles(pid)
    return view


# ---------- Статика мини-приложения ----------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ---------- Фоновая проверка цен ----------

def run_price_check_once():
    log.info("Проверяю цены...")
    products = mm2_api.fetch_all_products()
    if not products:
        log.warning("Не удалось получить каталог с API.")
        return

    new_snapshot = mm2_api.normalize(products)
    old_data = tracker.load_history()

    if old_data is not None:
        old_snapshot = old_data.get("products", {})
        drops, rises = tracker.compare(old_snapshot, new_snapshot)  # только godly/ancient/unique
        message = tracker.build_telegram_message(drops, rises)
        if message:
            tracker.send_telegram_message(message)
            log.info("Push отправлен (%d подешевело, %d подорожало)", len(drops), len(rises))

    tracker.save_history(new_snapshot)
    tracker.append_price_log(new_snapshot)
    tracker.update_meta(new_snapshot)


async def price_check_loop():
    while True:
        try:
            await asyncio.to_thread(run_price_check_once)
        except Exception:
            log.exception("Ошибка в фоновой проверке цен")
        await asyncio.sleep(CHECK_INTERVAL_SEC)


# ---------- Telegram-бот (только команда /start -> кнопка мини-приложения) ----------

ptb_app = None

if BOT_TOKEN:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
    from telegram.ext import Application, CommandHandler, ContextTypes

    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not WEBAPP_URL:
            await update.message.reply_text(
                "Мини-приложение ещё не задеплоено: на сервере не задан WEBAPP_URL."
            )
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛒 Открыть каталог", web_app=WebAppInfo(url=WEBAPP_URL)),
        ]])
        await update.message.reply_text(
            "Каталог MM2: цены, графики, ссылки на покупку 👇",
            reply_markup=keyboard,
        )

    ptb_app = Application.builder().token(BOT_TOKEN).build()
    ptb_app.add_handler(CommandHandler(["start", "app", "menu"], start_cmd))
else:
    log.warning("TELEGRAM_BOT_TOKEN не задан — бот не запустится, будет работать только API.")


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(price_check_loop())
    if ptb_app is not None:
        await ptb_app.initialize()
        await ptb_app.start()
        await ptb_app.updater.start_polling(allowed_updates=["message"])
        log.info("Telegram-бот запущен (long polling).")
    if not WEBAPP_URL:
        log.warning("WEBAPP_URL не задан — кнопка 'Открыть каталог' работать не будет.")


@app.on_event("shutdown")
async def on_shutdown():
    if ptb_app is not None:
        await ptb_app.updater.stop()
        await ptb_app.stop()
        await ptb_app.shutdown()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
