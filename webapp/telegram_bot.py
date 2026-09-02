#!/usr/bin/env python3
"""
Telegram-бот: команда /start (и /app, /menu) присылает кнопку "Открыть
каталог", которая открывает мини-приложение (Telegram Web App), плюс
постоянная Menu Button с тем же эффектом. Если TELEGRAM_BOT_TOKEN не задан —
ptb_app остаётся None и start()/stop() ничего не делают, работает только
HTTP API (см. server.py).
"""

import logging
import os

log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").rstrip("/")

ptb_app = None

if BOT_TOKEN:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Update, WebAppInfo
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


async def start():
    """Вызывается из FastAPI on_startup. Ничего не делает, если бот не настроен."""
    if ptb_app is None:
        if not WEBAPP_URL:
            log.warning("WEBAPP_URL не задан — кнопка 'Открыть каталог' работать не будет.")
        return

    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.updater.start_polling(allowed_updates=["message"])
    log.info("Telegram-бот запущен (long polling).")

    if WEBAPP_URL:
        try:
            await ptb_app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🛒 Каталог", web_app=WebAppInfo(url=WEBAPP_URL))
            )
            log.info("Постоянная кнопка меню (Menu Button) установлена — /start больше не нужен.")
        except Exception:
            log.exception("Не удалось установить кнопку меню")
    else:
        log.warning("WEBAPP_URL не задан — кнопка 'Открыть каталог' работать не будет.")


async def stop():
    """Вызывается из FastAPI on_shutdown. Ничего не делает, если бот не настроен."""
    if ptb_app is None:
        return
    await ptb_app.updater.stop()
    await ptb_app.stop()
    await ptb_app.shutdown()
