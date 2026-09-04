#!/usr/bin/env python3
"""
Алерты (push в Telegram) — только Godly/Ancient (по просьбе пользователя
Unique исключён из уведомлений, хотя в мини-приложении остаётся как обычно).
Три независимых вида (свои пороги, состояния дедупа и настройки вкл/выкл —
см. webapp/notification_settings.py):
  1. Сильное падение цены относительно своей же истории (build_drop_alerts) —
     порог и окно среднего: DROP_ALERT_THRESHOLD_PERCENT/AVG_PRICE_WINDOW_DAYS.
  2. Сильный рост цены — зеркально падению (build_rise_alerts) — порог:
     RISE_ALERT_THRESHOLD_PERCENT.
  3. Цена заметно ниже Community Value с mm2values.com (build_undervalue_alerts)
     — порог: UNDERVALUE_ALERT_THRESHOLD_PERCENT. Предмет может попасть под
     несколько условий одновременно или только под одно — состояния дедупа
     раздельные.
"""

import json
import logging
import os
import sys
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent.parent
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))

import requests

import mm2_price_tracker as tracker
import notification_settings
import price_history

log = logging.getLogger(__name__)

# Шлём push ТОЛЬКО по этим редкостям (Unique — по просьбе пользователя исключён
# из уведомлений, хотя в мини-приложении остаётся как обычно).
ALERT_RARITIES = {"godly", "ancient"}
# На сколько % текущая цена должна быть НИЖЕ средней за AVG_PRICE_WINDOW_DAYS,
# чтобы это считалось "подешевело в разы" и стоило уведомления. Например,
# Celestial 7000₽ → 4000₽ — это примерно -43%, чуть выше порога по умолчанию.
DROP_ALERT_THRESHOLD_PERCENT = float(os.environ.get("DROP_ALERT_THRESHOLD_PERCENT", "35"))
AVG_PRICE_WINDOW_DAYS = int(os.environ.get("AVG_PRICE_WINDOW_DAYS", "7"))
# Не считаем "падением", если по предмету накопилось слишком мало точек истории
# (например, самый первый запуск) — иначе первая же цена окажется "средней".
ALERT_MIN_HISTORY_SAMPLES = 5
# Куда сохраняем, по какой цене последний раз алертили каждый предмет — чтобы
# не слать одно и то же уведомление заново на каждом цикле, пока цена стоит на
# месте (баг: без этого бот спамил один и тот же алерт каждые 5 минут, пока
# цена не отрастёт обратно). Не версионируем — рантайм-состояние.
ALERTED_STATE_FILE = str(_REPO_DIR / "alerted_drops.json")
# То же самое, но для алертов "дешевле своей Community Value" ниже — отдельный
# файл и состояние, потому что это независимое условие (предмет может упасть
# в цене и одновременно оказаться ниже Value, или только одно из двух).
ALERTED_UNDERVALUE_STATE_FILE = str(_REPO_DIR / "alerted_undervalue.json")
# И для алертов о сильном РОСТЕ цены (см. build_rise_alerts) — тоже отдельное
# состояние: предмет может упасть и вырасти в разные циклы, дедуп должен быть
# независимым для каждого направления.
ALERTED_RISE_STATE_FILE = str(_REPO_DIR / "alerted_rise.json")


