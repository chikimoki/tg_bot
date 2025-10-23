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


Установка Python на Windows (для новичков)
--------------------------------
Если вы совсем не знакомы с установкой Python на Windows, выполните следующие шаги.

1) Скачайте установщик
- Откройте сайт: https://www.python.org/downloads/windows/
- Нажмите "Download Python 3.10.x" (или более новую стабильную версию).

2) Запустите установщик (ВАЖНО)
- При запуске инсталлятора ОБЯЗАТЕЛЬНО отметьте галочку "Add Python 3.10 to PATH" внизу окна установщика.
- Выберите "Install Now" (рекомендуется).

3) Проверка в командной строке
- Откройте PowerShell или CMD (Win+R, введите cmd или powershell).
- Введите:

```powershell
python --version
pip --version
```

Если вы видите версию Python и pip — всё установлено и PATH настроен.

4) Если Python не распознаётся (ошибка "'python' is not recognized...")
- Возможно, вы пропустили галочку "Add to PATH". Решение:
	- Перезапустите установщик и выберите "Modify", затем отметьте "Add Python to environment variables" и примените.
	- Либо добавьте путь вручную: откройте Пуск → "Edit the system environment variables" → Environment Variables → в разделе User variables найдите PATH → Edit → New и добавьте путь к папке с Python, например:
		- C:\Users\<ВашUser>\AppData\Local\Programs\Python\Python310\
		- и к скриптам: C:\Users\<ВашUser>\AppData\Local\Programs\Python\Python310\Scripts\

5) Создание виртуального окружения и установка зависимостей (PowerShell)

```powershell
cd C:\path\to\your\project\bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Если PowerShell блокирует выполнение скриптов (ошибка ExecutionPolicy), временно разрешите:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
# затем снова активируйте .venv
.\.venv\Scripts\Activate.ps1
```

6) Настройка `.env` и запуск
- Скопируйте `.env.example` в `.env` и заполните `BOT_TOKEN`.
- Запустите бот:

```powershell
python bot.py
```

Полезные советы для начинающих
- Если видите ошибки при установке пакетов, убедитесь, что вы активировали виртуальное окружение.
- Если бот не стартует и пишет про `BOT_TOKEN`, проверьте `.env` и убедитесь, что в нём есть строка `BOT_TOKEN="ваш_токен"`.
- Если все равно что-то не получается, то пишите в тг @amerika_sosat


