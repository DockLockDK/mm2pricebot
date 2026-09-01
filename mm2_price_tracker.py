#!/usr/bin/env python3
"""
MM2 DreamPets.GG price tracker.

Что делает:
  1. Забирает весь каталог оружия (и опционально питомцев) с API DreamPets.
  2. Сравнивает цены с прошлым запуском (история хранится в history.json).
  3. Печатает в консоль, у каких предметов упала цена (и на сколько).
  4. Сохраняет новый снапшот для следующего сравнения.

Запуск:
  python mm2_price_tracker.py

Чтобы гонять регулярно — повесь на cron (Linux/Mac) или Планировщик задач (Windows),
например каждые 5-10 минут.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ---------- Настройки ----------

API_URL = "https://mm2.dreampets.gg/api/sales/v1/sales/products"
PER_PAGE = 72
CURRENCY = "rub"
SORT = "popularity"

# Только оружие ("weapon") или все категории (weapon+pet+etc)
ONLY_WEAPONS = True

# Порог: сообщать только если цена изменилась минимум на столько процентов
MIN_CHANGE_PERCENT = 1.0

# Интервал между проверками в режиме постоянного мониторинга (секунды) — используется
# только при локальном запуске без --once. На GitHub Actions запуск идёт по cron.
CHECK_INTERVAL_SEC = 120

# ---------- Telegram ----------
# Токен бота и ID чата берутся из переменных окружения (безопаснее, чем хардкодить в коде).
# Локально можно задать через переменные окружения ОС, на GitHub Actions — через Secrets.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# Куда сохранять последний снапшот (для быстрого сравнения)
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

# Куда дописывать ПОЛНУЮ историю цен по времени (по одной строке JSON на каждую проверку)
PRICE_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "price_history.jsonl")

# Куда сохранять метаданные предметов (название, редкость и т.д.), чтобы не дублировать их в каждой строке лога
META_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "products_meta.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://dreampets.gg",
    "Referer": "https://dreampets.gg/",
}

REQUEST_TIMEOUT = 15
PAGE_DELAY_SEC = 0.3  # небольшая пауза между страницами, чтобы не долбить API слишком часто


# ---------- Забор данных ----------

def fetch_all_products():
    """Проходит по всем страницам API и возвращает список товаров."""
    all_products = []
    page = 0

    while True:
        params = {
            "min_price": 0,
            "max_price": 0,
            "currency": CURRENCY,
            "sort": SORT,
            "per_page": PER_PAGE,
            "page": page,
        }
        try:
            resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[!] Ошибка запроса страницы {page}: {e}")
            break

        data = resp.json()
        products = data.get("products", [])

        if not products:
            break

        all_products.extend(products)
        page += 1
        time.sleep(PAGE_DELAY_SEC)

        # защита от бесконечного цикла, если API вдруг всегда что-то отдаёт
        if page > 200:
            print("[!] Слишком много страниц, останавливаюсь на всякий случай.")
            break

    return all_products


def normalize(products):
    """
    Приводит список товаров к словарю {product_id: {...}} для удобного сравнения.
    Если ONLY_WEAPONS=True — оставляет только type == "weapon".
    """
    result = {}
    for entry in products:
        p = entry.get("product", {})
        if ONLY_WEAPONS and p.get("type") != "weapon":
            continue

        pid = entry.get("product_id")
        if not pid:
            continue

        result[pid] = {
            "name_ru": p.get("names_by_locale", {}).get("ru", p.get("name")),
            "name_en": p.get("name"),
            "category": p.get("category"),
            "rare": p.get("rare"),
            "chroma": p.get("chroma", False),
            "price": entry.get("price"),
        }
    return result


# ---------- История и сравнение ----------

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return None
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_history(snapshot):
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "products": snapshot,
    }
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def append_price_log(snapshot):
    """
    Дописывает одну строку в price_history.jsonl: временная метка + цены всех товаров.
    Файл растёт со временем, но хранит только id -> price, так что весит немного.
    """
    line = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prices": {pid: item["price"] for pid, item in snapshot.items()},
    }
    with open(PRICE_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def update_meta(snapshot):
    """
    Обновляет файл с метаданными предметов (название, редкость, категория).
    Эта информация почти не меняется, поэтому храним отдельно от истории цен.
    """
    meta = {}
    if os.path.exists(META_FILE):
        try:
            with open(META_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            meta = {}

    for pid, item in snapshot.items():
        meta[pid] = {
            "name_ru": item["name_ru"],
            "name_en": item["name_en"],
            "category": item["category"],
            "rare": item["rare"],
            "chroma": item["chroma"],
        }

    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def get_item_history(product_id):
    """
    Возвращает список (timestamp, price) для конкретного product_id из price_history.jsonl.
    Удобно, если потом захочешь посмотреть график цены одного предмета.
    """
    if not os.path.exists(PRICE_LOG_FILE):
        return []

    result = []
    with open(PRICE_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            price = entry.get("prices", {}).get(product_id)
            if price is not None:
                result.append((entry["timestamp"], price))
    return result


# Показываем изменения только по этим редкостям (значения из API — нижний регистр)
TARGET_RARITIES = {"godly", "ancient", "unique"}


def compare(old_snapshot, new_snapshot):
    """
    Возвращает (drops, rises) — списки предметов из TARGET_RARITIES, которые
    подешевели / подорожали, каждый отсортирован по цене (от большей к меньшей).
    """
    drops = []
    rises = []

    for pid, new_item in new_snapshot.items():
        old_item = old_snapshot.get(pid)
        if not old_item:
            continue  # новый товар, не с чем сравнивать

        if (new_item.get("rare") or "").lower() not in TARGET_RARITIES:
            continue

        old_price = old_item.get("price")
        new_price = new_item.get("price")

        if old_price is None or new_price is None or old_price <= 0:
            continue

        if new_price == old_price:
            continue

        change_percent = (new_price - old_price) / old_price * 100  # + рост, - падение

        if abs(change_percent) < MIN_CHANGE_PERCENT:
            continue

        entry = {
            "name_ru": new_item["name_ru"],
            "name_en": new_item["name_en"],
            "rare": new_item["rare"],
            "category": new_item["category"],
            "old_price": old_price,
            "new_price": new_price,
            "change_percent": change_percent,
        }

        if change_percent < 0:
            drops.append(entry)
        else:
            rises.append(entry)

    drops.sort(key=lambda x: x["new_price"], reverse=True)  # от дорогих к дешёвым
    rises.sort(key=lambda x: x["new_price"], reverse=True)  # от дорогих к дешёвым
    return drops, rises


# ---------- Вывод ----------

def send_telegram_message(text):
    """Отправляет сообщение в Telegram. Если токен/chat_id не заданы — просто пропускает."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    # Telegram режет сообщения длиннее 4096 символов — бьём на части, если нужно
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [text]

    for chunk in chunks:
        try:
            resp = requests.post(
                TELEGRAM_API_URL,
                data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                print(f"[!] Telegram API вернул ошибку: {resp.status_code} {resp.text}")
        except requests.RequestException as e:
            print(f"[!] Не удалось отправить сообщение в Telegram: {e}")


def build_telegram_message(drops, rises):
    """Собирает текст сообщения для Telegram из списков падений/роста."""
    if not drops and not rises:
        return None

    lines = []

    if drops:
        lines.append("📉 <b>Подешевело:</b>")
        for d in drops:
            lines.append(
                f"• {d['name_en']} ({d['rare']}): {d['old_price']:.2f}₽ → "
                f"<b>{d['new_price']:.2f}₽</b> ({d['change_percent']:+.1f}%)"
            )
        lines.append("")

    if rises:
        lines.append("📈 <b>Подорожало:</b>")
        for r in rises:
            lines.append(
                f"• {r['name_en']} ({r['rare']}): {r['old_price']:.2f}₽ → "
                f"<b>{r['new_price']:.2f}₽</b> ({r['change_percent']:+.1f}%)"
            )

    return "\n".join(lines)


def print_table(items, label):
    print(f"\n{label}: {len(items)}\n")
    if not items:
        return
    print(f"{'Название':<25} {'Редкость':<12} {'Было':>10} {'Стало':>10} {'Изм.':>9}")
    print("-" * 70)
    for d in items:
        print(
            f"{d['name_en']:<25} {d['rare']:<12} "
            f"{d['old_price']:>9.2f}₽ {d['new_price']:>9.2f}₽ "
            f"{d['change_percent']:>+8.1f}%"
        )


def print_changes(drops, rises):
    if not drops and not rises:
        print("Изменений в ценах не найдено.")
        return
    print_table(drops, "📉 Подешевело")
    print_table(rises, "📈 Подорожало")


# ---------- Main ----------

def run_once():
    """Один цикл: забрать каталог, сравнить с прошлым снапшотом, сохранить новый."""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Забираю каталог с DreamPets...")
    products = fetch_all_products()

    if not products:
        print("[!] Не удалось получить данные. Проверь интернет / API мог измениться.")
        return

    new_snapshot = normalize(products)
    print(f"Получено предметов: {len(new_snapshot)} (только оружие: {ONLY_WEAPONS})")

    old_data = load_history()

    if old_data is None:
        print("Это первый запуск — истории для сравнения ещё нет. Сохраняю снапшот.")
    else:
        old_snapshot = old_data.get("products", {})
        old_time = old_data.get("timestamp", "неизвестно")
        print(f"Сравниваю с прошлым снапшотом от {old_time}")
        drops, rises = compare(old_snapshot, new_snapshot)
        print_changes(drops, rises)

        message = build_telegram_message(drops, rises)
        if message:
            send_telegram_message(message)
            print("Уведомление отправлено в Telegram.")

    save_history(new_snapshot)
    append_price_log(new_snapshot)
    update_meta(new_snapshot)


def print_item_history(name_query):
    """Ищет предмет по названию (ru или en, без учёта регистра) и печатает его историю цен."""
    if not os.path.exists(META_FILE):
        print("Файл метаданных ещё не создан — запусти скрипт хотя бы раз без --history.")
        return

    with open(META_FILE, "r", encoding="utf-8") as f:
        meta = json.load(f)

    query = name_query.lower()
    matches = [
        (pid, m) for pid, m in meta.items()
        if query in m["name_ru"].lower() or query in m["name_en"].lower()
    ]

    if not matches:
        print(f"Предмет с названием '{name_query}' не найден в сохранённых метаданных.")
        return

    for pid, m in matches:
        history = get_item_history(pid)
        print(f"\n=== {m['name_ru']} ({m['name_en']}) — {m['rare']}, {m['category']} ===")
        if not history:
            print("История пуста.")
            continue
        for ts, price in history:
            print(f"  {ts}   {price:.2f}₽")


def main():
    # Посмотреть историю конкретного предмета: python mm2_price_tracker.py --history "Icewing"
    if "--history" in sys.argv:
        idx = sys.argv.index("--history")
        if idx + 1 < len(sys.argv):
            print_item_history(sys.argv[idx + 1])
        else:
            print("Укажи название предмета: --history \"Icewing\"")
        return

    # Разовый запуск: python mm2_price_tracker.py --once
    if "--once" in sys.argv:
        run_once()
        return

    print(f"Запускаю постоянный мониторинг, интервал: {CHECK_INTERVAL_SEC} сек.")
    print("Останови в любой момент через Ctrl+C.\n")

    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[!] Непредвиденная ошибка: {e}")

        try:
            time.sleep(CHECK_INTERVAL_SEC)
        except KeyboardInterrupt:
            print("\nОстановлено пользователем.")
            break


if __name__ == "__main__":
    main()
