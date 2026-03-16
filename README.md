# Telegram бот-напоминатель

Бот принимает сообщения с датой, временем и текстом события, сохраняет их и отправляет напоминания в тот же чат или в ту же тему Telegram.

Пример сообщения:

```text
16.03.2026 18:30 Встреча с командой
```

После сохранения бот ставит напоминания:

- за 1 день
- за 3 часа
- за 1 час
- в момент события

## Возможности

- принимает события обычным текстом
- хранит события в SQLite (`events.db`)
- восстанавливает напоминания после перезапуска
- подтверждает, что событие и напоминания установлены
- показывает список событий и будущих напоминаний
- удаляет одно событие по ID
- удаляет все события и напоминания в текущем чате или текущей теме
- в forum topics Telegram отвечает и напоминает в той же теме, где было создано событие

## Команды управления

- `/start` - показать справку
- `/help` - показать справку
- `/ping` - проверить, что бот отвечает
- `/list` - показать события и все будущие напоминания в текущем чате или теме
- `/delete <id>` - удалить одно событие по ID
- `/delete_all` - удалить все события и напоминания в текущем чате или теме

`id` события можно узнать через `/list`. Бот показывает события в таком формате:

```text
#3 • 17.03.2026 15:00 • Тест
```

Здесь `3` и есть ID для команды:

```text
/delete 3
```

## Формат события

Поддерживается формат:

```text
ДД.ММ.ГГГГ ЧЧ:ММ Название события
```

Пример:

```text
21.03.2026 09:00 Поезд в аэропорт
```

Если время события уже прошло, бот попросит указать будущее время.

## Как работает в чатах и темах

- в личных сообщениях бот отвечает прямо в диалоге
- в обычной группе бот отвечает в тот же чат
- в супергруппе с темами бот отвечает и напоминает в той же теме, где ты создал событие

Важно для групп:

- отключи `Privacy Mode` через `@BotFather`, иначе бот не будет видеть обычные сообщения
- после изменения настроек бота лучше удалить его из группы и добавить заново

## Запуск локально

1. Установить Python 3.11+.
2. Установить зависимости:

```bash
pip install -r requirements.txt
```

3. Создать бота через `@BotFather` и получить токен.
4. Задать переменные окружения:

Для Windows:

```bash
set TELEGRAM_BOT_TOKEN=ваш_токен
set BOT_TIMEZONE=Europe/Moscow
```

Для Linux:

```bash
export TELEGRAM_BOT_TOKEN="ваш_токен"
export BOT_TIMEZONE="Europe/Moscow"
```

5. Запустить:

```bash
python bot.py
```

## Запуск на сервере Linux

```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv
git clone git@github.com:youngrezzee/bot_napominatel.git
cd bot_napominatel
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="ваш_токен"
export BOT_TIMEZONE="Europe/Moscow"
python3 bot.py
```

Если на Debian/Ubuntu появляется ошибка про `ensurepip is not available`, установи пакет:

```bash
sudo apt install -y python3-venv
```

или пакет для конкретной версии Python, например:

```bash
sudo apt install -y python3.10-venv
```

## Запуск как процесс через systemd

В репозитории уже есть готовые файлы:

- `deploy/reminder-bot.service`
- `.env.example`

1. Подготовь env-файл:

```bash
cd ~/bot_napominatel
cp .env.example .env
nano .env
```

Пример содержимого:

```bash
TELEGRAM_BOT_TOKEN=твой_токен
BOT_TIMEZONE=Europe/Moscow
```

2. Если проект лежит не в `/root/bot_napominatel`, открой `deploy/reminder-bot.service` и исправь пути:

- `WorkingDirectory`
- `EnvironmentFile`
- `ExecStart`

3. Установи сервис:

```bash
sudo cp deploy/reminder-bot.service /etc/systemd/system/reminder-bot.service
sudo systemctl daemon-reload
sudo systemctl enable reminder-bot
sudo systemctl start reminder-bot
```

4. Команды управления сервисом:

```bash
sudo systemctl status reminder-bot
sudo systemctl restart reminder-bot
sudo systemctl stop reminder-bot
sudo journalctl -u reminder-bot -f
```

## Обновление на сервере

```bash
cd ~/bot_napominatel
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart reminder-bot
```

Если бот запущен не через `systemd`, просто перезапусти процесс вручную.

## Хранение данных

- события хранятся в файле `events.db`
- файл создается автоматически при первом запуске
- для forum topics дополнительно сохраняется `message_thread_id`, чтобы напоминания уходили в нужную тему

## Что можно улучшить дальше

- добавить поддержку естественного ввода даты
- ограничить удаление события только его автором
- добавить Docker
- добавить уведомление с кнопками управления
