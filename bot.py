import asyncio
import datetime
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
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID") or os.getenv("LOG_CHANNEL")
LOG_CHANNEL_ID = LOG_CHANNEL_ID.strip() if LOG_CHANNEL_ID else None

rate_limits: dict[str, list[float]] = {}

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("telegram").setLevel(logging.CRITICAL)
logging.getLogger("apscheduler").setLevel(logging.CRITICAL)

ACTION_LABELS = {
    "start": "старт",
    "check": "проверка рейтинга",
    "add": "добавление в мониторинг",
    "remove": "удаление из мониторинга",
    "list": "список мониторинга",
    "monitoring": "регулярная проверка",
}

BOT_DESCRIPTION = (
    "Этот проект представляет собой Telegram-бота, который отслеживает изменения рейтинга "
    "пользователей на сайте hackerlab.pro. Бот может проверять рейтинг по запросу, "
    "добавлять пользователей на регулярный мониторинг, выводить список отслеживаемых "
    "пользователей и удалять их из мониторинга. Мониторинг выполняется каждые 10 минут. "
    "Для одного чата можно поставить на мониторинг не более 10 пользователей, а разовые "
    "проверки ограничены пятью за пять минут."
)

DAILY_STATS = {"checked": 0, "changed": 0, "errors": 0}
DAILY_STATS_LOCK = asyncio.Lock()


def _format_full_name(first_name: str | None, last_name: str | None) -> str:
    parts = [p for p in [first_name, last_name] if p]
    return " ".join(parts).strip()


def _tg_user_link(user=None, chat: Chat | None = None) -> str:
    username = getattr(user, "username", None) or (chat.tg_username if chat else None)
    first_name = getattr(user, "first_name", None) or (chat.first_name if chat else None)
    last_name = getattr(user, "last_name", None) or (chat.last_name if chat else None)
    user_id = getattr(user, "id", None) or (chat.chat_id if chat else None)

    full_name = _format_full_name(first_name, last_name)
    if username:
        display = f"{full_name} (@{username})" if full_name else f"@{username}"
        url = f"https://t.me/{quote(username, safe='')}"
    else:
        display = full_name or f"ID {user_id}" if user_id else "Пользователь"
        url = f"tg://user?id={user_id}" if user_id else None

    display = escape(display)
    if url:
        return f'<a href="{url}">{display}</a>'
    return display


def _hackerlab_link(username: str) -> str:
    safe_username = username.strip()
    url = f"https://hackerlab.pro/users/{quote(safe_username, safe='')}"
    return f'<a href="{url}">{escape(safe_username)}</a>'


async def _send_channel_message(application, text: str, *, silent: bool = False) -> None:
    if not LOG_CHANNEL_ID:
        return
    try:
        await application.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            disable_notification=silent,
        )
    except Exception as exc:
        logger.error("channel_log_send_failed: chat_id=%s error=%s", LOG_CHANNEL_ID, exc)


async def _log_error(application, user, chat: Chat | None, action: str, detail: str) -> None:
    user_link = _tg_user_link(user, chat)
    action_label = ACTION_LABELS.get(action, action)
    text = f"Ошибка: {user_link} — {detail} (действие: {escape(action_label)})"
    await _send_channel_message(application, text)


async def _log_action(application, user, chat: Chat | None, action: str, detail: str | None = None) -> None:
    user_link = _tg_user_link(user, chat)
    action_label = ACTION_LABELS.get(action, action)
    if detail:
        text = f"Действие: {user_link} — {escape(action_label)}: {detail}"
    else:
        text = f"Действие: {user_link} — {escape(action_label)}"
    await _send_channel_message(application, text)


async def _record_daily_stats(checked: int, changed: int, errors: int) -> None:
    async with DAILY_STATS_LOCK:
        DAILY_STATS["checked"] += checked
        DAILY_STATS["changed"] += changed
        DAILY_STATS["errors"] += errors


async def _send_daily_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    async with DAILY_STATS_LOCK:
        stats = dict(DAILY_STATS)
        DAILY_STATS["checked"] = 0
        DAILY_STATS["changed"] = 0
        DAILY_STATS["errors"] = 0
    await _send_channel_message(
        context.application,
        f"Сводка за сутки: проверено {stats['checked']}, "
        f"обновлено {stats['changed']}, ошибок {stats['errors']}",
        silent=True,
    )


