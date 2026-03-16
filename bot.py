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


@dataclass(slots=True)
class Event:
    id: int
    chat_id: int
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
                    created_by_user_id INTEGER NOT NULL,
                    created_by_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    event_at_utc TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                )
                """
            )

    def add_event(
        self,
        chat_id: int,
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
                    created_by_user_id,
                    created_by_name,
                    title,
                    event_at_utc,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
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
                SELECT id, chat_id, created_by_user_id, created_by_name, title, event_at_utc
                FROM events
                WHERE event_at_utc > ?
                ORDER BY event_at_utc ASC
                """,
                (now_iso,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def get_upcoming_events_for_chat(self, chat_id: int) -> list[Event]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, chat_id, created_by_user_id, created_by_name, title, event_at_utc
                FROM events
                WHERE chat_id = ? AND event_at_utc > ?
                ORDER BY event_at_utc ASC
                """,
                (chat_id, now_iso),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def delete_event(self, event_id: int, chat_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM events WHERE id = ? AND chat_id = ?",
                (event_id, chat_id),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            chat_id=row["chat_id"],
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
        await update.effective_chat.send_message(
            self._help_text(),
            parse_mode=ParseMode.HTML,
        )

    async def help_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await update.effective_chat.send_message(
            self._help_text(),
            parse_mode=ParseMode.HTML,
        )

    async def ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_chat.send_message("pong")

    async def list_events(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        events = self.storage.get_upcoming_events_for_chat(chat.id)
        if not events:
            await chat.send_message("Активных событий пока нет.")
            return

        lines = ["Ближайшие события:"]
        for event in events[:20]:
            local_dt = event.event_at_utc.astimezone(self.local_tz)
            lines.append(
                f"#{event.id} • {local_dt.strftime('%d.%m.%Y %H:%M')} • {event.title}"
            )
        await chat.send_message("\n".join(lines))

    async def delete_event(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        if not context.args:
            await chat.send_message("Использование: /delete <id>")
            return

        try:
            event_id = int(context.args[0])
        except ValueError:
            await chat.send_message("ID должен быть числом. Пример: /delete 3")
            return

        deleted = self.storage.delete_event(event_id, chat.id)
        if not deleted:
            await chat.send_message("Событие не найдено.")
            return

        self._remove_jobs_for_event(event_id)
        await chat.send_message(f"Событие #{event_id} удалено.")

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
            await chat.send_message(
                "Не понял формат. Отправь сообщение так:\n"
                "<code>16.03.2026 18:30 Встреча с командой</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        local_dt, title = parsed
        now_local = datetime.now(self.local_tz)
        if local_dt <= now_local:
            await chat.send_message("Дата события уже в прошлом. Укажи будущее время.")
            return

        event = self.storage.add_event(
            chat_id=chat.id,
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
        await chat.send_message(
            "Событие сохранено, напоминания установлены.\n"
            f"ID: #{event.id}\n"
            f"Когда: {local_dt.strftime('%d.%m.%Y %H:%M')} ({self.local_tz.key})\n"
            f"Что: {title}\n"
            f"Напоминания: {reminder_text}"
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

    def _remove_jobs_for_event(self, event_id: int) -> None:
        for job in self.application.job_queue.jobs():
            if job.name.startswith(f"event:{event_id}:"):
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
            "/list - показать ближайшие события\n"
            "/delete ID - удалить событие\n"
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
