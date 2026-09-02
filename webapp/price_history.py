#!/usr/bin/env python3
"""
Работа с накопленной историей цен: текущий каталог, legacy-каталог и
Community Value (mm2values.com) — свечи и временные ряды для графиков, а
также "снапшот на N секунд назад" для расчёта "было -> стало" за выбранный
в интерфейсе период (см. WINDOW_OPTIONS). Хранилище — SQLite (webapp/pricedb.py);
при первом запуске после обновления один раз переносит данные из старых
price_history.jsonl/legacy_price_history.jsonl/value_history.jsonl, если они
ещё лежат на диске (см. _migrate_from_jsonl_if_needed ниже).

Хранит текущие живые снапшоты legacy-каталога и mm2values (обновляются в
фоне — см. update_legacy()/update_mm2values(), их дёргает run_price_check_once()
в server.py после каждого похода за живыми данными).
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent.parent
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))

import mm2_api
import mm2_price_tracker as tracker

import pricedb

log = logging.getLogger(__name__)

CANDLE_BUCKET_SEC = 3600  # свечи по умолчанию (реально всегда переопределяется CHART_BUCKET_SECONDS)

# Пути СТАРЫХ .jsonl-логов — только для одноразовой миграции в SQLite при
# первом запуске новой версии (см. _migrate_from_jsonl_if_needed). Новых
# записей сюда больше не пишем.
_OLD_LEGACY_PRICE_LOG_FILE = str(_REPO_DIR / "legacy_price_history.jsonl")
_OLD_VALUE_LOG_FILE = str(_REPO_DIR / "value_history.jsonl")

# ---------- Период сравнения "было -> стало" (выбирается в интерфейсе) ----------
# Раньше "было" всегда значило "на прошлом цикле проверки" — теперь это
# настоящее временное окно: ищем в истории последнюю точку не позже, чем
# (сейчас − окно), и сравниваем текущую цену с ней. Работает одинаково для
# движений на главном экране, сетки категории и карточки предмета.
WINDOW_OPTIONS = [
    ("1m", 60, "1 мин"),
    ("5m", 300, "5 мин"),
    ("1h", 3600, "1 час"),
    ("3h", 3 * 3600, "3 часа"),
    ("1d", 24 * 3600, "Сутки"),
    ("1w", 7 * 24 * 3600, "Неделя"),
    ("1mo", 30 * 24 * 3600, "Месяц"),
    ("1q", 91 * 24 * 3600, "Квартал"),
    ("1y", 365 * 24 * 3600, "Год"),
]
WINDOW_SECONDS = {key: sec for key, sec, _ in WINDOW_OPTIONS}
DEFAULT_WINDOW = "5m"

# Графики на карточке предмета НЕ равны выбранному периоду сравнения "было
# -> стало" — если бы график показывал ровно последние "5 минут" (как сам
# выбранный период), там почти всегда был бы 0-1 точек, даже если реальное
# падение цены случилось час назад: график был бы пустым ровно в тот момент,
# когда в шапке показан честный "-99%". Поэтому у коротких периодов (до суток
# включительно) график всё равно показывает минимум сутки истории — см.
# CHART_WINDOW_SECONDS; длинные периоды (неделя и больше) показывают ровно
# себя, там смысл "растянуть план" уже есть. bucket_sec — во сколько
# укрупняются точки/свечи (совпадает с шагом уплотнения в pricedb.compact(),
# чтобы бакеты на графике не были мельче, чем реально хранящиеся точки).
CHART_MIN_WINDOW_SECONDS = 24 * 3600  # график никогда не показывает меньше суток

CHART_WINDOW_SECONDS = {
    "1m": CHART_MIN_WINDOW_SECONDS,
    "5m": CHART_MIN_WINDOW_SECONDS,
    "1h": CHART_MIN_WINDOW_SECONDS,
    "3h": CHART_MIN_WINDOW_SECONDS,
    "1d": CHART_MIN_WINDOW_SECONDS,
    "1w": 7 * 24 * 3600,
    "1mo": 30 * 24 * 3600,
    "1q": 91 * 24 * 3600,
    "1y": 365 * 24 * 3600,
}

CHART_BUCKET_SECONDS = {
    "1m": 3600,
    "5m": 3600,
    "1h": 3600,
    "3h": 3600,
    "1d": 3600,
    "1w": 4 * 3600,
    "1mo": 24 * 3600,
    "1q": 24 * 3600,
    "1y": 7 * 24 * 3600,
}


def resolve_chart_window(window):
    return CHART_WINDOW_SECONDS.get(window, CHART_MIN_WINDOW_SECONDS)


def resolve_window(window):
    return WINDOW_SECONDS.get(window, WINDOW_SECONDS[DEFAULT_WINDOW])


# Снапшот legacy-каталога (dreampets.gg/mm2-legacy) для сравнения цен, ключ —
# mm2_api.match_key(name, category, rare, chroma). Снапшот community
# value-сайта (mm2values.com) — ключ mm2_api.normalize_name(name). Оба
# обновляются вызовом update_legacy()/update_mm2values() ниже.
_legacy_index = {}
_mm2values_index = {}
_roblox_game_info = {}


def update_legacy(legacy_index):
    """Обновляет живой снапшот legacy-каталога и дописывает точку в его
    историю по времени. Возвращает количество предметов — для логирования
    вызывающим кодом."""
    global _legacy_index
    _legacy_index = legacy_index
    now_ts = int(time.time())
    prices = {legacy_key_str(key): v["price"] for key, v in legacy_index.items() if v.get("price") is not None}
    pricedb.insert_legacy_points(now_ts, prices)
    return len(legacy_index)


def update_mm2values(mm2values_index):
    """Обновляет живой снапшот mm2values.com и дописывает точку в его историю
    по времени. Возвращает количество предметов — для логирования вызывающим
    кодом."""
    global _mm2values_index
    _mm2values_index = mm2values_index
    now_ts = int(time.time())
    values = {key: v["value"] for key, v in mm2values_index.items() if v.get("value") is not None}
    pricedb.insert_value_points(now_ts, values)
    return len(mm2values_index)


def update_roblox_game_info(info):
    """Обновляет живой снапшот 'когда MM2 последний раз обновлялась в Roblox'
    (см. mm2_api.fetch_roblox_game_info) — время патча самой игры, а не
    изменения каталога dreampets."""
    global _roblox_game_info
    if info:
        _roblox_game_info = info


def roblox_game_info():
    return _roblox_game_info or None


def append_price_points(snapshot):
    """Дописывает точку истории текущего каталога — вызывается раз в цикл
    проверки цен, вместо старого tracker.append_price_log (JSONL)."""
    now_ts = int(time.time())
    prices = {pid: item["price"] for pid, item in snapshot.items() if item.get("price") is not None}
    pricedb.insert_price_points(now_ts, prices)


def maybe_compact():
    """Раз в несколько часов уплотняет старые точки (см. pricedb.compact) —
    дёргать раз в цикл проверки цен, сама функция решает, пора ли реально
    что-то делать."""
    pricedb.maybe_compact(int(time.time()))


def load_meta():
    if not os.path.exists(tracker.META_FILE):
        return {}
    try:
        with open(tracker.META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def current_snapshot():
    return (tracker.load_history() or {}).get("products", {})


def snapshot_at(window_seconds):
    """Снапшот цен текущего каталога на момент 'window_seconds секунд назад'.
    Пусто, если такой точки в истории ещё нет (окно длиннее накопленной
    истории) — вызывающий код в этом случае просто не покажет 'было'/%."""
    cutoff_ts = int(time.time()) - window_seconds
    prices = pricedb.price_snapshot_at(cutoff_ts)
    if not prices:
        return {}
    meta = load_meta()
    snap = {}
    for pid, price in prices.items():
        m = meta.get(pid, {})
        snap[pid] = {
            "name": m.get("name", "?"),
            "rare": m.get("rare"),
            "category": m.get("category"),
            "chroma": m.get("chroma", False),
            "price": price,
        }
    return snap


def legacy_key_str(key):
    """JSON-строка от match_key(...) — стабильный ключ строки для базы/лога."""
    return json.dumps(list(key), ensure_ascii=False)


def legacy_snapshot_at(window_seconds):
    """{match_key_str: цена} на момент 'window_seconds секунд назад' по legacy-каталогу."""
    cutoff_ts = int(time.time()) - window_seconds
    return pricedb.legacy_snapshot_at(cutoff_ts)


def item_view(pid, item, old_snapshot, legacy_old=None):
    legacy_old = legacy_old or {}
    old_item = old_snapshot.get(pid) or {}
    old_price = old_item.get("price")
    price = item.get("price")
    change_percent = None
    if price is not None and old_price not in (None, 0):
        change_percent = (price - old_price) / old_price * 100

    view = {
        "id": pid,
        "name": item.get("name"),
        "rare": item.get("rare"),
        "category": item.get("category"),
        "chroma": item.get("chroma", False),
        "price": price,
        "best_price": price,
        "prev_price": old_price,
        "change_percent": change_percent,
        "image": mm2_api.image_url(pid),
        "buy_url": mm2_api.buy_url(pid, item.get("name")),
        "legacy_price": None,
        "legacy_buy_url": None,
        "cheaper_source": "current",
    }

    key = mm2_api.match_key(item.get("name"), item.get("category"), item.get("rare"), item.get("chroma"))
    legacy = _legacy_index.get(key)
    if legacy and legacy.get("price") is not None:
        view["legacy_price"] = legacy["price"]
        view["legacy_buy_url"] = mm2_api.legacy_buy_url(legacy["product_id"], legacy["name"])
        if price is None or legacy["price"] < price:
            view["cheaper_source"] = "legacy"
            # Крупный ценник всегда показывает самый дешёвый вариант из двух каталогов —
            # значит и "было"/% должны быть от ЭТОЙ же цены, а не от текущего каталога
            # (который мог вообще не двигаться, пока дешевле был legacy).
            view["best_price"] = legacy["price"]
            old_legacy_price = legacy_old.get(legacy_key_str(key))
            if old_legacy_price not in (None, 0):
                view["prev_price"] = old_legacy_price
                view["change_percent"] = (legacy["price"] - old_legacy_price) / old_legacy_price * 100
            else:
                view["prev_price"] = None
                view["change_percent"] = None

    # Community value (mm2values.com) — НЕ цена покупки, справочный ориентир
    # комьюнити. Никогда не влияет на best_price/cheaper_source.
    # mm2values.com отдаёт данные ТОЛЬКО по годным/ancient/unique категориям
    # (см. mm2_api.MM2VALUES_CATEGORIES), а сопоставление идёт просто по
    # нормализованному имени без учёта редкости — при совпадении имени с
    # предметом другой редкости (например, "Ornament" есть и как Common Knife,
    # и как Godly Knife) дешёвому common-предмету иначе подставился бы
    # community value от чужого godly-тёзки. Поэтому подставляем значение,
    # только если у самого предмета редкость вообще входит в покрытие сайта.
    name_key = mm2_api.normalize_name(item.get("name"))
    community_values = []
    mm2v = _mm2values_index.get(name_key)
    if mm2v and mm2v.get("value_raw") and (item.get("rare") or "").lower() in mm2_api.MM2VALUES_CATEGORIES:
        cv_entry = {
            "source": "mm2values",
            "label": "MM2Values",
            "value_raw": mm2v["value_raw"],
            "demand": mm2v.get("demand"),
            "rarity": mm2v.get("rarity"),
            "stability": mm2v.get("stability"),
            "url": mm2v["url"],
        }
        community_values.append(cv_entry)
    view["community_values"] = community_values

    return view


def _bucket(rows, bucket_sec, kind):
    """rows: [(ts, value), ...] по возрастанию времени. kind='candle' группирует
    в OHLC-свечи, kind='last' — берёт последнее значение бакета (для линии value)."""
    if not rows:
        return []
    if kind == "last" and not bucket_sec:
        return [{"time": ts, "value": v} for ts, v in rows]

    buckets = {}
    order = []
    for ts, v in rows:
        bucket_ts = (ts // bucket_sec) * bucket_sec
        if kind == "candle":
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"open": v, "high": v, "low": v, "close": v}
                order.append(bucket_ts)
            else:
                b = buckets[bucket_ts]
                b["close"] = v
                b["high"] = max(b["high"], v)
                b["low"] = min(b["low"], v)
        else:
            if bucket_ts not in buckets:
                order.append(bucket_ts)
            buckets[bucket_ts] = v  # последнее значение в бакете

    if kind == "candle":
        return [{"time": ts, **buckets[ts]} for ts in order]
    return [{"time": ts, "value": buckets[ts]} for ts in order]


def build_candles(pid, bucket_sec=CANDLE_BUCKET_SEC, window_seconds=None):
    """window_seconds ограничивает историю глубиной выбранного периода (None —
    вся накопленная история)."""
    since_ts = int(time.time()) - window_seconds if window_seconds else None
    rows = pricedb.price_series(pid, since_ts=since_ts)
    return _bucket(rows, bucket_sec, kind="candle")


def build_legacy_candles(match_key_str, bucket_sec=CANDLE_BUCKET_SEC, window_seconds=None):
    """То же самое, но по истории legacy-каталога — используется вместо
    build_candles(), когда legacy сейчас дешевле (см. item_view/cheaper_source):
    иначе график показывал бы историю текущего каталога, а крупная цена и %
    над ним — legacy, разные серии выглядели бы как рассинхрон."""
    since_ts = int(time.time()) - window_seconds if window_seconds else None
    rows = pricedb.legacy_price_series(match_key_str, since_ts=since_ts)
    return _bucket(rows, bucket_sec, kind="candle")


def build_value_series(name_key, bucket_sec=None, window_seconds=None):
    """История Community value (mm2values) для графика: [{time, value}, ...] по
    возрастанию времени. window_seconds ограничивает глубину историей выбранного
    периода (None — вся история); bucket_sec укрупняет точки (последняя точка
    бакета), чтобы длинные периоды не отправляли в браузер лишние точки."""
    since_ts = int(time.time()) - window_seconds if window_seconds else None
    rows = pricedb.value_series(name_key, since_ts=since_ts)
    return _bucket(rows, bucket_sec, kind="last")


def price_history_avg(pid, days):
    """Средняя цена предмета за последние `days` дней. Возвращает (среднее,
    число_точек) — вызывающий сам решает, достаточно ли точек, чтобы
    доверять этому среднему."""
    since_ts = int(time.time()) - days * 86400
    avg, count = pricedb.price_avg(pid, since_ts)
    if not count:
        return None, 0
    return avg, count


# ---------- Одноразовая миграция старых .jsonl в SQLite ----------

def _iso_to_ts(iso_str):
    try:
        return int(datetime.fromisoformat(iso_str).timestamp())
    except (TypeError, ValueError):
        return None


def _migrate_price_jsonl():
    if not os.path.exists(tracker.PRICE_LOG_FILE):
        return
    log.info("Миграция %s -> SQLite...", tracker.PRICE_LOG_FILE)
    conn = pricedb.get_conn()
    rows = []
    with open(tracker.PRICE_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _iso_to_ts(entry.get("timestamp"))
            if ts is None:
                continue
            for pid, price in entry.get("prices", {}).items():
                if price is not None:
                    rows.append((ts, pid, price))
    if rows:
        conn.executemany("INSERT INTO price_points (ts, product_id, price) VALUES (?, ?, ?)", rows)
        conn.commit()
    log.info("Перенесено %d точек цены из %s.", len(rows), tracker.PRICE_LOG_FILE)


def _migrate_legacy_jsonl():
    if not os.path.exists(_OLD_LEGACY_PRICE_LOG_FILE):
        return
    log.info("Миграция %s -> SQLite...", _OLD_LEGACY_PRICE_LOG_FILE)
    conn = pricedb.get_conn()
    rows = []
    with open(_OLD_LEGACY_PRICE_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _iso_to_ts(entry.get("timestamp"))
            if ts is None:
                continue
            for key, price in entry.get("prices", {}).items():
                if price is not None:
                    rows.append((ts, key, price))
    if rows:
        conn.executemany("INSERT INTO legacy_price_points (ts, match_key, price) VALUES (?, ?, ?)", rows)
        conn.commit()
    log.info("Перенесено %d точек legacy-цены из %s.", len(rows), _OLD_LEGACY_PRICE_LOG_FILE)


def _migrate_value_jsonl():
    if not os.path.exists(_OLD_VALUE_LOG_FILE):
        return
    log.info("Миграция %s -> SQLite...", _OLD_VALUE_LOG_FILE)
    conn = pricedb.get_conn()
    rows = []
    with open(_OLD_VALUE_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _iso_to_ts(entry.get("timestamp"))
            if ts is None:
                continue
            for key, value in entry.get("values", {}).items():
                if value is not None:
                    rows.append((ts, key, value))
    if rows:
        conn.executemany("INSERT INTO value_points (ts, name_key, value) VALUES (?, ?, ?)", rows)
        conn.commit()
    log.info("Перенесено %d точек value из %s.", len(rows), _OLD_VALUE_LOG_FILE)


def init():
    """Создаёт таблицы SQLite и (один раз, если таблицы ещё пустые, а старые
    .jsonl ещё лежат на диске) переносит в них накопленную историю. Вызывать
    один раз при старте сервиса."""
    pricedb.init_db()
    counts = pricedb.row_counts()
    if counts.get("price_points", 0) == 0:
        _migrate_price_jsonl()
    if counts.get("legacy_price_points", 0) == 0:
        _migrate_legacy_jsonl()
    if counts.get("value_points", 0) == 0:
        _migrate_value_jsonl()
