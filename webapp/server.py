#!/usr/bin/env python3
"""
Постоянно работающий сервис DreamPets MM2 (НЕ для GitHub Actions — нужен
процесс, живущий 24/7: VPS, Render, Railway, systemd и т.п.).

Делает три вещи в одном asyncio event loop'е:
  1. Telegram-бот (webapp/telegram_bot.py): команда /start (и /app, /menu)
     присылает кнопку "Открыть каталог", которая открывает мини-приложение
     (Telegram Web App).
  2. HTTP API + статика мини-приложения (см. webapp/static/): карточки
     предметов по категориям (Godly/Ancient/Unique), список "сильно
     изменившихся в цене" на главном экране, у каждого предмета — фото,
     текущая/прошлая цена за выбранный период, график цены свечками и график
     Community Value, ссылка "Купить" на DreamPets, а также сравнение с
     legacy-маркетплейсом (dreampets.gg/mm2-legacy) — если там дешевле,
     показываем это отдельно. Вся история цен и расчёт периодов —
     webapp/price_history.py.
  3. Фоновая проверка цен (как mm2_price_tracker.py --once, но по циклу) —
     раз в CHECK_INTERVAL_SEC сравнивает каталог с прошлым снапшотом,
     обновляет legacy/community-value индексы и шлёт push-уведомления о
     сильном падении цены (webapp/alerts.py).

Переменные окружения:
  TELEGRAM_BOT_TOKEN  — токен бота (обязательно для команды /start)
  TELEGRAM_CHAT_ID    — куда слать автоматические push-уведомления (опционально)
  WEBAPP_URL          — публичный https-адрес этого сервиса (обязательно для
                        кнопки "Открыть каталог" — Telegram Web App требует
                        настоящий https-домен, localhost не подходит)
  CHECK_INTERVAL_SEC  — интервал фоновой проверки цен, сек (по умолчанию 300)
  PORT                — порт HTTP-сервера (по умолчанию 8000)
  DROP_ALERT_THRESHOLD_PERCENT, AVG_PRICE_WINDOW_DAYS — см. webapp/alerts.py

Запуск:
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... WEBAPP_URL=https://your-host.example \
    python webapp/server.py
"""

import asyncio
import hashlib
import logging
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
for _p in (str(REPO_DIR), str(BASE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import mm2_api
import mm2_price_tracker as tracker

import alerts
import inventory
import price_history
import telegram_bot

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

STATIC_DIR = BASE_DIR / "static"
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "300"))
PORT = int(os.environ.get("PORT", "8000"))

CATEGORIES = [
    ("godly", "🟡 Godly"),
    ("ancient", "🔵 Ancient"),
    ("unique", "🟣 Unique"),
]
CATEGORY_LABELS = dict(CATEGORIES)

MOVERS_LIMIT = 20

price_history.init()  # создаёт SQLite-таблицы и переносит старые .jsonl-логи, если ещё не перенесены

