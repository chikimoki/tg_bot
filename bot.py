#!/usr/bin/env python3
"""
Telegram Anonymous Bridge Bot

Requirements (Python 3.10+ recommended):
    pip install python-telegram-bot==21.5 pyyaml filelock

Run:
    export BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
    python anon_bridge_bot.py

What it does:
- Acts as a relay between Students and Curators while hiding Student identity.
- Supports text, photos, videos, voice, video notes, audio, documents, stickers.
- Uses JSON/YAML files for storage (no database):
    - data/config.yaml       – admins, banned patterns, optional default curator
    - data/mappings.json     – student<->curator bindings
    - data/threads.json      – transient reply-routing (curator msg -> student chat)
- Content safety filter: blocks messages that contain @usernames or phone-like strings
  (patterns configurable in config.yaml). If blocked, notifies admins.
- Curator replies by replying to the bot's relayed message; bot routes back to the correct student.

Notes on privacy:
- The bot NEVER forwards; it "copies" messages (copyMessage) to avoid exposing senders.
- All curator-facing messages carry an internal "ticket" (short ID) instead of username/phone.

Admin commands:
- /help – basic help
- /link <student_id> <curator_id> – bind student to curator (admin only)
- /unlink <student_id> – remove binding (admin only)
- /list – show bindings (admin only)
- /setpattern <regex> – add banned regex (admin only)
- /delpattern <index> – remove banned regex by index (admin only)
- /patterns – list banned regexes (admin only)
- /setdefaultcurator <curator_id> – set fallback curator (admin only)

File formats:
- config.yaml example is generated on first run if missing.
- mappings.json: {"students": {"<student_chat_id>": {"curator": <curator_chat_id>, "ticket": "S1234"}}, "curators": {"<curator_chat_id>": [<student_chat_id>, ...]}}

"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Any, List, Tuple

from filelock import FileLock
from telegram import (
    Update,
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

import yaml
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------- Storage Helpers ---------------------------
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

CONFIG_YAML = DATA_DIR / "config.yaml"
MAPPINGS_JSON = DATA_DIR / "mappings.json"
THREADS_JSON = DATA_DIR / "threads.json"

CONFIG_LOCK = FileLock(str(CONFIG_YAML) + ".lock")
MAPPINGS_LOCK = FileLock(str(MAPPINGS_JSON) + ".lock")
THREADS_LOCK = FileLock(str(THREADS_JSON) + ".lock")

DEFAULT_CONFIG = {
    "admins": [],  # Telegram user IDs allowed to use admin commands
    "banned_regex": [
        r"@[A-Za-z0-9_]{3,32}",     # @username
        r"\+?\d[\d\s\-]{7,}\b",  # phone-like patterns
    ],
    "default_curator": None,  # chat_id of curator to use when a new student writes without mapping
    "branding": {
        "student_tag_prefix": "S",
    },
}


def load_yaml(path: Path, default: dict) -> dict:
    with CONFIG_LOCK:
        if not path.exists():
            path.write_text(yaml.safe_dump(default, allow_unicode=True), encoding="utf-8")
            return default.copy()
        return yaml.safe_load(path.read_text(encoding="utf-8")) or default.copy()


def save_yaml(path: Path, data: dict) -> None:
    with CONFIG_LOCK:
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def load_json(path: Path, default: dict) -> dict:
    lock = MAPPINGS_LOCK if path == MAPPINGS_JSON else THREADS_LOCK
    with lock:
        if not path.exists():
            path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
            return json.loads(json.dumps(default))
        return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    lock = MAPPINGS_LOCK if path == MAPPINGS_JSON else THREADS_LOCK
    with lock:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------- Seen Users (first-contact registry) ---------------------------
SEEN_JSON = DATA_DIR / "seen_users.json"
SEEN_LOCK = FileLock(str(SEEN_JSON) + ".lock")

def load_seen() -> dict:
    with SEEN_LOCK:
        if not SEEN_JSON.exists():
            SEEN_JSON.write_text(json.dumps({"users": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"users": {}}
        return json.loads(SEEN_JSON.read_text(encoding="utf-8"))

def save_seen(data: dict) -> None:
    with SEEN_LOCK:
        SEEN_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def mark_user_seen(user) -> bool:
    """
    Record a user the first time they interact with the bot.
    Returns True if newly seen; False if already present.
    """
    seen = load_seen()
    uid = str(user.id)
    if uid in seen.get("users", {}):
        return False
    seen.setdefault("users", {})[uid] = {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "ts": int(time.time()),
    }
    save_seen(seen)
    return True

# --------------------------- Domain Model ---------------------------
@dataclass
class Binding:
    student_id: int
    curator_id: int
    ticket: str  # anonymized short tag like S1234


# --------------------------- Mappings & Threads ---------------------------

def ensure_files() -> None:
    load_yaml(CONFIG_YAML, DEFAULT_CONFIG)
    load_json(MAPPINGS_JSON, {"students": {}, "curators": {}})
    load_json(THREADS_JSON, {"routes": {}, "ts": int(time.time())})


def get_config() -> dict:
    return load_yaml(CONFIG_YAML, DEFAULT_CONFIG)


def get_mappings() -> dict:
    return load_json(MAPPINGS_JSON, {"students": {}, "curators": {}})


def get_threads() -> dict:
    return load_json(THREADS_JSON, {"routes": {}, "ts": int(time.time())})


def set_mapping(student_id: int, curator_id: int, ticket: Optional[str] = None) -> Binding:
    cfg = get_config()
    mappings = get_mappings()

    if not ticket:
        prefix = cfg.get("branding", {}).get("student_tag_prefix", "S")
        ticket = f"{prefix}{str(student_id)[-4:]}"  # e.g., S1234

    mappings.setdefault("students", {})[str(student_id)] = {
        "curator": curator_id,
        "ticket": ticket,
    }
    cur_list = mappings.setdefault("curators", {}).setdefault(str(curator_id), [])
    if student_id not in cur_list:
        cur_list.append(student_id)

    save_json(MAPPINGS_JSON, mappings)
    return Binding(student_id=student_id, curator_id=curator_id, ticket=ticket)


def del_mapping(student_id: int) -> bool:
    mappings = get_mappings()
    s = mappings.get("students", {}).pop(str(student_id), None)
    if s:
        cur_id = s.get("curator")
        cur_list = mappings.setdefault("curators", {}).get(str(cur_id), [])
        mappings.setdefault("curators", {})[str(cur_id)] = [x for x in cur_list if x != student_id]
        save_json(MAPPINGS_JSON, mappings)
        return True
    return False


def find_binding(student_id: int) -> Optional[Binding]:
    mappings = get_mappings()
    s = mappings.get("students", {}).get(str(student_id))
    if not s:
        return None
    return Binding(student_id=student_id, curator_id=s["curator"], ticket=s["ticket"]) 


def list_bindings() -> List[Binding]:
    mappings = get_mappings()
    out: List[Binding] = []
    for sid, payload in mappings.get("students", {}).items():
        out.append(Binding(student_id=int(sid), curator_id=int(payload["curator"]), ticket=payload["ticket"]))
    return out


def student_by_ticket(ticket: str) -> Optional[int]:
    """Return student_id by anonymized ticket like 'S1234'."""
    mappings = get_mappings()
    for sid, payload in mappings.get("students", {}).items():
        if payload.get("ticket") == ticket:
            return int(sid)
    return None


def list_students_for_curator(curator_id: int) -> List[Binding]:
    """Return bindings for all students assigned to a curator."""
    mappings = get_mappings()
    res: List[Binding] = []
    for sid, payload in mappings.get("students", {}).items():
        if int(payload.get("curator")) == int(curator_id):
            res.append(Binding(student_id=int(sid), curator_id=int(curator_id), ticket=payload.get("ticket")))
    return res


def route_remember(curator_msg_id: int, curator_chat_id: int, student_id: int) -> None:
    threads = get_threads()
    key = f"{curator_chat_id}:{curator_msg_id}"
    threads.setdefault("routes", {})[key] = student_id
    threads["ts"] = int(time.time())
    save_json(THREADS_JSON, threads)


def route_lookup(curator_msg: Message) -> Optional[int]:
    if not curator_msg.reply_to_message:
        return None
    key = f"{curator_msg.chat_id}:{curator_msg.reply_to_message.message_id}"
    threads = get_threads()
    return threads.get("routes", {}).get(key)


# --------------------------- Filters ---------------------------

def violates_policies(text: str, cfg: dict) -> Optional[str]:
    if not text:
        return None
    for idx, pattern in enumerate(cfg.get("banned_regex", [])):
        try:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return f"Matched banned_regex[{idx}]: {pattern}"
        except re.error:
            # Ignore bad patterns
            continue
    return None


async def notify_admins(app: Application, cfg: dict, message: str) -> None:
    for admin_id in cfg.get("admins", []):
        try:
            await app.bot.send_message(admin_id, message)
        except Exception:
            pass


# --------------------------- Utilities ---------------------------
# Avoid constructing a combined `filters` expression at import time because some
# PTB versions may not expose all attributes (e.g. filters.STICKER). Instead,
# use the `is_media_copyable_message` helper which checks the Message object.
MEDIA_COPYABLE = None

# Filters from PTB expect Update, not Message. We'll use a helper for Message objects.
def is_media_copyable_message(msg: Message) -> bool:
    try:
        return bool(
            getattr(msg, "photo", None) or
            getattr(msg, "video", None) or
            getattr(msg, "video_note", None) or
            getattr(msg, "voice", None) or
            getattr(msg, "audio", None) or
            getattr(msg, "document", None) or
            getattr(msg, "sticker", None)
        )
    except Exception:
        return False


def caption_of(msg: Message) -> Optional[str]:
    return msg.caption if msg.caption else (msg.text or None)


async def copy_message_safely(update: Update, context: ContextTypes.DEFAULT_TYPE, target_chat_id: int) -> Message:
    msg = update.effective_message
    # Prefer copyMessage API – it sends as the bot, hides the original sender.
    try:
        return await context.bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
            protect_content=False,
        )
    except Exception as e:
        # Fallback: best-effort resending
        text = caption_of(msg) or ""
        return await context.bot.send_message(chat_id=target_chat_id, text=f"[copy failed] {text}")


# --------------------------- Handlers ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    # Mark user as seen; if it's the first time, notify ONLY admins (silent for user)
    is_new = mark_user_seen(user)
    if is_new:
        cfg = get_config()
        info = f"NEW USER: id={user.id}"
        if user.username:
            info += f", username=@{user.username}"
        fullname = " ".join(filter(None, [user.first_name, user.last_name]))
        if fullname.strip():
            info += f", name={fullname.strip()}"
        await notify_admins(context.application, cfg, f"🔔 {info}")

    await update.message.reply_text(
        "Бот-ретранслятор готов. Напишите сообщение — мы передадим куратору, не раскрывая ваш ник/номер.\n"
        "Для справки: /help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_config()
    user_id = update.effective_user.id
    admins = cfg.get("admins", [])
    mappings = get_mappings()
    is_curator = str(user_id) in mappings.get("curators", {})

    if is_admin(user_id):
        text = [
            "Админская справка:\n",
            "— Управление связями ученик↔куратор:",
            "/link <student_id> <curator_id>",
            "/unlink <student_id>",
            "/list",
            "",
            "— Паттерны для блокировки:",
            "/patterns",
            "/setpattern <regex>",
            "/delpattern <index>",
            "",
            "/setdefaultcurator <curator_id>",
            "",
            f"Admins: {admins if admins else 'нет'}",
        ]
    elif is_curator:
        text = [
            "Справка куратора:\n",
            "— Пишите в этот чат: сообщения от закреплённых учеников приходят анонимно.",
            "— Чтобы ответить ученику, ответьте на сообщение бота, пришедшее вам.",
            "",
            "Полезные команды:",
            "/mystudents — список ваших закреплённых учеников (тикеты)",
            "/to <student_id|ticket> <текст> — отправить напрямую ученику",
        ]
    else:
        text = [
            "Справка ученика:\n",
            "— Пишите сюда: мы отправим ваше сообщение куратору анонимно.",
            "— Если у вас ещё нет куратора — обратитесь к администратору.",
            "",
            "Команды:",
            "/help — показать эту подсказку",
        ]

    await update.message.reply_text("\n".join(text))


def is_admin(user_id: int) -> bool:
    cfg = get_config()
    return user_id in cfg.get("admins", [])


async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    try:
        student_id = int(context.args[0])
        curator_id = int(context.args[1])
    except Exception:
        await update.message.reply_text("Использование: /link <student_id> <curator_id>")
        return
    b = set_mapping(student_id, curator_id)
    await update.message.reply_text(f"Связал {b.ticket} ({b.student_id}) → куратор {b.curator_id}")


async def unlink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    try:
        student_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("Использование: /unlink <student_id>")
        return
    ok = del_mapping(student_id)
    await update.message.reply_text("Удалено" if ok else "Не найдено")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    rows = [f"{b.ticket}: {b.student_id} → {b.curator_id}" for b in list_bindings()]
    await update.message.reply_text("\n".join(rows) if rows else "Пусто")


async def patterns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    cfg = get_config()
    rows = [f"[{i}] {p}" for i, p in enumerate(cfg.get("banned_regex", []))]
    await update.message.reply_text("\n".join(rows) if rows else "Список пуст")


async def setpattern_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    pattern = " ".join(context.args).strip()
    if not pattern:
        await update.message.reply_text("Использование: /setpattern <regex>")
        return
    cfg = get_config()
    cfg.setdefault("banned_regex", []).append(pattern)
    save_yaml(CONFIG_YAML, cfg)
    await update.message.reply_text(f"Добавлен паттерн: {pattern}")


async def delpattern_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    try:
        idx = int(context.args[0])
    except Exception:
        await update.message.reply_text("Использование: /delpattern <index>")
        return
    cfg = get_config()
    arr = cfg.get("banned_regex", [])
    if 0 <= idx < len(arr):
        removed = arr.pop(idx)
        save_yaml(CONFIG_YAML, cfg)
        await update.message.reply_text(f"Удален паттерн: {removed}")
    else:
        await update.message.reply_text("Нет такого индекса")


async def setdefaultcurator_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    try:
        curator_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("Использование: /setdefaultcurator <curator_id>")
        return
    cfg = get_config()
    cfg["default_curator"] = curator_id
    save_yaml(CONFIG_YAML, cfg)
    await update.message.reply_text(f"default_curator = {curator_id}")


# --------------------------- Core Flow ---------------------------
async def to_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Direct send from curator: /to <student_id|ticket> [text]"""
    user_id = update.effective_user.id
    mappings = get_mappings()
    is_curator = str(user_id) in mappings.get("curators", {}) or is_admin(user_id)
    if not is_curator:
        return

    if not context.args:
        await update.message.reply_text("Использование: /to <student_id|ticket> [текст]")
        return

    ident = context.args[0]
    # resolve target
    if ident.isdigit():
        target_student = int(ident)
    else:
        target_student = student_by_ticket(ident) or None

    if not target_student:
        await update.message.reply_text("Ученик с таким идентификатором не найден.")
        return

    # Режим 1: сразу есть текст -> шлём текст немедленно (как раньше)
    if len(context.args) >= 2:
        payload_text = update.message.text.split(maxsplit=2)
        if len(payload_text) < 3:
            await update.message.reply_text("Укажите текст сообщения после идентификатора.")
            return
        payload_text = payload_text[2]

        cfg = get_config()
        reason = violates_policies(payload_text or "", cfg)
        if reason:
            await notify_admins(context.application, cfg, f"BLOCKED (/to curator->student) from {user_id}: {reason}\n{payload_text}")
            return

        await context.bot.send_message(chat_id=target_student, text=payload_text)
        await update.message.reply_text("Доставлено")
        return

    # Режим 2: без текста -> ждём следующее сообщение (текст или МЕДИА)
    context.user_data["to_target"] = target_student
    context.user_data["awaiting_to"] = True
    await update.message.reply_text("Ок. Пришлите следующее сообщение (текст/фото/видео/голос/файл) — я отправлю его ученику. Для отмены: /cancel_to")

