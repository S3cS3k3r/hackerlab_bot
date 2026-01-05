import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (ApplicationBuilder, CommandHandler, ConversationHandler,
                          ContextTypes, MessageHandler, filters)

from db import Chat, MonitoredUser, init_db
from rating_scraper import get_rating

load_dotenv()

SessionLocal = init_db()

CHOOSING_ACTION, AWAITING_USERNAME = range(2)

rate_limits: dict[str, list[float]] = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
                lst = [u.username for u in chat.users]
                await update.message.reply_text("На мониторинге:\n" + "\n".join(lst))
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
    action = context.user_data.get("action")
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
                await update.message.reply_text("Превышен лимит запросов")
                return CHOOSING_ACTION
            timestamps.append(now)
            rate_limits[chat_id] = timestamps
            rating = await get_rating(username)
            if rating is None:
                await update.message.reply_text("Не удалось получить рейтинг")
            else:
                await update.message.reply_text(f"Текущий рейтинг пользователя {username}: {rating}")
            return CHOOSING_ACTION
        if action == "add":
            current_count = len(chat.users)
            if current_count >= 10:
                await update.message.reply_text("Достигнут лимит пользователей на мониторинге")
                return CHOOSING_ACTION
            existing = (
                session.query(MonitoredUser)
                .filter_by(chat_id=chat.id, username=username)
                .first()
            )
            if existing:
                await update.message.reply_text("Пользователь уже на мониторинге")
                return CHOOSING_ACTION
            rating = await get_rating(username)
            mu = MonitoredUser(chat_id=chat.id, username=username, last_rating=rating if rating is not None else None)
            session.add(mu)
            session.commit()
            await update.message.reply_text("Пользователь добавлен на мониторинг")
            return CHOOSING_ACTION
        if action == "remove":
            mu = (
                session.query(MonitoredUser)
                .filter_by(chat_id=chat.id, username=username)
                .first()
            )
            if not mu:
                await update.message.reply_text("Такой пользователь не найден")
                return CHOOSING_ACTION
            session.delete(mu)
            session.commit()
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
        entry_points=[CommandHandler("start", start)],
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
