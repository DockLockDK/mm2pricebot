#!/usr/bin/env python3
"""
Алерты о сильном падении цены (push в Telegram) — только Godly/Ancient
(по просьбе пользователя Unique исключён из уведомлений, хотя в
мини-приложении остаётся как обычно). Порог и окно среднего берутся из
переменных окружения DROP_ALERT_THRESHOLD_PERCENT/AVG_PRICE_WINDOW_DAYS.
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


def load_alerted_state():
    """{pid: цена, по которой мы последний раз алертили этот предмет}."""
    if not os.path.exists(ALERTED_STATE_FILE):
        return {}
    try:
        with open(ALERTED_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_alerted_state(state):
    try:
        with open(ALERTED_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("Не удалось сохранить alerted_drops.json")


def build_drop_alerts(new_snapshot, old_snapshot, alerted_state):
    """Ищет предметы Godly/Ancient, у которых текущая цена упала минимум на
    DROP_ALERT_THRESHOLD_PERCENT % ниже средней за AVG_PRICE_WINDOW_DAYS дней.
    Пропускает предмет, если мы уже слали алерт именно по этой цене (иначе
    бот шлёт одно и то же уведомление на каждом цикле, пока цена стоит на
    месте) — алерт повторится только когда цена изменится (упадёт ещё ниже
    или сначала отрастёт обратно и упадёт заново)."""
    found = []
    for pid, item in new_snapshot.items():
        if (item.get("rare") or "").lower() not in ALERT_RARITIES:
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


def send_drop_alert(alert):
    """Шлёт одно уведомление о сильном падении цены с кнопкой-ссылкой на лот."""
    if not tracker.TELEGRAM_BOT_TOKEN or not tracker.TELEGRAM_CHAT_ID:
        return

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
    image_url = view.get("image")

    # С картинкой предмета (sendPhoto, подпись = тот же текст) — если картинки
    # нет или Telegram не смог её загрузить (CDN недоступен/нет фото у
    # предмета), откатываемся на обычное текстовое sendMessage, чтобы алерт
    # в любом случае дошёл.
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