app = FastAPI(title="MM2 Price Bot API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- API ----------

@app.get("/api/windows")
def api_windows():
    return {
        "options": [{"key": k, "label": l} for k, _, l in price_history.WINDOW_OPTIONS],
        "default": price_history.DEFAULT_WINDOW,
    }


@app.get("/api/menu")
def api_menu(window: str = price_history.DEFAULT_WINDOW):
    window_sec = price_history.resolve_window(window)
    snapshot = price_history.current_snapshot()
    old_snapshot = price_history.snapshot_at(window_sec)
    legacy_old = price_history.legacy_snapshot_at(window_sec)

    drops, rises = tracker.compare(old_snapshot, snapshot, rarities=None)
    movers = drops + rises
    movers.sort(key=lambda x: abs(x["change_percent"]), reverse=True)
    movers = movers[:MOVERS_LIMIT]

    movers_view = []
    for m in movers:
        item = snapshot.get(m["product_id"])
        if item:
            movers_view.append(price_history.item_view(m["product_id"], item, old_snapshot, legacy_old))

    categories = []
    for key, label in CATEGORIES:
        count = sum(
            1 for it in snapshot.values()
            if (it.get("rare") or "").lower() == key and price_history.has_any_price(it)
        )
        categories.append({"key": key, "label": label, "count": count})

    resolved_window = window if window in price_history.WINDOW_SECONDS else price_history.DEFAULT_WINDOW
    return {
        "categories": categories,
        "movers": movers_view,
        "window": resolved_window,
        "game_update": price_history.roblox_game_info(),
        "news": price_history.mm2_news(),
    }


@app.get("/api/category/{rarity}")
def api_category(rarity: str, window: str = price_history.DEFAULT_WINDOW):
    rarity = rarity.lower()
    window_sec = price_history.resolve_window(window)
    snapshot = price_history.current_snapshot()
    old_snapshot = price_history.snapshot_at(window_sec)
    legacy_old = price_history.legacy_snapshot_at(window_sec)

    items = [
        price_history.item_view(pid, item, old_snapshot, legacy_old)
        for pid, item in snapshot.items()
        if (item.get("rare") or "").lower() == rarity and price_history.has_any_price(item)
    ]
    items.sort(key=lambda x: x["best_price"], reverse=True)

    resolved_window = window if window in price_history.WINDOW_SECONDS else price_history.DEFAULT_WINDOW
    return {
        "rarity": rarity,
        "label": CATEGORY_LABELS.get(rarity, rarity),
        "items": items,
        "window": resolved_window,
    }


@app.get("/api/item/{pid}")
def api_item(pid: str, window: str = price_history.DEFAULT_WINDOW):
    window_sec = price_history.resolve_window(window)
    snapshot = price_history.current_snapshot()
    item = snapshot.get(pid)
    if not item:
        raise HTTPException(status_code=404, detail="item not found")

    old_snapshot = price_history.snapshot_at(window_sec)
    legacy_old = price_history.legacy_snapshot_at(window_sec)
    view = price_history.item_view(pid, item, old_snapshot, legacy_old)

    resolved_window = window if window in price_history.WINDOW_SECONDS else price_history.DEFAULT_WINDOW
    bucket_sec = price_history.CHART_BUCKET_SECONDS.get(resolved_window, price_history.CANDLE_BUCKET_SEC)
    # Графики намеренно смотрят дальше, чем сам выбранный период сравнения
    # (см. CHART_WINDOW_SECONDS) — иначе на "5 мин"/"1 час" график почти всегда
    # пуст, даже если "было/стало" в шапке честно показывает сильное падение,
    # случившееся чуть раньше выбранного окна.
    chart_window_sec = price_history.resolve_chart_window(resolved_window)
    if view["cheaper_source"] == "legacy":
        # Крупная цена и % над графиком сейчас — из legacy-каталога (он дешевле),
        # значит и график должен показывать ЕГО историю, а не текущего каталога
        # (который мог вообще не двигаться, пока дешевле был legacy) — иначе
        # цифры сверху и линия на графике визуально не совпадают.
        key = mm2_api.match_key(item.get("name"), item.get("category"), item.get("rare"), item.get("chroma"))
        match_key_str = price_history.legacy_key_str(key)
        view["candles"] = price_history.build_legacy_candles(match_key_str, bucket_sec=bucket_sec, window_seconds=chart_window_sec)
    else:
        view["candles"] = price_history.build_candles(pid, bucket_sec=bucket_sec, window_seconds=chart_window_sec)
    for cv in view["community_values"]:
        if cv["source"] == "mm2values":
            name_key = mm2_api.normalize_name(item.get("name"))
            cv["history"] = price_history.build_value_series(name_key, bucket_sec=bucket_sec, window_seconds=chart_window_sec)
    view["window"] = resolved_window
    view["inventory_quantity"] = inventory.load().get(pid, 0)
    return view


SEARCH_LIMIT = 30


@app.get("/api/search")
def api_search(q: str = ""):
    """Поиск предмета по названию среди ВСЕГО каталога (любая редкость, не
    только Godly/Ancient/Unique) — общий пикер для инвентаря и калькулятора
    трейда, которым может быть нужен любой предмет, а не только отслеживаемые
    на основных экранах категории. Пустой запрос — не пустой список, а
    список для "пролистать" (самые дорогие сначала), чтобы предмет можно
    было выбрать просто скроллом, не вводя название целиком."""
    query = q.strip().lower()
    snapshot = price_history.current_snapshot()
    candidates = [(pid, item) for pid, item in snapshot.items() if item.get("price") is not None]
    if query:
        candidates = [(pid, item) for pid, item in candidates if query in (item.get("name") or "").lower()]
    candidates.sort(key=lambda pair: pair[1]["price"], reverse=True)

    results = []
    for pid, item in candidates[:SEARCH_LIMIT]:
        view = price_history.item_view(pid, item, {})
        results.append({
            "id": pid,
            "name": view["name"],
            "rare": view["rare"],
            "category": view["category"],
            "image": view["image"],
            "best_price": view["best_price"],
            "community_values": view["community_values"],
        })
    return {"items": results}


@app.get("/api/inventory")
def api_inventory():
    """Личный инвентарь — {pid: количество} из inventory.py, дополненный
    текущей ценой на момент запроса (не хранится статично, потому что цена
    двигается каждый цикл проверки)."""
    data = inventory.load()
    snapshot = price_history.current_snapshot()
    items = []
    total = 0.0
    for pid, quantity in data.items():
        item = snapshot.get(pid)
        if not item or item.get("price") is None:
            continue
        view = price_history.item_view(pid, item, {})
        subtotal = view["best_price"] * quantity
        total += subtotal
        items.append({
            "id": pid,
            "name": view["name"],
            "rare": view["rare"],
            "category": view["category"],
            "image": view["image"],
            "best_price": view["best_price"],
            "community_values": view["community_values"],
            "quantity": quantity,
            "subtotal": subtotal,
        })
    items.sort(key=lambda x: -x["subtotal"])
    return {"items": items, "total": total}


@app.post("/api/inventory/{pid}")
def api_inventory_set(pid: str, payload: dict = Body(...)):
    quantity = int(payload.get("quantity", 0))
    inventory.set_quantity(pid, quantity)
    return {"ok": True}


# ---------- Статика мини-приложения ----------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _static_version():
    """Хэш содержимого app.js+style.css — используется как ?v= в index.html,
    чтобы браузер/Telegram WebView гарантированно подтягивали новый файл
    после деплоя, а не отдавали закэшированную версию под тем же URL (баг
    класса "у меня всё ещё старый интерфейс", уже дважды путавший при
    проверке новых фич)."""
    h = hashlib.md5()
    for name in ("app.js", "style.css"):
        h.update((STATIC_DIR / name).read_bytes())
    return h.hexdigest()[:8]


_STATIC_VERSION = _static_version()


@app.get("/")
def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('/static/app.js"', f'/static/app.js?v={_STATIC_VERSION}"')
    html = html.replace('/static/style.css"', f'/static/style.css?v={_STATIC_VERSION}"')
    return HTMLResponse(html)


# ---------- Фоновая проверка цен ----------

def run_price_check_once():
    log.info("Проверяю цены...")
    products = mm2_api.fetch_all_products()
    if not products:
        log.warning("Не удалось получить каталог с API.")
        return

    new_snapshot = mm2_api.normalize(products)
    old_data = tracker.load_history()
    old_snapshot = old_data.get("products", {}) if old_data is not None else {}

    # Сначала обновляем legacy- и community-value индексы — алерты ниже (и
    # item_view внутри них) должны опираться на свежие данные, а не на
    # прошлый цикл.
    try:
        legacy_products = mm2_api.fetch_legacy_products()
        if legacy_products:
            count = price_history.update_legacy(mm2_api.normalize_legacy(legacy_products))
            log.info("Legacy-каталог обновлён (%d предметов).", count)
        else:
            log.warning("Не удалось получить legacy-каталог — сравнение цен временно недоступно.")
    except Exception:
        log.exception("Ошибка при обновлении legacy-каталога")

    try:
        idx = mm2_api.fetch_mm2values()
        if idx:
            count = price_history.update_mm2values(idx)
            log.info("mm2values.com обновлён (%d предметов).", count)
        else:
            log.warning("Не удалось получить данные с mm2values.com.")
    except Exception:
        log.exception("Ошибка при обновлении mm2values.com")

    try:
        game_info = mm2_api.fetch_roblox_game_info()
        if game_info:
            price_history.update_roblox_game_info(game_info)
        else:
            log.warning("Не удалось получить данные о MM2 из Roblox Games API.")
    except Exception:
        log.exception("Ошибка при обновлении данных Roblox Games API")

    try:
        colbe = mm2_api.fetch_colbe_latest_video()
        mmoexp = mm2_api.fetch_mmoexp_latest()
        price_history.update_mm2_news(colbe, mmoexp)
    except Exception:
        log.exception("Ошибка при обновлении новостей MM2 (Colbe/mmoexp)")

    try:
        funpay_listings = mm2_api.fetch_funpay_listings()
        count = price_history.update_funpay(funpay_listings, new_snapshot)
        log.info("FunPay: сопоставлено %d предметов (из %d лотов 'Предметы').", count, len(funpay_listings))
    except Exception:
        log.exception("Ошибка при обновлении цен FunPay")

    # Алерты о сильном падении цены — считаем ДО append_price_points() ниже,
    # чтобы "средняя за N дней" была за период ДО этого момента, а не включала
    # саму текущую (уже упавшую) цену.
    if old_data is not None:
        try:
            alerted_state = alerts.load_alerted_state()
            drop_alerts = alerts.build_drop_alerts(new_snapshot, old_snapshot, alerted_state)
            for alert in drop_alerts:
                alerts.send_drop_alert(alert)
                alerted_state[alert["pid"]] = alert["price"]
            if drop_alerts:
                alerts.save_alerted_state(alerted_state)
                log.info("Отправлено алертов о падении цены: %d", len(drop_alerts))
        except Exception:
            log.exception("Ошибка при формировании алертов о падении цены")

        try:
            underval_state = alerts.load_alerted_undervalue_state()
            underval_alerts = alerts.build_undervalue_alerts(new_snapshot, old_snapshot, underval_state)
            for alert in underval_alerts:
                alerts.send_undervalue_alert(alert)
                underval_state[alert["pid"]] = alert["view"]["best_price"]
            if underval_alerts:
                alerts.save_alerted_undervalue_state(underval_state)
                log.info("Отправлено алертов о цене ниже Community Value: %d", len(underval_alerts))
        except Exception:
            log.exception("Ошибка при формировании алертов о цене ниже Community Value")

    tracker.save_history(new_snapshot)
    price_history.append_price_points(new_snapshot)
    tracker.update_meta(new_snapshot)
    price_history.maybe_compact()


async def price_check_loop():
    while True:
        try:
            await asyncio.to_thread(run_price_check_once)
        except Exception:
            log.exception("Ошибка в фоновой проверке цен")
        await asyncio.sleep(CHECK_INTERVAL_SEC)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(price_check_loop())
    await telegram_bot.start()


@app.on_event("shutdown")
async def on_shutdown():
    await telegram_bot.stop()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
