import asyncio
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


DATE_INPUT_RE = re.compile(
    r"^\s*(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4})\s+(\d{1,2}:\d{2})\s+(.+?)\s*$"
)
REMINDER_OFFSETS = (
    ("1 день", timedelta(days=1)),
    ("3 часа", timedelta(hours=3)),
    ("1 час", timedelta(hours=1)),
    ("момент события", timedelta()),
)
CLEANUP_DELAY_SECONDS = 60
LIST_CLEANUP_DELAY_SECONDS = 120


@dataclass(slots=True)
class Event:
    id: int
    chat_id: int
    message_thread_id: int | None
    created_by_user_id: int
    created_by_name: str
    title: str
    event_at_utc: datetime


class EventStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_thread_id INTEGER,
                    created_by_user_id INTEGER NOT NULL,
                    created_by_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    event_at_utc TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(events)").fetchall()
            }
            if "message_thread_id" not in columns:
                connection.execute(
                    "ALTER TABLE events ADD COLUMN message_thread_id INTEGER"
                )

    def add_event(
        self,
        chat_id: int,
        message_thread_id: int | None,
        created_by_user_id: int,
        created_by_name: str,
        title: str,
        event_at_utc: datetime,
    ) -> Event:
        event_iso = event_at_utc.astimezone(timezone.utc).isoformat()
        created_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events (
                    chat_id,
                    message_thread_id,
                    created_by_user_id,
                    created_by_name,
                    title,
                    event_at_utc,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    message_thread_id,
                    created_by_user_id,
                    created_by_name,
                    title,
                    event_iso,
                    created_iso,
                ),
            )
            event_id = cursor.lastrowid

        return Event(
            id=event_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            created_by_user_id=created_by_user_id,
            created_by_name=created_by_name,
            title=title,
            event_at_utc=datetime.fromisoformat(event_iso),
        )

    def get_upcoming_events(self) -> list[Event]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, chat_id, message_thread_id, created_by_user_id, created_by_name, title, event_at_utc
                FROM events
                WHERE event_at_utc > ?
                ORDER BY event_at_utc ASC
                """,
                (now_iso,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def get_upcoming_events_for_chat(
        self, chat_id: int, message_thread_id: int | None
    ) -> list[Event]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            if message_thread_id is None:
                rows = connection.execute(
                    """
                    SELECT id, chat_id, message_thread_id, created_by_user_id, created_by_name, title, event_at_utc
                    FROM events
                    WHERE chat_id = ? AND message_thread_id IS NULL AND event_at_utc > ?
                    ORDER BY event_at_utc ASC
                    """,
                    (chat_id, now_iso),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, chat_id, message_thread_id, created_by_user_id, created_by_name, title, event_at_utc
                    FROM events
                    WHERE chat_id = ? AND message_thread_id = ? AND event_at_utc > ?
                    ORDER BY event_at_utc ASC
                    """,
                    (chat_id, message_thread_id, now_iso),
                ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def delete_event(
        self, event_id: int, chat_id: int, message_thread_id: int | None
    ) -> bool:
        with self._connect() as connection:
            if message_thread_id is None:
                cursor = connection.execute(
                    "DELETE FROM events WHERE id = ? AND chat_id = ? AND message_thread_id IS NULL",
                    (event_id, chat_id),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM events WHERE id = ? AND chat_id = ? AND message_thread_id = ?",
                    (event_id, chat_id, message_thread_id),
                )
            return cursor.rowcount > 0

    def delete_all_events_for_chat(
        self, chat_id: int, message_thread_id: int | None
    ) -> int:
        with self._connect() as connection:
            if message_thread_id is None:
                cursor = connection.execute(
                    "DELETE FROM events WHERE chat_id = ? AND message_thread_id IS NULL",
                    (chat_id,),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM events WHERE chat_id = ? AND message_thread_id = ?",
                    (chat_id, message_thread_id),
                )
            return cursor.rowcount

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            chat_id=row["chat_id"],
            message_thread_id=row["message_thread_id"],
            created_by_user_id=row["created_by_user_id"],
            created_by_name=row["created_by_name"],
            title=row["title"],
            event_at_utc=datetime.fromisoformat(row["event_at_utc"]),
        )


