#!/usr/bin/env python3
"""
Общий слой доступа к каталогу DreamPets MM2 — используется и mm2_price_tracker.py
(разовые уведомления через GitHub Actions), и webapp/server.py (постоянный бот +
мини-приложение).

ВАЖНО: сайт использует маркет-API на mm2-test.dreampets.gg (несмотря на "-test" в
домене — это то, что реально дёргает сам сайт dreampets.gg, проверено через сетевые
запросы браузера). У этого API product_id совпадает с ID в CDN-картинках и в
ссылках на лоты (см. image_url/buy_url ниже). Старый API mm2.dreampets.gg/api/sales/...
тоже отвечает, но отдаёт другое пространство ID, к которому нельзя приклеить ни
фото, ни рабочую ссылку "Купить" — поэтому он больше не используется.
"""

import re

import requests

API_URL = "https://mm2-test.dreampets.gg/api/market/v1/market/products"
CDN_IMAGE_URL_TMPL = "https://cdn.dreampets.gg/mm2/catalog/products/{product_id}"
BUY_URL_TMPL = "https://dreampets.gg/mm2/product/{slug}/{product_id}"

CURRENCY = "rub"
SORT = "popularity_desc"
FETCH_LIMIT = 3000  # в каталоге ~1000 предметов, одним запросом забираем всё сразу

# Только оружие (weapon) или включая питомцев (pet) и прочее
ONLY_WEAPONS = True

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://dreampets.gg",
    "Referer": "https://dreampets.gg/",
}

REQUEST_TIMEOUT = 15


def fetch_all_products():
    """Одним запросом забирает весь каталог. Возвращает список сырых dict'ов от API."""
    params = {
        "search": "",
        "currency": CURRENCY,
        "sort": SORT,
        "limit": FETCH_LIMIT,
        "offset": 0,
    }
    try:
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[!] Ошибка запроса каталога: {e}")
        return []

    try:
        data = resp.json()
    except ValueError as e:
        print(f"[!] Не удалось разобрать ответ API как JSON: {e}")
        return []

    return data.get("products", [])


def normalize(products):
    """
    Приводит список товаров к словарю {product_id: {...}}.
    Пропускает не-оружие (если ONLY_WEAPONS=True). Предметы без активных лотов
    остаются в словаре с price=None — их нужно самостоятельно фильтровать там,
    где нужна именно цена (сравнение, карточки).
    """
    result = {}
    for p in products:
        if ONLY_WEAPONS and p.get("type") != "weapon":
            continue

        pid = p.get("product_id")
        if not pid:
            continue

        price = None
        raw_price = p.get("min_price")
        if raw_price is not None:
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                price = None

        result[pid] = {
            "name": p.get("name") or "?",
            "category": p.get("category"),
            "rare": p.get("rarity"),
            "chroma": bool(p.get("chroma", False)),
            "price": price,
        }
    return result


def slugify(name):
    """Часть ссылки на лот — сайт принимает вообще любой слаг, лишь бы ID был верный,
    так что тут не нужна побуквенная точность, только читаемость ссылки."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "item"


def image_url(product_id):
    return CDN_IMAGE_URL_TMPL.format(product_id=product_id)


def buy_url(product_id, name):
    return BUY_URL_TMPL.format(slug=slugify(name), product_id=product_id)
