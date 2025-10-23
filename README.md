# Telegram Anonymous Bridge Bot

Коротко: бот для анонимной связи между учениками и кураторами. Ученики пишут боту в личку, бот пересылает (копирует) сообщение куратору, при этом не раскрывая идентификаторы учеников. Куратор отвечает, ответ доставляется студенту.

Требования
- Python 3.10+
- Библиотеки перечислены в `requirements.txt` (python-telegram-bot, pyyaml, filelock...)

Быстрый старт

1. Склонируйте репозиторий или скопируйте файлы в папку, например `/home/mihail/bot`.
2. Установите зависимости:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Создайте файл `.env` в корне (рекомендуется). Скопируйте `.env.example` и вставьте токен:

```bash
cp .env.example .env
# отредактируйте .env и замените BOT_TOKEN
```

Альтернатива (быстрый экспорт в текущей сессии):

```bash
export BOT_TOKEN="<ваш_токен_бота>"
```

Windows (PowerShell и CMD)
--------------------------------
Если вы работаете на Windows, используйте PowerShell или CMD. Примеры ниже предполагают, что у вас установлен Python 3.10+ и добавлен в PATH.

PowerShell (рекомендуется):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# отредактируйте .env (например в Notepad) и вставьте BOT_TOKEN
python bot.py
```

CMD (Command Prompt):

```cmd
python -m venv .venv
.\.venv\Scripts\activate.bat
pip install -r requirements.txt
copy .env.example .env
rem отредактируйте .env в Блокноте
python bot.py
```

Запуск в фоне / как сервис
--------------------------------
Для долгоживущего запуска на Windows можно использовать Task Scheduler или запускаемый сервис через NSSM (https://nssm.cc/) чтобы бот автоматически перезапускался и запускался при старте системы.

4. Отредактируйте `data/config.yaml` — добавьте ваш Telegram user_id в `admins` и при желании укажите `default_curator`.

5. Запустите бота:

```bash
python bot.py
```

Файлы данных
- `data/config.yaml` — конфиги (admins, banned_regex и т.д.)
- `data/mappings.json` — маппинг ученик -> куратор
- `data/threads.json` — временные маршруты для ответов (curator_msg -> student_id)

Полезные команды
- /start — регистрация и приветствие
- /help — список команд
- /link /unlink /list — админские команды для управления привязками
- /mystudents — для куратора: список закрепленных учеников

Безопасность и приватность
- Бот использует `copy_message` там, где возможно, чтобы скрыть исходного отправителя.
- Паттерны для блокировки упоминаний/телефонов хранятся в `data/config.yaml`.