class ReminderBot:
    def __init__(self, token: str, db_path: Path, local_tz_name: str) -> None:
        self.local_tz = ZoneInfo(local_tz_name)
        self.storage = EventStorage(db_path)
        self.application: Application = ApplicationBuilder().token(token).build()
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("list", self.list_events))
        self.application.add_handler(CommandHandler("delete", self.delete_event))
        self.application.add_handler(CommandHandler("delete_all", self.delete_all_events))
        self.application.add_handler(CommandHandler("ping", self.ping))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message)
        )
        self.application.add_error_handler(self.handle_error)
        self.application.post_init = self.on_startup

    async def on_startup(self, application: Application) -> None:
        for event in self.storage.get_upcoming_events():
            self.schedule_event_reminders(event)
        logging.info("Loaded %s upcoming events", len(self.storage.get_upcoming_events()))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(
            update,
            self._help_text(),
            parse_mode=ParseMode.HTML,
            cleanup_delay_seconds=LIST_CLEANUP_DELAY_SECONDS,
        )

    async def help_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._reply(
            update,
            self._help_text(),
            parse_mode=ParseMode.HTML,
            cleanup_delay_seconds=LIST_CLEANUP_DELAY_SECONDS,
        )

    async def ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(update, "pong", cleanup_delay_seconds=CLEANUP_DELAY_SECONDS)

    async def list_events(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        thread_id = update.effective_message.message_thread_id if update.effective_message else None
        events = self.storage.get_upcoming_events_for_chat(chat.id, thread_id)
        if not events:
            await self._reply(
                update,
                "Активных событий пока нет.",
                cleanup_delay_seconds=CLEANUP_DELAY_SECONDS,
            )
            return

        now_local = datetime.now(self.local_tz)
        lines = ["Ближайшие события и напоминания:"]
        for event in events[:20]:
            local_dt = event.event_at_utc.astimezone(self.local_tz)
            lines.append(f"#{event.id} • {local_dt.strftime('%d.%m.%Y %H:%M')} • {event.title}")
            for label, delta in REMINDER_OFFSETS:
                remind_at = local_dt - delta
                if remind_at <= now_local:
                    continue
                if label == "момент события":
                    lines.append(f"  - в момент события: {local_dt.strftime('%d.%m.%Y %H:%M')}")
                else:
                    lines.append(f"  - за {label}: {remind_at.strftime('%d.%m.%Y %H:%M')}")
        await self._reply(
            update,
            "\n".join(lines),
            cleanup_delay_seconds=LIST_CLEANUP_DELAY_SECONDS,
        )

    async def delete_event(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        if not context.args:
            await self._reply(
                update,
                "Использование: /delete <id>",
                cleanup_delay_seconds=CLEANUP_DELAY_SECONDS,
            )
            return

        try:
            event_id = int(context.args[0])
        except ValueError:
            await self._reply(
                update,
                "ID должен быть числом. Пример: /delete 3",
                cleanup_delay_seconds=CLEANUP_DELAY_SECONDS,
            )
            return

        thread_id = update.effective_message.message_thread_id if update.effective_message else None
        deleted = self.storage.delete_event(event_id, chat.id, thread_id)
        if not deleted:
            await self._reply(
                update,
                "Событие не найдено.",
                cleanup_delay_seconds=CLEANUP_DELAY_SECONDS,
            )
            return

        self._remove_jobs_for_event(event_id)
        await self._reply(
            update,
            f"Событие #{event_id} удалено.",
            cleanup_delay_seconds=CLEANUP_DELAY_SECONDS,
        )

    async def delete_all_events(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        thread_id = update.effective_message.message_thread_id if update.effective_message else None
        deleted_count = self.storage.delete_all_events_for_chat(chat.id, thread_id)
        self._remove_jobs_for_chat(chat.id, thread_id)

        if deleted_count == 0:
            await self._reply(
                update,
                "Активных событий для удаления нет.",
                cleanup_delay_seconds=CLEANUP_DELAY_SECONDS,
            )
            return

        await self._reply(
            update,
            f"Удалены все события и напоминания в этом чате: {deleted_count} шт.",
            cleanup_delay_seconds=CLEANUP_DELAY_SECONDS,
        )

    async def handle_text_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not message or not message.text or not chat or not user:
            return

        logging.info(
            "Incoming text | chat_id=%s | user_id=%s | text=%r",
            chat.id,
            user.id,
            message.text,
        )

        parsed = self._parse_event_message(message.text)
        if not parsed:
            await self._reply(
                update,
                "Не понял формат. Отправь сообщение так:\n"
                "<code>16.03.2026 18:30 Встреча с командой</code>",
                parse_mode=ParseMode.HTML,
                cleanup_delay_seconds=CLEANUP_DELAY_SECONDS,
            )
            return

        local_dt, title = parsed
        now_local = datetime.now(self.local_tz)
        if local_dt <= now_local:
            await self._reply(
                update,
                "Дата события уже в прошлом. Укажи будущее время.",
                cleanup_delay_seconds=CLEANUP_DELAY_SECONDS,
            )
            return

        event = self.storage.add_event(
            chat_id=chat.id,
            message_thread_id=message.message_thread_id,
            created_by_user_id=user.id,
            created_by_name=user.full_name,
            title=title,
            event_at_utc=local_dt.astimezone(timezone.utc),
        )
        self.schedule_event_reminders(event)

        reminders = []
        for label, delta in REMINDER_OFFSETS:
            if local_dt - delta > now_local:
                reminders.append(label)
            elif delta == timedelta():
                reminders.append(label)

        reminder_text = ", ".join(reminders) if reminders else "нет доступных напоминаний"
        await self._reply(
            update,
            "Событие сохранено, напоминания установлены.\n"
            f"ID: #{event.id}\n"
            f"Когда: {local_dt.strftime('%d.%m.%Y %H:%M')} ({self.local_tz.key})\n"
            f"Что: {title}\n"
            f"Напоминания: {reminder_text}",
            cleanup_delay_seconds=CLEANUP_DELAY_SECONDS,
        )

    def _parse_event_message(self, text: str) -> tuple[datetime, str] | None:
        match = DATE_INPUT_RE.match(text)
        if not match:
            return None

        date_part, time_part, title = match.groups()
        normalized_date = re.sub(r"[-/]", ".", date_part)
        try:
            naive_dt = datetime.strptime(
                f"{normalized_date} {time_part}", "%d.%m.%Y %H:%M"
            )
        except ValueError:
            return None
        return naive_dt.replace(tzinfo=self.local_tz), title.strip()

    def schedule_event_reminders(self, event: Event) -> None:
        for label, delta in REMINDER_OFFSETS:
            remind_at = event.event_at_utc - delta
            if remind_at <= datetime.now(timezone.utc):
                continue

            job_name = self._job_name(event.id, label)
            self.application.job_queue.run_once(
                self.send_reminder,
                when=remind_at,
                name=job_name,
                data={
                    "event_id": event.id,
                    "chat_id": event.chat_id,
                    "message_thread_id": event.message_thread_id,
                    "title": event.title,
                    "event_at_utc": event.event_at_utc.isoformat(),
                    "created_by_name": event.created_by_name,
                    "offset_label": label,
                },
            )

    async def send_reminder(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        job_data = context.job.data
        event_at = datetime.fromisoformat(job_data["event_at_utc"]).astimezone(self.local_tz)
        await context.bot.send_message(
            chat_id=job_data["chat_id"],
            message_thread_id=job_data["message_thread_id"],
            text=(
                (
                    f"Событие начинается сейчас\n"
                    if job_data["offset_label"] == "момент события"
                    else f"Напоминание: через {job_data['offset_label']} событие\n"
                )
                + (
                    f"«{job_data['title']}»\n"
                    f"Когда: {event_at.strftime('%d.%m.%Y %H:%M')} ({self.local_tz.key})\n"
                    f"Создал: {job_data['created_by_name']}"
                )
            ),
        )

    async def handle_error(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        logging.exception("Unhandled error while processing update", exc_info=context.error)

    async def _reply(
        self,
        update: Update,
        text: str,
        cleanup_delay_seconds: int | None = None,
        **kwargs: object,
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if not chat:
            return

        if message and message.message_thread_id is not None:
            kwargs["message_thread_id"] = message.message_thread_id

        sent_message = await chat.send_message(text, **kwargs)

        if cleanup_delay_seconds and chat.type != "private":
            self._schedule_message_cleanup(
                chat_id=chat.id,
                bot_message_id=sent_message.message_id,
                user_message_id=message.message_id if message else None,
                delay_seconds=cleanup_delay_seconds,
            )

    def _schedule_message_cleanup(
        self,
        chat_id: int,
        bot_message_id: int,
        user_message_id: int | None,
        delay_seconds: int,
    ) -> None:
        self.application.job_queue.run_once(
            self.delete_messages,
            when=delay_seconds,
            data={
                "chat_id": chat_id,
                "bot_message_id": bot_message_id,
                "user_message_id": user_message_id,
            },
        )

    async def delete_messages(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        job_data = context.job.data
        for message_id in (
            job_data.get("user_message_id"),
            job_data.get("bot_message_id"),
        ):
            if not message_id:
                continue
            try:
                await context.bot.delete_message(
                    chat_id=job_data["chat_id"],
                    message_id=message_id,
                )
            except Exception:
                logging.debug(
                    "Failed to delete message %s in chat %s",
                    message_id,
                    job_data["chat_id"],
                )

    def _remove_jobs_for_event(self, event_id: int) -> None:
        for job in self.application.job_queue.jobs():
            if job.name.startswith(f"event:{event_id}:"):
                job.schedule_removal()

    def _remove_jobs_for_chat(
        self, chat_id: int, message_thread_id: int | None
    ) -> None:
        for job in self.application.job_queue.jobs():
            job_data = job.data or {}
            if (
                job_data.get("chat_id") == chat_id
                and job_data.get("message_thread_id") == message_thread_id
            ):
                job.schedule_removal()

    @staticmethod
    def _job_name(event_id: int, label: str) -> str:
        safe_label = label.replace(" ", "_")
        return f"event:{event_id}:{safe_label}"

    def _help_text(self) -> str:
        return (
            "Я сохраняю события и напоминаю о них за <b>1 день</b>, <b>3 часа</b> и <b>1 час</b>.\n\n"
            "Формат сообщения:\n"
            "<code>16.03.2026 18:30 Встреча с командой</code>\n\n"
            "Команды:\n"
            "/list - показать события и все будущие напоминания\n"
            "/delete ID - удалить событие\n"
            "/delete_all - удалить все события и напоминания в чате\n"
            "/help - показать подсказку\n\n"
            f"Часовой пояс бота: <b>{self.local_tz.key}</b>"
        )

    def run(self) -> None:
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable")

    db_path = Path(os.getenv("BOT_DB_PATH", "events.db"))
    timezone_name = os.getenv("BOT_TIMEZONE", "Europe/Moscow")

    bot = ReminderBot(token=token, db_path=db_path, local_tz_name=timezone_name)
    bot.run()


if __name__ == "__main__":
    main()
