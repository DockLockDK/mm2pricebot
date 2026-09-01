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

import json
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


# ---------------------------------------------------------------------------
# Legacy-маркетплейс (mm2-legacy) — второй, более старый каталог на том же
# сайте. Пространство ID у него не совпадает с основным (через него нельзя
# построить ни фото, ни ссылку "Купить" на CDN основного каталога), но одни и
# те же предметы можно сопоставить по (name, category, rare, chroma) — эти
# поля совпадают в обоих API. Используется только для сравнения цен: если тут
# дешевле, показываем это как альтернативный вариант покупки.
LEGACY_API_URL = "https://mm2.dreampets.gg/api/sales/v1/sales/products"
LEGACY_BUY_URL_TMPL = "https://dreampets.gg/mm2-legacy/product/{slug}/{product_id}"
LEGACY_FETCH_PER_PAGE = 5000


def fetch_legacy_products():
    """Одним запросом забирает весь legacy-каталог. Возвращает список сырых dict'ов."""
    params = {
        "min_price": 0,
        "max_price": 0,
        "currency": CURRENCY,
        "sort": "popularity",
        "per_page": LEGACY_FETCH_PER_PAGE,
        "page": 0,
    }
    try:
        resp = requests.get(LEGACY_API_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[!] Ошибка запроса legacy-каталога: {e}")
        return []

    try:
        data = resp.json()
    except ValueError as e:
        print(f"[!] Не удалось разобрать ответ legacy-API как JSON: {e}")
        return []

    return data.get("products", [])


def match_key(name, category, rare, chroma):
    """Ключ сопоставления одного и того же предмета между основным и legacy
    каталогами — оба API отдают одинаковые name/category/rare/chroma, но разные
    product_id, так что сравнивать нужно по этой связке."""
    return ((name or "").strip().lower(), category, rare, bool(chroma))


def normalize_legacy(products):
    """
    Приводит legacy-каталог к словарю {match_key: {...}}, где match_key —
    (name.lower(), category, rare, chroma). Если два предмета дают одинаковый
    ключ (редкий случай дублей), остаётся последний — это приемлемо для задачи
    сравнения цен.
    """
    result = {}
    for p in products:
        product = p.get("product") or {}
        if ONLY_WEAPONS and product.get("type") != "weapon":
            continue

        pid = p.get("product_id") or product.get("product_id")
        name = product.get("name")
        if not pid or not name:
            continue

        price = p.get("price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None

        category = product.get("category")
        rare = product.get("rare")
        chroma = bool(product.get("chroma", False))

        result[match_key(name, category, rare, chroma)] = {
            "product_id": pid,
            "name": name,
            "category": category,
            "rare": rare,
            "chroma": chroma,
            "price": price,
        }
    return result


def legacy_buy_url(product_id, name):
    return LEGACY_BUY_URL_TMPL.format(slug=slugify(name), product_id=product_id)


# ---------------------------------------------------------------------------
# Community value-сайт (mm2values.com) — это НЕ магазин. Там нет ни кнопки
# "купить", ни реальных денег — только условная "Value", ориентир комьюнити
# для трейдинга (сколько предмет "стоит" в сделках между игроками).
# Используется исключительно как справочная информация на карточке предмета
# и НИКОГДА не участвует в сравнении цен "где дешевле купить".
#
# supremevalues.com — второй такой сайт — сюда сознательно не подключён:
# он стоит за анти-бот защитой Incapsula, которая блокирует любой запрос без
# реального браузера с JS (не вопрос частоты запросов — блокирует и один
# аккуратный запрос раз в 5 минут). Обойти можно только headless-браузером
# (Playwright) — решили не тащить эту зависимость на сервер ради одного
# источника, раз mm2values.com даёт то же самое без проблем.
VALUE_SITE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

# Ограничиваемся тем, что реально отслеживает бот (см. CATEGORIES в webapp/server.py) —
# на обоих сайтах есть куда больше категорий (Pets/Misc/Chromas и т.д.), но они
# нам не нужны.
MM2VALUES_URL_TMPL = "https://mm2values.com/?p={category}"
MM2VALUES_CATEGORIES = ["godly", "ancient", "unique"]

_MM2VALUES_ITEM_RE = re.compile(
    r"<b>([^<]+)</b><br> Value: ([^<]+)<br>Range: ([^<]*)<br>"
    r"Demand: ([^-]+) - Rarity: ([^<]+)<br>Stability: ([^<]+)<hr>"
)


def normalize_name(name):
    """Ключ для сопоставления одного и того же предмета между dreampets и
    mm2values — сайты по-разному пишут одно и то же имя
    ('Icewing' / 'Ice Wing', 'Niks Scythe' / \"Nik's Scythe\"), поэтому
    оставляем только буквы/цифры в нижнем регистре."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _parse_value_number(raw):
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def fetch_mm2values():
    """Забирает Value/Demand/Rarity/Stability для godly/ancient/unique с
    mm2values.com. Возвращает {normalize_name(имя): {...}}."""
    result = {}
    for category in MM2VALUES_CATEGORIES:
        url = MM2VALUES_URL_TMPL.format(category=category)
        try:
            resp = requests.get(url, headers=VALUE_SITE_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[!] Ошибка запроса mm2values ({category}): {e}")
            continue

        for m in _MM2VALUES_ITEM_RE.finditer(resp.text):
            name, value_raw, _range, demand, rarity, stability = (g.strip() for g in m.groups())
            key = normalize_name(name)
            if not key:
                continue
            result[key] = {
                "name": name,
                "value": _parse_value_number(value_raw),
                "value_raw": value_raw,
                "demand": demand,
                "rarity": rarity,
                "stability": stability,
                "url": url,
            }
    return result



