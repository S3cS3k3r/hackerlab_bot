import asyncio
import logging
import os
import time
from html import escape
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (ApplicationBuilder, CommandHandler, ConversationHandler,
                          ContextTypes, MessageHandler, filters)

from db import Chat, MonitoredUser, init_db
from rating_scraper import get_rating

load_dotenv()

SessionLocal = init_db()

CHOOSING_ACTION, AWAITING_USERNAME = range(2)
MENU_CHOICE_REGEX = r"^(Проверка рейтинга|Пользователи на мониторинге|Добавить на мониторинг|Удалить с мониторинга)$"

rate_limits: dict[str, list[float]] = {}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = str(update.effective_chat.id) if update.effective_chat else "unknown"
    user_id = str(update.effective_user.id) if update.effective_user else "unknown"
    logger.info("start: chat_id=%s user_id=%s", chat_id, user_id)
    keyboard = [
        ["Проверка рейтинга", "Пользователи на мониторинге"],
        ["Добавить на мониторинг", "Удалить с мониторинга"],
    ]
    await update.message.reply_text(
        "Выберите действие",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
    )
    return CHOOSING_ACTION


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    chat_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id) if update.effective_user else "unknown"
    logger.info("choice: chat_id=%s user_id=%s text=%s", chat_id, user_id, text)
    if text == "Проверка рейтинга":
        context.user_data["action"] = "check"
        await update.message.reply_text("Введите ник пользователя")
        return AWAITING_USERNAME
    if text == "Пользователи на мониторинге":
        session = SessionLocal()
        try:
            chat = session.query(Chat).filter_by(chat_id=chat_id).first()
            if not chat or not chat.users:
                await update.message.reply_text("Список пуст")
            else:
                links = []
                for user in chat.users:
                    username = (user.username or "").strip()
                    if not username:
                        continue
                    url = f"https://hackerlab.pro/users/{quote(username, safe='')}"
                    links.append(f'<a href="{url}">{escape(username)}</a>')
                if not links:
                    await update.message.reply_text("Список пуст")
                else:
                    await update.message.reply_text(
                        "На мониторинге:\n" + "\n".join(links),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
        finally:
            session.close()
        return CHOOSING_ACTION
    if text == "Добавить на мониторинг":
        context.user_data["action"] = "add"
        await update.message.reply_text("Введите ник пользователя")
        return AWAITING_USERNAME
    if text == "Удалить с мониторинга":
        context.user_data["action"] = "remove"
        session = SessionLocal()
        try:
            chat = session.query(Chat).filter_by(chat_id=chat_id).first()
            if not chat or not chat.users:
                await update.message.reply_text("Список пуст")
                return CHOOSING_ACTION
            lst = [u.username for u in chat.users]
            await update.message.reply_text("Введите ник пользователя для удаления:\n" + "\n".join(lst))
        finally:
            session.close()
        return AWAITING_USERNAME
    await update.message.reply_text("Неизвестная команда")
    return CHOOSING_ACTION


async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = update.message.text.strip()
    chat_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id) if update.effective_user else "unknown"
    action = context.user_data.get("action")
    logger.info(
        "username: chat_id=%s user_id=%s action=%s username=%s",
        chat_id,
        user_id,
        action,
        username,
    )
    session = SessionLocal()
    try:
        chat = session.query(Chat).filter_by(chat_id=chat_id).first()
        if not chat:
            chat = Chat(chat_id=chat_id)
            session.add(chat)
            session.commit()
        if action == "check":
            now = time.time()
            timestamps = rate_limits.get(chat_id, [])
            timestamps = [t for t in timestamps if now - t < 300]
            if len(timestamps) >= 5:
                rate_limits[chat_id] = timestamps
                logger.warning("rate_limit: chat_id=%s user_id=%s", chat_id, user_id)
                await update.message.reply_text("Превышен лимит запросов")
                return CHOOSING_ACTION
            timestamps.append(now)
            rate_limits[chat_id] = timestamps
            rating = await get_rating(username)
            if rating is None:
                logger.warning(
                    "rating_fetch_failed: chat_id=%s user_id=%s username=%s",
                    chat_id,
                    user_id,
                    username,
                )
                await update.message.reply_text("Не удалось получить рейтинг")
            else:
                logger.info(
                    "rating_checked: chat_id=%s user_id=%s username=%s rating=%s",
                    chat_id,
                    user_id,
                    username,
                    rating,
                )
                await update.message.reply_text(f"Текущий рейтинг пользователя {username}: {rating}")
            return CHOOSING_ACTION
        if action == "add":
            current_count = len(chat.users)
            if current_count >= 10:
                logger.warning(
                    "monitor_limit: chat_id=%s user_id=%s username=%s",
                    chat_id,
                    user_id,
                    username,
                )
                await update.message.reply_text("Достигнут лимит пользователей на мониторинге")
                return CHOOSING_ACTION
            existing = (
                session.query(MonitoredUser)
                .filter_by(chat_id=chat.id, username=username)
                .first()
            )
            if existing:
                logger.info(
                    "monitor_exists: chat_id=%s user_id=%s username=%s",
                    chat_id,
                    user_id,
                    username,
                )
                await update.message.reply_text("Пользователь уже на мониторинге")
                return CHOOSING_ACTION
            rating = await get_rating(username)
            mu = MonitoredUser(chat_id=chat.id, username=username, last_rating=rating if rating is not None else None)
            session.add(mu)
            session.commit()
            logger.info(
                "monitor_added: chat_id=%s user_id=%s username=%s",
                chat_id,
                user_id,
                username,
            )
            await update.message.reply_text("Пользователь добавлен на мониторинг")
            return CHOOSING_ACTION
        if action == "remove":
            mu = (
                session.query(MonitoredUser)
                .filter_by(chat_id=chat.id, username=username)
                .first()
            )
            if not mu:
                logger.info(
                    "monitor_missing: chat_id=%s user_id=%s username=%s",
                    chat_id,
                    user_id,
                    username,
                )
                await update.message.reply_text("Такой пользователь не найден")
                return CHOOSING_ACTION
            session.delete(mu)
            session.commit()
            logger.info(
                "monitor_removed: chat_id=%s user_id=%s username=%s",
                chat_id,
                user_id,
                username,
            )
            await update.message.reply_text("Пользователь удален из мониторинга")
            return CHOOSING_ACTION
    finally:
        session.close()
    return CHOOSING_ACTION


async def check_all_ratings(context: ContextTypes.DEFAULT_TYPE) -> None:
    application = context.application
    session = SessionLocal()
    try:
        users = session.query(MonitoredUser).all()
        for user in users:
            new_rating = await get_rating(user.username)
            if new_rating is None:
                continue
            if user.last_rating is None or new_rating != user.last_rating:
                user.last_rating = new_rating
                session.commit()
                await application.bot.send_message(
                    chat_id=user.chat.chat_id,
                    text=f"Рейтинг пользователя {user.username} изменился: {new_rating}",
                )
    finally:
        session.close()


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    application = ApplicationBuilder().token(token).build()
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(MENU_CHOICE_REGEX), handle_choice),
        ],
        states={
            CHOOSING_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_choice)],
            AWAITING_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    application.add_handler(conv_handler)
    application.job_queue.run_repeating(check_all_ratings, interval=600, first=600)
    application.run_polling()


if __name__ == "__main__":
    main()
