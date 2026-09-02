#!/usr/bin/env python3
"""
Работа с накопленной историей цен: текущий каталог, legacy-каталог и
Community Value (mm2values.com) — чтение/дозапись JSONL-логов, свечи и
временные ряды для графиков, а также "снапшот на N секунд назад" для
расчёта "было -> стало" за выбранный в интерфейсе период (см. WINDOW_OPTIONS).

Хранит текущие живые снапшоты legacy-каталога и mm2values (обновляются в
фоне — см. update_legacy()/update_mm2values(), их дёргает run_price_check_once()
в server.py после каждого похода за живыми данными).
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent.parent
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))

import mm2_api
import mm2_price_tracker as tracker

log = logging.getLogger(__name__)

PRICE_LOG_READ_LIMIT = 20000  # сколько последних строк price_history.jsonl читать за раз
VALUE_LOG_READ_LIMIT = 20000  # то же самое для value_history.jsonl (community value)
CANDLE_BUCKET_SEC = 3600  # свечи по умолчанию (реально всегда переопределяется CHART_BUCKET_SECONDS)

# История legacy-цен по времени — тот же принцип, что и price_history.jsonl,
# но ключ не product_id (у legacy-каталога своё, несовпадающее пространство ID),
# а строка от mm2_api.match_key(...) (см. legacy_key_str). Нужна, чтобы когда
# legacy дешевле текущего каталога, "было"/% изменения считались по ЕГО
# собственной истории, а не по истории текущего каталога (который мог вообще
# не двигаться) — иначе на карточке дешёвая legacy-цена показывалась бы рядом
# с "было" и "%" от другого, не относящегося к ней источника.
LEGACY_PRICE_LOG_FILE = str(_REPO_DIR / "legacy_price_history.jsonl")

# Куда копим историю Community value (mm2values.com) по времени — отдельный лог,
# формат как у price_history.jsonl, но по ключу normalize_name(имя) вместо
# product_id (у mm2values нет своих ID, только имена). Нужен только чтобы
# рисовать график изменения value на карточке предмета — сама текущая величина
# берётся из _mm2values_index, тут только история для графика.
VALUE_LOG_FILE = str(_REPO_DIR / "value_history.jsonl")

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

# Тот же выбор периода задаёт и то, что показывают графики на карточке предмета:
# отрезаем историю на глубину окна и укрупняем свечи/точки, чтобы на "1 год" не
# пытаться нарисовать десятки тысяч 5-минутных точек, а на "1 час" не схлопывать
# всё в одну часовую свечу.
CHART_BUCKET_SECONDS = {
    "1m": 60,
    "5m": 60,
    "1h": 300,
    "3h": 900,
    "1d": 3600,
    "1w": 4 * 3600,
    "1mo": 24 * 3600,
    "1q": 24 * 3600,
    "1y": 7 * 24 * 3600,
}


def resolve_window(window):
    return WINDOW_SECONDS.get(window, WINDOW_SECONDS[DEFAULT_WINDOW])


# Снапшот legacy-каталога (dreampets.gg/mm2-legacy) для сравнения цен, ключ —
# mm2_api.match_key(name, category, rare, chroma). Снапшот community
# value-сайта (mm2values.com) — ключ mm2_api.normalize_name(name). Оба
# обновляются вызовом update_legacy()/update_mm2values() ниже.
_legacy_index = {}
_mm2values_index = {}


def update_legacy(legacy_index):
    """Обновляет живой снапшот legacy-каталога и дописывает точку в его
    историю по времени. Возвращает количество предметов — для логирования
    вызывающим кодом."""
    global _legacy_index
    _legacy_index = legacy_index
    _append_legacy_price_log(legacy_index)
    return len(legacy_index)


def update_mm2values(mm2values_index):
    """Обновляет живой снапшот mm2values.com и дописывает точку в его историю
    по времени. Возвращает количество предметов — для логирования вызывающим
    кодом."""
    global _mm2values_index
    _mm2values_index = mm2values_index
    _append_value_log(mm2values_index)
    return len(mm2values_index)


# ---------- Чтение накопленных данных ----------

def read_price_log(limit_lines=PRICE_LOG_READ_LIMIT):
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


def _find_entry_at_or_before(log_lines, cutoff):
    """Последняя запись лога с timestamp <= cutoff, идя с конца (обычно рядом
    с концом списка — так дешевле для окон вроде '5 мин'/'1 час'). None, если
    во всей истории нет ни одной точки настолько старой (значит окно длиннее,
    чем накопленная история)."""
    for entry in reversed(log_lines):
        try:
            dt = datetime.fromisoformat(entry["timestamp"])
        except (KeyError, ValueError):
            continue
        if dt <= cutoff:
            return entry
    return None


def snapshot_at(window_seconds):
    """Снапшот цен текущего каталога на момент 'window_seconds секунд назад'.
    Пусто, если такой точки в истории ещё нет (окно длиннее накопленной
    истории) — вызывающий код в этом случае просто не покажет 'было'/%."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    entry = _find_entry_at_or_before(read_price_log(), cutoff)
    if entry is None:
        return {}
    meta = load_meta()
    snap = {}
    for pid, price in entry.get("prices", {}).items():
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
    """JSON-строка от match_key(...) — стабильный ключ словаря для файлового лога."""
    return json.dumps(list(key), ensure_ascii=False)


