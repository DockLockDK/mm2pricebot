#!/usr/bin/env python3
"""
SQLite-хранилище истории цен: текущий каталог, legacy-каталог, Community
Value (mm2values.com). Раньше это были три .jsonl-файла, дописываемые
каждый цикл проверки — рабочая схема, но с двумя проблемами на дистанции:
  1. Файл никогда не уменьшался, только рос — за месяцы работы это
     десятки-сотни МБ, и каждый запрос (график одного предмета, средняя за
     7 дней для алертов, "было -> стало" за период) читал ВЕСЬ файл заново.
  2. Чтение при этом ограничено последними *_LOG_READ_LIMIT строками —
     то есть после ~70 дней (20000 строк / 288 циклов в день при
     CHECK_INTERVAL_SEC=300) старая история физически ещё на диске, но
     приложение её уже не видит.

Здесь вместо "одна строка = весь каталог за цикл" — своя строка на каждую
(предмет, время, цена), с индексом (ключ, время). Запрос истории ОДНОГО
предмета не сканирует остальные ~900 — работает одинаково быстро что при
10 000 точек в базе, что при 10 000 000. "Снапшот на N секунд назад" для
всего каталога — два индексных запроса (найти нужный момент времени, потом
выбрать все строки с ним) вместо чтения файла целиком.

Retention (см. compact()): свежие данные хранятся с полным разрешением
проверки (обычно раз в 5 минут), после HOT_RETENTION_DAYS — почасовыми
точками, после WARM_RETENTION_DAYS (180 дней по умолчанию) — по одной
точке в день, и так хранится бессрочно. Гарантирует минимум
WARM_RETENTION_DAYS дней видимой истории для любого периода в интерфейсе,
а дальше просто грубее, а не пропадает совсем.
"""

import logging
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_REPO_DIR = Path(__file__).resolve().parent.parent
DB_FILE = str(_REPO_DIR / "price_history.db")

HOT_RETENTION_DAYS = 14     # полное разрешение проверки (см. CHECK_INTERVAL_SEC)
WARM_RETENTION_DAYS = 180   # почасовое разрешение; старше — по одной точке в день, бессрочно

_local = threading.local()

# Таблицы и их (столбец-ключ, столбец-значение) — единственное место, где эти
# имена перечислены как доверенные константы (никогда не из пользовательского
# ввода), поэтому подстановка имён таблиц/столбцов в SQL ниже безопасна.
_TABLES = {
    "price_points": ("product_id", "price"),
    "legacy_price_points": ("match_key", "price"),
    "value_points": ("name_key", "value"),
}


def _connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get_conn():
    """Одно соединение на поток. FastAPI обрабатывает синхронные роуты в
    потоках из пула, а sqlite3-соединение нельзя безопасно делить между
    потоками — у каждого свой в threading.local()."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS price_points (
            ts INTEGER NOT NULL,
            product_id TEXT NOT NULL,
            price REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_price_points_pid_ts ON price_points(product_id, ts);
        CREATE INDEX IF NOT EXISTS idx_price_points_ts ON price_points(ts);

        CREATE TABLE IF NOT EXISTS legacy_price_points (
            ts INTEGER NOT NULL,
            match_key TEXT NOT NULL,
            price REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_legacy_price_points_key_ts ON legacy_price_points(match_key, ts);
        CREATE INDEX IF NOT EXISTS idx_legacy_price_points_ts ON legacy_price_points(ts);

        CREATE TABLE IF NOT EXISTS value_points (
            ts INTEGER NOT NULL,
            name_key TEXT NOT NULL,
            value REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_value_points_key_ts ON value_points(name_key, ts);
        CREATE INDEX IF NOT EXISTS idx_value_points_ts ON value_points(ts);

        CREATE TABLE IF NOT EXISTS kv_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()


# ---------- запись ----------

def insert_price_points(ts, prices):
    """prices: {product_id: price}."""
    if not prices:
        return
    conn = get_conn()
    conn.executemany(
        "INSERT INTO price_points (ts, product_id, price) VALUES (?, ?, ?)",
        [(ts, pid, price) for pid, price in prices.items() if price is not None],
    )
    conn.commit()


def insert_legacy_points(ts, prices):
    """prices: {match_key_str: price}."""
    if not prices:
        return
    conn = get_conn()
    conn.executemany(
        "INSERT INTO legacy_price_points (ts, match_key, price) VALUES (?, ?, ?)",
        [(ts, key, price) for key, price in prices.items() if price is not None],
    )
    conn.commit()


def insert_value_points(ts, values):
    """values: {name_key: value}."""
    if not values:
        return
    conn = get_conn()
    conn.executemany(
        "INSERT INTO value_points (ts, name_key, value) VALUES (?, ?, ?)",
        [(ts, key, value) for key, value in values.items() if value is not None],
    )
    conn.commit()


# ---------- чтение ----------

def price_snapshot_at(cutoff_ts):
    """{product_id: price} — состояние ВСЕГО каталога на момент 'последняя
    точка не позже cutoff_ts'. {} если такой точки в истории ещё нет."""
    return _snapshot_at("price_points", "product_id", "price", cutoff_ts)


def legacy_snapshot_at(cutoff_ts):
    """{match_key: price} на момент 'последняя точка не позже cutoff_ts'."""
    return _snapshot_at("legacy_price_points", "match_key", "price", cutoff_ts)


def _snapshot_at(table, key_col, value_col, cutoff_ts):
    conn = get_conn()
    row = conn.execute(f"SELECT MAX(ts) FROM {table} WHERE ts <= ?", (cutoff_ts,)).fetchone()
    if row is None or row[0] is None:
        return {}
    target_ts = row[0]
    cur = conn.execute(f"SELECT {key_col}, {value_col} FROM {table} WHERE ts = ?", (target_ts,))
    return dict(cur.fetchall())