async def _post_init(application) -> None:
    if not LOG_CHANNEL_ID:
        return
    await _send_channel_message(application, "Логи: бот запущен")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = str(update.effective_chat.id) if update.effective_chat else "unknown"
    user = update.effective_user
    tg_username = user.username if user else None
    first_name = user.first_name if user else None
    last_name = user.last_name if user else None
    greeting_name = f"@{tg_username}" if tg_username else _format_full_name(first_name, last_name) or "друг"
    session = SessionLocal()
    try:
        chat = session.query(Chat).filter_by(chat_id=chat_id).first()
        if not chat:
            chat = Chat(chat_id=chat_id, tg_username=tg_username, first_name=first_name, last_name=last_name)
            session.add(chat)
        else:
            if tg_username:
                chat.tg_username = tg_username
            if first_name:
                chat.first_name = first_name
            if last_name:
                chat.last_name = last_name
        session.commit()
    except Exception:
        await _log_error(
            context.application,
            user,
            None,
            "start",
            "не удалось сохранить данные пользователя",
        )
    finally:
        session.close()
    await _log_action(context.application, user, None, "start")
    keyboard = [
        ["Проверка рейтинга", "Пользователи на мониторинге"],
        ["Добавить на мониторинг", "Удалить с мониторинга"],
    ]
    await update.message.reply_text(
        f"Привет, {greeting_name}!\n\n{BOT_DESCRIPTION}\n\nBy s3cs3k3r.ru\n\nВыберите действие",
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
        chat = None
        link_count = 0
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
                    link_count = len(links)
                    await update.message.reply_text(
                        "На мониторинге:\n" + "\n".join(links),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
        finally:
            session.close()
        await _log_action(
            context.application,
            update.effective_user,
            chat,
            "list",
            f"пользователей: {link_count}",
        )
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
    user = update.effective_user
    tg_username = user.username if user else None
    first_name = user.first_name if user else None
    last_name = user.last_name if user else None
    action = context.user_data.get("action")
    session = SessionLocal()
    try:
        chat = session.query(Chat).filter_by(chat_id=chat_id).first()
        if not chat:
            chat = Chat(chat_id=chat_id, tg_username=tg_username, first_name=first_name, last_name=last_name)
            session.add(chat)
        else:
            if tg_username:
                chat.tg_username = tg_username
            if first_name:
                chat.first_name = first_name
            if last_name:
                chat.last_name = last_name
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
                await _log_error(
                    context.application,
                    user,
                    chat,
                    "check",
                    f"не удалось получить рейтинг для {_hackerlab_link(username)}",
                )
                await update.message.reply_text("Не удалось получить рейтинг")
            else:
                await update.message.reply_text(
                    f"Текущий рейтинг пользователя {_hackerlab_link(username)}: {rating}",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                await _log_action(
                    context.application,
                    user,
                    chat,
                    "check",
                    f"{_hackerlab_link(username)} = {rating}",
                )
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
            if rating is None:
                await _log_error(
                    context.application,
                    user,
                    chat,
                    "add",
                    f"не удалось получить рейтинг для {_hackerlab_link(username)}",
                )
            mu = MonitoredUser(chat_id=chat.id, username=username, last_rating=rating if rating is not None else None)
            session.add(mu)
            session.commit()
            await update.message.reply_text("Пользователь добавлен на мониторинг")
            await _log_action(
                context.application,
                user,
                chat,
                "add",
                _hackerlab_link(username),
            )
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
            await _log_action(
                context.application,
                user,
                chat,
                "remove",
                _hackerlab_link(username),
            )
            return CHOOSING_ACTION
    finally:
        session.close()
    return CHOOSING_ACTION


async def check_all_ratings(context: ContextTypes.DEFAULT_TYPE) -> None:
    application = context.application
    session = SessionLocal()
    checked = 0
    changed = 0
    errors = 0
    try:
        users = session.query(MonitoredUser).all()
        for user in users:
            checked += 1
            try:
                new_rating = await get_rating(user.username)
            except Exception:
                errors += 1
                await _log_error(
                    application,
                    None,
                    user.chat,
                    "monitoring",
                    f"ошибка получения рейтинга для {_hackerlab_link(user.username)}",
                )
                continue
            if new_rating is None:
                errors += 1
                await _log_error(
                    application,
                    None,
                    user.chat,
                    "monitoring",
                    f"не удалось получить рейтинг для {_hackerlab_link(user.username)}",
                )
                continue
            if user.last_rating is None or new_rating != user.last_rating:
                old_rating = user.last_rating
                user_link = _hackerlab_link(user.username)
                user.last_rating = new_rating
                session.commit()
                changed += 1
                try:
                    if old_rating is None:
                        message = f"Рейтинг пользователя {user_link}: {new_rating}"
                    else:
                        message = f"Рейтинг пользователя {user_link} изменился: {old_rating} -> {new_rating}"
                    await application.bot.send_message(
                        chat_id=user.chat.chat_id,
                        text=message,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                except Exception:
                    errors += 1
                    await _log_error(
                        application,
                        None,
                        user.chat,
                        "monitoring",
                        f"не удалось отправить уведомление для {_hackerlab_link(user.username)}",
                    )
    finally:
        session.close()
    await _record_daily_stats(checked, changed, errors)


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    application = ApplicationBuilder().token(token).post_init(_post_init).build()
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
    application.job_queue.run_daily(_send_daily_summary, time=datetime.time(hour=13, minute=0))
    application.run_polling()


if __name__ == "__main__":
    main()