async def cancel_to_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("to_target", None)
    context.user_data.pop("awaiting_to", None)
    await update.message.reply_text("Отменено.")


async def to_student_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data
    if not data.startswith("to_student:"):
        return
    student_id = int(data.split(":", 1)[1])
    # remember selection in user_data and ask for text
    context.user_data["to_target"] = student_id
    context.user_data["awaiting_to"] = True
    kb = [[InlineKeyboardButton("Отмена", callback_data="to_cancel")]]
    await q.message.reply_text("Напишите текст для отправки выбранному ученику:", reply_markup=InlineKeyboardMarkup(kb))


async def to_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    context.user_data.pop("to_target", None)
    context.user_data.pop("awaiting_to", None)
    await q.message.reply_text("Операция отменена.")


async def request_assignment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    # Notify admins that curator requests assignment
    cfg = get_config()
    admins = cfg.get("admins", [])
    user = q.from_user
    note = f"Запрос назначения: куратор id={user.id}"
    if user.username:
        note += f", @{user.username}"
    for aid in admins:
        try:
            await context.application.bot.send_message(aid, note)
        except Exception:
            pass
    await q.message.reply_text("Админы уведомлены. Ожидайте назначения.")

async def handle_from_student(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_config()
    msg = update.effective_message
    student_id = msg.chat_id

    # 1) Policy check on text/caption — if violates, silently drop for user/curator, notify ONLY admins
    text = caption_of(msg)
    reason = violates_policies(text or "", cfg)
    if reason:
        await notify_admins(
            context.application,
            cfg,
            f"BLOCKED (student->curator) from {student_id}: {reason}\n{text}"
        )
        return

    # 2) Resolve binding (or use default_curator)
    binding = find_binding(student_id)
    if not binding:
        default_curator = cfg.get("default_curator")
        if not default_curator:
            # No curator to deliver to — notify admins, keep silent for student
            await notify_admins(
                context.application, cfg,
                f"NO-DELIVERY (no curator) for student {student_id}. Set default_curator or /link.")
            return
        binding = set_mapping(student_id, default_curator)

    # 3) Relay to curator, keeping student anonymous
    header = f"Новое сообщение от {binding.ticket}"

    # If it's text-only, send header+text to avoid duplication
    if text and text.strip() and not is_media_copyable_message(msg):
        sent = await context.bot.send_message(binding.curator_id, f"{header}\n——\n{text}")
        route_remember(sent.message_id, binding.curator_id, student_id)
        await msg.reply_text("Отправлено куратору ✅")
        return

    # For media (or any message), copy to curator to preserve media without exposing sender
    copied = await copy_message_safely(update, context, binding.curator_id)

    # Send ticket header separately if media without caption
    if is_media_copyable_message(msg) and not msg.caption:
        sent_header = await context.bot.send_message(binding.curator_id, header)
        route_remember(sent_header.message_id, binding.curator_id, student_id)

    # Remember routing for replies
    route_remember(copied.message_id, binding.curator_id, student_id)

    # Acknowledge to student
    await msg.reply_text("Отправлено куратору ✅")


async def mystudents_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """For curators: list your assigned students with tickets."""
    user_id = update.effective_user.id
    items = list_students_for_curator(user_id)
    if not items:
        await update.message.reply_text("За вами не закреплено ни одного ученика.")
        return
    # Показываем только тикеты (анонимная информация)
    rows = [f"{b.ticket}" for b in items]
    await update.message.reply_text("\n".join(rows))


async def handle_from_curator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_config()
    msg = update.effective_message

    # Check curator message for violations — silently drop and notify ONLY admins
    text = caption_of(msg)
    reason = violates_policies(text or "", cfg)
    if reason:
        await notify_admins(
            context.application,
            cfg,
            f"BLOCKED (curator->student) from {msg.chat_id}: {reason}\n{text}"
        )
        return

    # Must be a reply to a bot message that came from a student
    target_student = route_lookup(msg)
    if not target_student:
        await msg.reply_text("Пожалуйста, ответьте на сообщение бота, чтобы отправить ученику или используйте /to <ticket> <текст>.")
        return

    # Relay curator's message to student (avoid forwarding)
    if is_media_copyable_message(msg):
        # Copy media to student
        await context.bot.copy_message(chat_id=target_student, from_chat_id=msg.chat_id, message_id=msg.message_id)
    elif msg.text:
        await context.bot.send_message(chat_id=target_student, text=msg.text)
    else:
        await context.bot.send_message(chat_id=target_student, text="(сообщение куратора)")

    await msg.reply_text("Отправлено студенту ✅")


# Dispatcher that routes by chat kind: any private chat that's not an admin command
async def any_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return

    chat = update.effective_chat
    if chat.type != ChatType.PRIVATE:
        return

    user_id = update.effective_user.id
    mappings = get_mappings()
    is_maybe_curator = str(user_id) in mappings.get("curators", {})

    # --- НОВОЕ: двухшаговый /to (ожидание следующего сообщения любого типа) ---
    if is_maybe_curator and context.user_data.get("awaiting_to") and context.user_data.get("to_target"):
        target_student = context.user_data.get("to_target")
        msg = update.effective_message
        cfg = get_config()

        # проверяем политику по тексту/подписи (если есть)
        txt = caption_of(msg)
        reason = violates_policies(txt or "", cfg)
        if reason:
            await notify_admins(context.application, cfg, f"BLOCKED (/to curator->student) from {user_id}: {reason}\n{txt or ''}")
            # по твоему требованию — без уведомлений куратору/ученику
            context.user_data.pop("awaiting_to", None)
            context.user_data.pop("to_target", None)
            return

        try:
            if is_media_copyable_message(msg):
                # копируем как есть (фото/видео/голос/док/стикер) — сохраняет анонимность
                await context.bot.copy_message(
                    chat_id=target_student,
                    from_chat_id=msg.chat_id,
                    message_id=msg.message_id,
                )
            elif msg.text:
                await context.bot.send_message(chat_id=target_student, text=msg.text)
            else:
                # на всякий случай, если тип не распознан
                await context.bot.send_message(chat_id=target_student, text="(сообщение куратора)")
            await update.message.reply_text("Доставлено")
        except Exception:
            await update.message.reply_text("Ошибка при отправке.")

        # сбрасываем состояние
        context.user_data.pop("awaiting_to", None)
        context.user_data.pop("to_target", None)
        return
    # --- КОНЕЦ НОВОГО куска ---

    # далее твоя текущая логика:
    text = update.effective_message.text if update.effective_message else None
    if is_maybe_curator and not (text and text.startswith("/")):
        await handle_from_curator(update, context)
    else:
        await handle_from_student(update, context)


# --------------------------- App Bootstrap ---------------------------
async def post_init(app: Application) -> None:
    ensure_files()
    cfg = get_config()
    if cfg.get("admins"):
        await notify_admins(app, cfg, "✅ Bot запущен")


def build_app() -> Application:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN env var is required")

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("to", to_cmd))
    app.add_handler(CommandHandler("link", link_cmd))
    app.add_handler(CommandHandler("unlink", unlink_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("patterns", patterns_cmd))
    app.add_handler(CommandHandler("setpattern", setpattern_cmd))
    app.add_handler(CommandHandler("delpattern", delpattern_cmd))
    # Callback handler for selecting student in /to flow
    app.add_handler(CallbackQueryHandler(to_student_callback, pattern=r"^to_student:\d+$"))
    app.add_handler(CommandHandler("setdefaultcurator", setdefaultcurator_cmd))
    app.add_handler(CommandHandler("mystudents", mystudents_cmd))
    app.add_handler(CommandHandler("cancel_to", cancel_to_cmd))


    # Content: everything in private chats
    content_filter = (
        filters.ChatType.PRIVATE
        & (
            filters.ALL
        )
    )
    app.add_handler(MessageHandler(content_filter, any_message))

    return app


def main() -> None:
    ensure_files()
    app = build_app()
    print("Bot is running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