def price_series(product_id, since_ts=None):
    """[(ts, price), ...] по возрастанию времени для одного предмета."""
    conn = get_conn()
    if since_ts is not None:
        cur = conn.execute(
            "SELECT ts, price FROM price_points WHERE product_id = ? AND ts >= ? ORDER BY ts",
            (product_id, since_ts),
        )
    else:
        cur = conn.execute(
            "SELECT ts, price FROM price_points WHERE product_id = ? ORDER BY ts",
            (product_id,),
        )
    return cur.fetchall()


def legacy_price_series(match_key, since_ts=None):
    """[(ts, price), ...] по возрастанию времени для одного предмета в legacy-каталоге."""
    conn = get_conn()
    if since_ts is not None:
        cur = conn.execute(
            "SELECT ts, price FROM legacy_price_points WHERE match_key = ? AND ts >= ? ORDER BY ts",
            (match_key, since_ts),
        )
    else:
        cur = conn.execute(
            "SELECT ts, price FROM legacy_price_points WHERE match_key = ? ORDER BY ts",
            (match_key,),
        )
    return cur.fetchall()


def value_series(name_key, since_ts=None):
    """[(ts, value), ...] по возрастанию времени для одного предмета (community value)."""
    conn = get_conn()
    if since_ts is not None:
        cur = conn.execute(
            "SELECT ts, value FROM value_points WHERE name_key = ? AND ts >= ? ORDER BY ts",
            (name_key, since_ts),
        )
    else:
        cur = conn.execute(
            "SELECT ts, value FROM value_points WHERE name_key = ? ORDER BY ts",
            (name_key,),
        )
    return cur.fetchall()


def price_avg(product_id, since_ts):
    """(среднее, число_точек) цены предмета с since_ts до сейчас. (None, 0),
    если точек нет вовсе."""
    conn = get_conn()
    avg, count = conn.execute(
        "SELECT AVG(price), COUNT(*) FROM price_points WHERE product_id = ? AND ts >= ?",
        (product_id, since_ts),
    ).fetchone()
    return (avg, count or 0)


def price_min_max(product_id):
    """(мин, макс) цены предмета за всю накопленную историю. (None, None),
    если точек нет. Внимание: после compact() старые точки — это средние по
    часу/дню, а не честные min/max внутри бакета, так что для очень старой
    истории это приближение, а не абсолютно точный исторический экстремум."""
    conn = get_conn()
    row = conn.execute(
        "SELECT MIN(price), MAX(price) FROM price_points WHERE product_id = ?", (product_id,)
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def legacy_price_min_max(match_key):
    """То же самое, но по истории legacy-каталога."""
    conn = get_conn()
    row = conn.execute(
        "SELECT MIN(price), MAX(price) FROM legacy_price_points WHERE match_key = ?", (match_key,)
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


# ---------- уплотнение старых данных ----------

def maybe_compact(now_ts, min_interval_sec=6 * 3600):
    """compact(), но не чаще min_interval_sec — сам факт последнего запуска
    хранится в kv_meta, так что достаточно дёргать эту функцию на каждом
    цикле проверки цен: почти всегда это один дешёвый SELECT и выход."""
    conn = get_conn()
    row = conn.execute("SELECT value FROM kv_meta WHERE key = 'last_compact_ts'").fetchone()
    last = int(row[0]) if row else 0
    if now_ts - last < min_interval_sec:
        return
    compact(now_ts)
    conn.execute(
        "INSERT INTO kv_meta (key, value) VALUES ('last_compact_ts', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(now_ts),),
    )
    conn.commit()


def compact(now_ts):
    """Уплотняет данные старше HOT_RETENTION_DAYS до почасовых точек, а
    старше WARM_RETENTION_DAYS — до одной точки в день (среднее за бакет).
    Идемпотентно: повторное уплотнение уже уплотнённых точек не искажает
    их (среднее одной точки — она сама)."""
    hot_cutoff = now_ts - HOT_RETENTION_DAYS * 86400
    warm_cutoff = now_ts - WARM_RETENTION_DAYS * 86400

    conn = get_conn()
    for table, (key_col, value_col) in _TABLES.items():
        _compact_range(conn, table, key_col, value_col, bucket_sec=3600, lower=warm_cutoff, upper=hot_cutoff)
        _compact_range(conn, table, key_col, value_col, bucket_sec=86400, lower=None, upper=warm_cutoff)
    conn.commit()
    log.info("Уплотнение истории цен выполнено (hot<%dд, warm<%dд).", HOT_RETENTION_DAYS, WARM_RETENTION_DAYS)


def _compact_range(conn, table, key_col, value_col, bucket_sec, lower, upper):
    where = "ts < ?"
    params = [upper]
    if lower is not None:
        where += " AND ts >= ?"
        params.append(lower)

    conn.execute(f"DROP TABLE IF EXISTS _compact_agg")
    conn.execute(
        f"""
        CREATE TEMP TABLE _compact_agg AS
        SELECT {key_col} AS k, (ts / {bucket_sec}) * {bucket_sec} AS bucket_ts, AVG({value_col}) AS v
        FROM {table}
        WHERE {where}
        GROUP BY {key_col}, bucket_ts
        """,
        params,
    )
    conn.execute(f"DELETE FROM {table} WHERE {where}", params)
    conn.execute(
        f"INSERT INTO {table} (ts, {key_col}, {value_col}) SELECT bucket_ts, k, v FROM _compact_agg"
    )
    conn.execute("DROP TABLE _compact_agg")


def row_counts():
    """Для диагностики: {table: количество строк}."""
    conn = get_conn()
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in _TABLES}
