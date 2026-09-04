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
    from telegram import (
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        LabeledPrice,
        MenuButtonWebApp,
        Update,
        WebAppInfo,
    )
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters

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

    async def _precheckout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Донат через Telegram Stars — ничего не проверяем (не товар, не может
        быть 'нет в наличии'), просто подтверждаем в течение отведённых 10 сек,
        иначе Telegram сам отменит платёж."""
        await update.pre_checkout_query.answer(ok=True)

    async def _successful_payment_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        stars = update.message.successful_payment.total_amount
        await update.message.reply_text(f"Спасибо за поддержку — {stars} ⭐! 🎉")

    ptb_app = Application.builder().token(BOT_TOKEN).build()
    ptb_app.add_handler(CommandHandler(["start", "app", "menu"], start_cmd))
    ptb_app.add_handler(PreCheckoutQueryHandler(_precheckout_cmd))
    ptb_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, _successful_payment_cmd))

    async def create_support_invoice_link(stars: int, lang: str = "ru"):
        """Ссылка на разовый донат через Telegram Stars (валюта XTR — цена
        прямо в звёздах, provider_token не нужен). None при ошибке или если
        бот не настроен."""
        if ptb_app is None:
            return None
        title = "Поддержать MM2 Pulse" if lang != "en" else "Support MM2 Pulse"
        description = (
            f"Разовый донат на развитие проекта — {stars} ⭐" if lang != "en"
            else f"A one-time donation to the project — {stars} ⭐"
        )
        try:
            return await ptb_app.bot.create_invoice_link(
                title=title,
                description=description,
                payload=f"support_{stars}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label=title, amount=stars)],
            )
        except Exception:
            log.exception("Не удалось создать ссылку на донат через Stars")
            return None
else:
    log.warning("TELEGRAM_BOT_TOKEN не задан — бот не запустится, будет работать только API.")

    async def create_support_invoice_link(stars: int, lang: str = "ru"):
        return None


async def start():
    """Вызывается из FastAPI on_startup. Ничего не делает, если бот не настроен."""
    if ptb_app is None:
        if not WEBAPP_URL:
            log.warning("WEBAPP_URL не задан — кнопка 'Открыть каталог' работать не будет.")
        return

    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.updater.start_polling(allowed_updates=["message", "pre_checkout_query"])
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