def _load_json_state(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_json_state(path, state):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("Не удалось сохранить %s", path)


def load_alerted_state():
    """{pid: цена, по которой мы последний раз алертили этот предмет}."""
    return _load_json_state(ALERTED_STATE_FILE)


def save_alerted_state(state):
    _save_json_state(ALERTED_STATE_FILE, state)


def load_alerted_undervalue_state():
    return _load_json_state(ALERTED_UNDERVALUE_STATE_FILE)


def save_alerted_undervalue_state(state):
    _save_json_state(ALERTED_UNDERVALUE_STATE_FILE, state)


def load_alerted_rise_state():
    return _load_json_state(ALERTED_RISE_STATE_FILE)


def save_alerted_rise_state(state):
    _save_json_state(ALERTED_RISE_STATE_FILE, state)


def build_drop_alerts(new_snapshot, old_snapshot, alerted_state):
    """Ищет предметы Godly/Ancient, у которых текущая цена упала минимум на
    DROP_ALERT_THRESHOLD_PERCENT % ниже средней за AVG_PRICE_WINDOW_DAYS дней.
    Пропускает предмет, если мы уже слали алерт именно по этой цене (иначе
    бот шлёт одно и то же уведомление на каждом цикле, пока цена стоит на
    месте) — алерт повторится только когда цена изменится (упадёт ещё ниже
    или сначала отрастёт обратно и упадёт заново). Учитывает настройки
    пользователя (webapp/notification_settings.py) — можно выключить алерты о
    падении целиком или ограничить конкретными предметами."""
    settings = notification_settings.load()
    if not settings["drop_enabled"]:
        return []

    found = []
    for pid, item in new_snapshot.items():
        if (item.get("rare") or "").lower() not in ALERT_RARITIES:
            continue
        if not notification_settings.allows(settings, "drop", pid):
            continue

        price = item.get("price")
        if price is None:
            continue

        if alerted_state.get(pid) == price:
            continue

        avg_price, samples = price_history.price_history_avg(pid, days=AVG_PRICE_WINDOW_DAYS)
        if avg_price is None or avg_price <= 0 or samples < ALERT_MIN_HISTORY_SAMPLES:
            continue

        drop_percent = (price - avg_price) / avg_price * 100
        if drop_percent > -DROP_ALERT_THRESHOLD_PERCENT:
            continue

        found.append({
            "pid": pid,
            "price": price,
            "view": price_history.item_view(pid, item, old_snapshot),
            "avg_price": avg_price,
            "drop_percent": drop_percent,
        })

    found.sort(key=lambda a: a["drop_percent"])  # сильнее всего подешевевшие — первыми
    return found


def _send_telegram_alert(text, keyboard, image_url):
    """Общая отправка для всех видов алертов — с картинкой предмета
    (sendPhoto, подпись = текст), а если картинки нет или Telegram не смог её
    загрузить (CDN недоступен/нет фото у предмета), откатываемся на обычное
    текстовое sendMessage, чтобы алерт в любом случае дошёл."""
    if not tracker.TELEGRAM_BOT_TOKEN or not tracker.TELEGRAM_CHAT_ID:
        return

    if image_url:
        try:
            resp = requests.post(
                tracker.TELEGRAM_PHOTO_API_URL,
                data={
                    "chat_id": tracker.TELEGRAM_CHAT_ID,
                    "photo": image_url,
                    "caption": text,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(keyboard),
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return
            log.warning(
                "Telegram sendPhoto вернул ошибку, шлю текстом без картинки: %s %s",
                resp.status_code, resp.text,
            )
        except requests.RequestException:
            log.exception("Не удалось отправить алерт с картинкой в Telegram, шлю текстом")

    try:
        resp = requests.post(
            tracker.TELEGRAM_API_URL,
            data={
                "chat_id": tracker.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(keyboard),
            },
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning("Telegram API вернул ошибку при отправке алерта: %s %s", resp.status_code, resp.text)
    except requests.RequestException:
        log.exception("Не удалось отправить алерт в Telegram")


def send_drop_alert(alert):
    """Шлёт одно уведомление о сильном падении цены с кнопкой-ссылкой на лот."""
    view = alert["view"]
    avg_price = alert["avg_price"]
    best_price = view["best_price"]
    is_legacy = view["cheaper_source"] == "legacy"
    buy_url = view["legacy_buy_url"] if is_legacy else view["buy_url"]

    community_values = view.get("community_values") or []
    if community_values:
        value_text = " · ".join(f"{cv['label']}: {cv['value_raw']}" for cv in community_values)
    else:
        value_text = "нет данных"

    # Средняя и % всегда относятся к ЦЕНЕ ТЕКУЩЕГО КАТАЛОГА (alert['price']) —
    # именно её падение и обнаружил алерт (см. build_drop_alerts). best_price
    # может оказаться дешевле (legacy) прямо сейчас — это отдельная строка и
    # кнопка "Купить", а не тот же процент: иначе цифры не сходились бы (% и
    # цена из разных источников).
    text = (
        f"🔥 <b>{view['name']}</b> ({view['rare']}) сильно подешевело!\n\n"
        f"Средняя цена (за {AVG_PRICE_WINDOW_DAYS} дн.): {avg_price:.2f}₽\n"
        f"Сейчас в текущем каталоге: <b>{alert['price']:.2f}₽</b> ({alert['drop_percent']:+.1f}%)\n"
    )
    if is_legacy:
        text += f"Дешевле всего сейчас: <b>{best_price:.2f}₽</b> в Legacy-каталоге\n"
    text += f"Community value: {value_text}"

    keyboard = {"inline_keyboard": [[{"text": f"🛒 Купить за {best_price:.2f}₽", "url": buy_url}]]}
    _send_telegram_alert(text, keyboard, view.get("image"))


# На сколько % текущая цена должна быть ВЫШЕ средней за AVG_PRICE_WINDOW_DAYS
# дней, чтобы это считалось "сильно подорожало" — зеркально DROP_ALERT_THRESHOLD_PERCENT,
# только в обратную сторону.
RISE_ALERT_THRESHOLD_PERCENT = float(os.environ.get("RISE_ALERT_THRESHOLD_PERCENT", "35"))


def build_rise_alerts(new_snapshot, old_snapshot, alerted_state):
    """Зеркально build_drop_alerts, только ищет сильный РОСТ цены относительно
    средней за AVG_PRICE_WINDOW_DAYS дней. Тот же дедуп-приём и та же проверка
    настроек (можно выключить алерты о росте целиком или ограничить конкретными
    предметами, см. webapp/notification_settings.py)."""
    settings = notification_settings.load()
    if not settings["rise_enabled"]:
        return []

    found = []
    for pid, item in new_snapshot.items():
        if (item.get("rare") or "").lower() not in ALERT_RARITIES:
            continue
        if not notification_settings.allows(settings, "rise", pid):
            continue

        price = item.get("price")
        if price is None:
            continue

        if alerted_state.get(pid) == price:
            continue

        avg_price, samples = price_history.price_history_avg(pid, days=AVG_PRICE_WINDOW_DAYS)
        if avg_price is None or avg_price <= 0 or samples < ALERT_MIN_HISTORY_SAMPLES:
            continue

        rise_percent = (price - avg_price) / avg_price * 100
        if rise_percent < RISE_ALERT_THRESHOLD_PERCENT:
            continue

        found.append({
            "pid": pid,
            "price": price,
            "view": price_history.item_view(pid, item, old_snapshot),
            "avg_price": avg_price,
            "rise_percent": rise_percent,
        })

    found.sort(key=lambda a: -a["rise_percent"])  # сильнее всего подорожавшие — первыми
    return found


def send_rise_alert(alert):
    """Шлёт одно уведомление о сильном росте цены — зеркально send_drop_alert."""
    view = alert["view"]
    avg_price = alert["avg_price"]
    best_price = view["best_price"]
    buy_url = view["buy_url"]

    community_values = view.get("community_values") or []
    if community_values:
        value_text = " · ".join(f"{cv['label']}: {cv['value_raw']}" for cv in community_values)
    else:
        value_text = "нет данных"

    text = (
        f"📈 <b>{view['name']}</b> ({view['rare']}) сильно подорожало!\n\n"
        f"Средняя цена (за {AVG_PRICE_WINDOW_DAYS} дн.): {avg_price:.2f}₽\n"
        f"Сейчас в текущем каталоге: <b>{alert['price']:.2f}₽</b> ({alert['rise_percent']:+.1f}%)\n"
        f"Community value: {value_text}"
    )
    keyboard = {"inline_keyboard": [[{"text": f"🛒 Купить за {best_price:.2f}₽", "url": buy_url}]]}
    _send_telegram_alert(text, keyboard, view.get("image"))


# На сколько % best_price должен быть НИЖЕ Community Value (mm2values.com),
# чтобы это считалось выгодной сделкой, а не обычным разбросом цен лотов.
UNDERVALUE_ALERT_THRESHOLD_PERCENT = float(os.environ.get("UNDERVALUE_ALERT_THRESHOLD_PERCENT", "30"))


def build_undervalue_alerts(new_snapshot, old_snapshot, alerted_state):
    """Ищет предметы Godly/Ancient, которые прямо сейчас продаются минимум на
    UNDERVALUE_ALERT_THRESHOLD_PERCENT % дешевле своей Community Value
    (mm2values.com) — то есть потенциально выгодную сделку, а не падение
    относительно собственной истории (см. build_drop_alerts — это отдельное,
    независимое условие). Тот же дедуп-приём: не повторяем алерт, пока
    best_price не изменится."""
    found = []
    for pid, item in new_snapshot.items():
        if (item.get("rare") or "").lower() not in ALERT_RARITIES:
            continue

        view = price_history.item_view(pid, item, old_snapshot)
        best_price = view["best_price"]
        if best_price is None:
            continue

        cv = next((c for c in view["community_values"] if c["source"] == "mm2values"), None)
        if not cv or cv.get("value") is None or cv["value"] <= 0:
            continue
        value = cv["value"]

        if alerted_state.get(pid) == best_price:
            continue

        discount_percent = (best_price - value) / value * 100
        if discount_percent > -UNDERVALUE_ALERT_THRESHOLD_PERCENT:
            continue

        found.append({"pid": pid, "view": view, "value": value, "discount_percent": discount_percent})

    found.sort(key=lambda a: a["discount_percent"])  # сильнее всего недооценённые — первыми
    return found


def send_undervalue_alert(alert):
    """Шлёт одно уведомление о предмете, который продаётся заметно дешевле
    своей Community Value."""
    view = alert["view"]
    value = alert["value"]
    best_price = view["best_price"]
    is_legacy = view["cheaper_source"] == "legacy"
    buy_url = view["legacy_buy_url"] if is_legacy else view["buy_url"]

    text = (
        f"💎 <b>{view['name']}</b> ({view['rare']}) продаётся дешевле своей Community Value!\n\n"
        f"Цена сейчас: <b>{best_price:.2f}₽</b>{' (Legacy-каталог)' if is_legacy else ''}\n"
        f"Community Value: {value:,.0f}\n"
        f"Дешевле на {abs(alert['discount_percent']):.0f}%"
    )
    keyboard = {"inline_keyboard": [[{"text": f"🛒 Купить за {best_price:.2f}₽", "url": buy_url}]]}
    _send_telegram_alert(text, keyboard, view.get("image"))