def _read_legacy_price_log(limit_lines=PRICE_LOG_READ_LIMIT):
    if not os.path.exists(LEGACY_PRICE_LOG_FILE):
        return []
    with open(LEGACY_PRICE_LOG_FILE, "r", encoding="utf-8") as f:
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


def _append_legacy_price_log(legacy_index):
    """Дописывает одну строку в legacy_price_history.jsonl: {match_key_str: price}."""
    prices = {
        legacy_key_str(key): v["price"]
        for key, v in legacy_index.items() if v.get("price") is not None
    }
    if not prices:
        return
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "prices": prices}
    try:
        with open(LEGACY_PRICE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("Не удалось дописать legacy_price_history.jsonl")


def legacy_snapshot_at(window_seconds):
    """{match_key_str: цена} на момент 'window_seconds секунд назад' по legacy-каталогу."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    entry = _find_entry_at_or_before(_read_legacy_price_log(), cutoff)
    return entry.get("prices", {}) if entry else {}


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
    name_key = mm2_api.normalize_name(item.get("name"))
    community_values = []
    mm2v = _mm2values_index.get(name_key)
    if mm2v and mm2v.get("value_raw"):
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


def build_candles(pid, bucket_sec=CANDLE_BUCKET_SEC, window_seconds=None):
    """window_seconds ограничивает историю глубиной выбранного периода (None —
    вся накопленная история)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds) if window_seconds else None
    log_lines = read_price_log()
    points = []
    for entry in log_lines:
        price = entry.get("prices", {}).get(pid)
        if price is None:
            continue
        try:
            dt = datetime.fromisoformat(entry["timestamp"])
        except (KeyError, ValueError):
            continue
        if cutoff is not None and dt < cutoff:
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


def _read_value_log(limit_lines=VALUE_LOG_READ_LIMIT):
    if not os.path.exists(VALUE_LOG_FILE):
        return []
    with open(VALUE_LOG_FILE, "r", encoding="utf-8") as f:
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


def _append_value_log(mm2values_index):
    """Дописывает одну строку в value_history.jsonl: {normalize_name(имя): value}
    для всех предметов, у которых mm2values дал числовое значение."""
    values = {
        key: v["value"] for key, v in mm2values_index.items() if v.get("value") is not None
    }
    if not values:
        return
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "values": values}
    try:
        with open(VALUE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("Не удалось дописать value_history.jsonl")


def build_value_series(name_key, bucket_sec=None, window_seconds=None):
    """История Community value (mm2values) для графика: [{time, value}, ...] по
    возрастанию времени. window_seconds ограничивает глубину историей выбранного
    периода (None — вся история); bucket_sec укрупняет точки тем же способом,
    что и build_candles (последняя точка бакета), чтобы длинные периоды не
    отправляли в браузер десятки тысяч сырых 5-минутных значений."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds) if window_seconds else None
    points = []
    for entry in _read_value_log():
        value = entry.get("values", {}).get(name_key)
        if value is None:
            continue
        try:
            dt = datetime.fromisoformat(entry["timestamp"])
        except (KeyError, ValueError):
            continue
        if cutoff is not None and dt < cutoff:
            continue
        points.append((dt, value))
    points.sort(key=lambda p: p[0])

    if not points:
        return []

    if not bucket_sec:
        return [{"time": int(dt.timestamp()), "value": v} for dt, v in points]

    buckets = {}
    order = []
    for dt, v in points:
        bucket_ts = int(dt.timestamp() // bucket_sec) * bucket_sec
        if bucket_ts not in buckets:
            order.append(bucket_ts)
        buckets[bucket_ts] = v  # последнее значение в бакете

    return [{"time": ts, "value": buckets[ts]} for ts in order]


def price_history_avg(pid, days):
    """Средняя цена предмета по price_history.jsonl за последние `days` дней
    (той же серии текущего каталога, что копится при каждой проверке).
    Возвращает (среднее, число_точек) — вызывающий сам решает, достаточно ли
    точек, чтобы доверять этому среднему."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    values = []
    for entry in read_price_log():
        try:
            dt = datetime.fromisoformat(entry["timestamp"])
        except (KeyError, ValueError):
            continue
        if dt < cutoff:
            continue
        price = entry.get("prices", {}).get(pid)
        if price is not None:
            values.append(price)

    if not values:
        return None, 0
    return sum(values) / len(values), len(values)
