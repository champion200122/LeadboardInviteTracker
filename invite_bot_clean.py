import asyncio
import functools
import html
import json
import logging
import signal
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import ClientSession
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message

# ──────────────────── КОНФИГ ────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "bot.sqlite3"

if not CONFIG_PATH.exists():
    raise FileNotFoundError(
        "Не найден config.json. Скопируй config.example.json в config.json и заполни его."
    )

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

BOT_TOKEN = config["BOT_TOKEN"]
OWNER_ID = int(config["OWNER_ID"])

# ──────────────────── ЛОГИ ────────────────────
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("invite_bot_clean")


# ──────────────────── SIGTERM ────────────────────
def handle_sigterm(signum, frame):
    log.info("Получен сигнал завершения, выхожу.")
    sys.exit(0)


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)


# ──────────────────── ВСПОМОГАТЕЛЬНЫЕ ────────────────────
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def h(value) -> str:
    return html.escape(str(value), quote=False)


def normalize_username(raw: str | None) -> str:
    return (raw or "").strip().lstrip("@").lower()


def user_has_username(msg: Message) -> bool:
    return bool(msg.from_user and msg.from_user.username)


def caller_id(msg: Message) -> int | None:
    return msg.from_user.id if msg.from_user else None


def caller_username(msg: Message) -> str | None:
    if msg.from_user and msg.from_user.username:
        return normalize_username(msg.from_user.username)
    return None


def participant_title(username: str, display_name: str | None, bold: bool = False) -> str:
    username = normalize_username(username)
    display_name = (display_name or "").strip()

    if display_name and normalize_username(display_name) != username:
        text = f"{h(display_name)} (@{h(username)})"
    else:
        text = f"@{h(username)}"

    return f"<b>{text}</b>" if bold else text


# ──────────────────── БАЗА ────────────────────
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS participants (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                tg_id INTEGER,
                invite_link TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_username TEXT NOT NULL,
                invited_name TEXT NOT NULL,
                added_at TEXT NOT NULL,
                removed INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (participant_username)
                    REFERENCES participants(username)
                    ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contest_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active INTEGER NOT NULL DEFAULT 0,
                chat_id INTEGER,
                started_at TEXT
            )
            """
        )

        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id) VALUES (?)",
            (OWNER_ID,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO contest_state (id, active, chat_id, started_at)
            VALUES (1, 0, NULL, NULL)
            """
        )


def db_is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    if user_id == OWNER_ID:
        return True

    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM admins WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row is not None


def db_get_admins() -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM admins ORDER BY user_id"
        ).fetchall()
    ids = [int(r["user_id"]) for r in rows]
    if OWNER_ID not in ids:
        ids.insert(0, OWNER_ID)
    return ids


def db_add_admin(user_id: int):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))


def db_remove_admin(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))


def db_get_participant(username: str) -> dict | None:
    uname = normalize_username(username)
    if not uname:
        return None

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT username, display_name, tg_id, invite_link, created_at
            FROM participants
            WHERE username = ?
            """,
            (uname,),
        ).fetchone()

    return dict(row) if row else None


def db_participant_exists(username: str) -> bool:
    return db_get_participant(username) is not None


def db_create_participant(username: str, display_name: str | None = None, tg_id: int | None = None):
    uname = normalize_username(username)
    if not uname:
        raise ValueError("Пустой username")

    display = (display_name or "").strip() or uname

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO participants (username, display_name, tg_id, invite_link, created_at)
            VALUES (?, ?, ?, '', ?)
            """,
            (uname, display, tg_id, now_str()),
        )


def db_ensure_participant(username: str, display_name: str | None = None, tg_id: int | None = None) -> tuple[bool, dict]:
    uname = normalize_username(username)
    if not uname:
        raise ValueError("Пустой username")

    existing = db_get_participant(uname)
    if existing:
        new_name = (display_name or existing["display_name"] or uname).strip() or uname
        new_tg_id = tg_id if tg_id is not None else existing["tg_id"]

        with get_conn() as conn:
            conn.execute(
                """
                UPDATE participants
                SET display_name = ?, tg_id = ?
                WHERE username = ?
                """,
                (new_name, new_tg_id, uname),
            )
        return False, db_get_participant(uname)

    db_create_participant(uname, display_name=display_name or uname, tg_id=tg_id)
    return True, db_get_participant(uname)


def db_touch_user(tg_user) -> tuple[bool, str | None]:
    uname = normalize_username(getattr(tg_user, "username", None))
    if not uname:
        return False, None

    created, _ = db_ensure_participant(
        username=uname,
        display_name=getattr(tg_user, "full_name", None) or uname,
        tg_id=getattr(tg_user, "id", None),
    )
    return created, uname


def db_remove_participant(username: str):
    uname = normalize_username(username)
    with get_conn() as conn:
        conn.execute("DELETE FROM participants WHERE username = ?", (uname,))


def db_set_link(username: str, link: str):
    uname = normalize_username(username)
    with get_conn() as conn:
        conn.execute(
            "UPDATE participants SET invite_link = ? WHERE username = ?",
            (link.strip(), uname),
        )


def db_get_top() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                p.username,
                p.display_name,
                p.tg_id,
                p.invite_link,
                COALESCE(SUM(CASE WHEN i.removed = 0 THEN 1 ELSE 0 END), 0) AS invite_count
            FROM participants p
            LEFT JOIN invites i ON i.participant_username = p.username
            GROUP BY p.username, p.display_name, p.tg_id, p.invite_link
            ORDER BY invite_count DESC, LOWER(p.display_name) ASC, p.username ASC
            """
        ).fetchall()

    return [dict(r) for r in rows]


def db_get_invites(username: str) -> list[dict]:
    uname = normalize_username(username)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, invited_name, added_at, removed
            FROM invites
            WHERE participant_username = ?
            ORDER BY id ASC
            """,
            (uname,),
        ).fetchall()
    return [dict(r) for r in rows]


def db_count_valid_invites(username: str) -> int:
    uname = normalize_username(username)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM invites
            WHERE participant_username = ? AND removed = 0
            """,
            (uname,),
        ).fetchone()
    return int(row["cnt"] if row else 0)


def db_add_invite(username: str, invited_name: str):
    uname = normalize_username(username)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO invites (participant_username, invited_name, added_at, removed)
            VALUES (?, ?, ?, 0)
            """,
            (uname, invited_name.strip(), now_str()),
        )


def db_get_invite_by_number(username: str, number: int) -> dict | None:
    uname = normalize_username(username)
    offset = number - 1
    if offset < 0:
        return None

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, invited_name, added_at, removed
            FROM invites
            WHERE participant_username = ?
            ORDER BY id ASC
            LIMIT 1 OFFSET ?
            """,
            (uname, offset),
        ).fetchone()

    return dict(row) if row else None


def db_set_invite_removed(invite_id: int, removed: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE invites SET removed = ? WHERE id = ?",
            (1 if removed else 0, invite_id),
        )


def db_get_contest_state() -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT active, chat_id, started_at FROM contest_state WHERE id = 1"
        ).fetchone()
    if not row:
        return {"active": 0, "chat_id": None, "started_at": None}
    return dict(row)


def db_set_contest_state(active: bool, chat_id: int | None, started_at: str | None):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE contest_state
            SET active = ?, chat_id = ?, started_at = ?
            WHERE id = 1
            """,
            (1 if active else 0, chat_id, started_at),
        )


def db_reset_all():
    with get_conn() as conn:
        conn.execute("DELETE FROM invites")
        conn.execute("DELETE FROM participants")
        conn.execute("DELETE FROM admins")
        conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (OWNER_ID,))
        conn.execute(
            "UPDATE contest_state SET active = 0, chat_id = NULL, started_at = NULL WHERE id = 1"
        )


# ──────────────────── БОТ ────────────────────
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)


# ──────────────────── ДЕКОРАТОРЫ ────────────────────
def require_username(func):
    @functools.wraps(func)
    async def wrapper(msg: Message, *args, **kwargs):
        if not user_has_username(msg):
            await msg.answer(
                "⚠️ Чтобы участвовать в конкурсе, сначала установи Telegram username в настройках."
            )
            return
        return await func(msg, *args, **kwargs)

    return wrapper


def admin_only(func):
    @functools.wraps(func)
    async def wrapper(msg: Message, *args, **kwargs):
        if not db_is_admin(caller_id(msg)):
            await msg.answer("⛔ У тебя нет доступа к этой команде.")
            return
        return await func(msg, *args, **kwargs)

    return wrapper


def owner_only(func):
    @functools.wraps(func)
    async def wrapper(msg: Message, *args, **kwargs):
        if caller_id(msg) != OWNER_ID:
            await msg.answer("⛔ Только главный админ может использовать эту команду.")
            return
        return await func(msg, *args, **kwargs)

    return wrapper


# ═══════════════ ОБЩИЕ КОМАНДЫ ═══════════════
@router.message(CommandStart())
@require_username
async def cmd_start(msg: Message):
    created, uname = db_touch_user(msg.from_user)
    participant = db_get_participant(uname)

    if db_is_admin(caller_id(msg)):
        await msg.answer(
            "👋 <b>Привет, админ!</b>\n\n"
            "Используй /help чтобы увидеть команды."
        )
        return

    if created:
        await msg.answer(
            "✅ <b>Ты зарегистрирован в конкурсе.</b>\n\n"
            f"Твой профиль: {participant_title(participant['username'], participant['display_name'])}\n"
            "/mystats — мои инвайты\n"
            "/top — таблица лидеров"
        )
    else:
        await msg.answer(
            "👋 <b>С возвращением!</b>\n\n"
            "/mystats — мои инвайты\n"
            "/top — таблица лидеров"
        )


@router.message(Command("help"))
async def cmd_help(msg: Message):
    text = (
        "📖 <b>Команды для всех:</b>\n"
        "/start — регистрация\n"
        "/help — помощь\n"
        "/mystats — мои инвайты\n"
        "/top — таблица лидеров\n"
        "/myid — мой Telegram ID\n"
    )

    if db_is_admin(caller_id(msg)):
        text += (
            "\n🔧 <b>Команды для админа:</b>\n"
            "/contest_start — запустить автодобавление в группе\n"
            "/contest_stop — остановить автодобавление\n"
            "/add_participant @username — добавить участника\n"
            "/remove_participant @username — удалить участника\n"
            "/set_link @username ссылка — поставить ссылку\n"
            "/add_invite @username Имя_друга — добавить инвайт\n"
            "/remove_invite @username номер — удалить инвайт\n"
            "/restore_invite @username номер — восстановить инвайт\n"
            "/info @username — подробности\n"
            "/list — список участников\n"
            "/export — выгрузка\n"
            "/broadcast текст — рассылка\n"
            "/admins — список админов\n"
            "/add_admin ID — добавить админа\n"
            "/remove_admin ID — убрать админа\n"
            "/reset_all ПОДТВЕРЖДАЮ — полный сброс\n"
        )

    await msg.answer(text)


# ═══════════════ ПОЛЬЗОВАТЕЛЬСКИЕ ═══════════════
@router.message(Command("myid"))
async def cmd_myid(msg: Message):
    await msg.answer(f"🆔 Твой Telegram ID: <code>{caller_id(msg)}</code>")


@router.message(Command("mystats"))
@require_username
async def cmd_mystats(msg: Message):
    _, uname = db_touch_user(msg.from_user)
    participant = db_get_participant(uname)
    invites = db_get_invites(uname)
    valid = db_count_valid_invites(uname)

    text = (
        f"📊 {participant_title(participant['username'], participant['display_name'], bold=True)} — "
        f"<b>{valid}</b> инвайтов\n\n"
    )

    if participant.get("invite_link"):
        text += f"🔗 Ссылка: {h(participant['invite_link'])}\n\n"

    if invites:
        for i, inv in enumerate(invites, 1):
            status = "❌ удалён" if inv["removed"] else "✅"
            text += f"{i}. {h(inv['invited_name'])} — {status}\n"
    else:
        text += "Пока нет инвайтов."

    await msg.answer(text)


@router.message(Command("top"))
async def cmd_top(msg: Message):
    rows = db_get_top()
    if not rows:
        await msg.answer("📭 Пока нет участников.")
        return

    medals = ["🥇", "🥈", "🥉"]
    text = "🏆 <b>ТАБЛИЦА ЛИДЕРОВ</b>\n\n"

    for i, row in enumerate(rows):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        text += (
            f"{prefix} {participant_title(row['username'], row['display_name'], bold=True)} — "
            f"{row['invite_count']}\n"
        )

    await msg.answer(text)


# ═══════════════ АДМИН: КОНКУРС ═══════════════
@router.message(Command("contest_start"))
@admin_only
async def cmd_contest_start(msg: Message):
    if msg.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await msg.answer("⚠️ Эту команду нужно запускать в группе.")
        return

    db_set_contest_state(True, msg.chat.id, now_str())
    await msg.answer(
        "🚀 <b>Конкурс запущен.</b>\n\n"
        "Теперь каждый пользователь с username, который напишет сообщение в этом чате, "
        "автоматически попадёт в участники.\n\n"
        "Важно: в BotFather нужно выключить Group Privacy."
    )


@router.message(Command("contest_stop"))
@admin_only
async def cmd_contest_stop(msg: Message):
    db_set_contest_state(False, None, None)
    await msg.answer("⏹ <b>Автодобавление остановлено.</b>")


# ═══════════════ АДМИН: УЧАСТНИКИ ═══════════════
@router.message(Command("add_participant"))
@admin_only
async def cmd_add_participant(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /add_participant @username")
        return

    uname = normalize_username(parts[1])
    if not uname:
        await msg.answer("⚠️ Укажи username.")
        return

    if db_participant_exists(uname):
        await msg.answer(f"⚠️ Участник @{h(uname)} уже существует.")
        return

    db_create_participant(uname, display_name=uname)
    await msg.answer(
        f"✅ Участник {participant_title(uname, uname, bold=True)} добавлен.\n"
        "Когда он напишет боту или появится в группе, имя подтянется автоматически."
    )


@router.message(Command("remove_participant"))
@admin_only
async def cmd_remove_participant(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /remove_participant @username")
        return

    uname = normalize_username(parts[1])
    participant = db_get_participant(uname)
    if not participant:
        await msg.answer("❌ Участник не найден.")
        return

    db_remove_participant(uname)
    await msg.answer(f"🗑 Удалён {participant_title(uname, participant['display_name'], bold=True)}.")


@router.message(Command("set_link"))
@admin_only
async def cmd_set_link(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /set_link @username ссылка")
        return

    uname = normalize_username(parts[1])
    link = parts[2].strip()
    participant = db_get_participant(uname)
    if not participant:
        await msg.answer("❌ Участник не найден.")
        return

    db_set_link(uname, link)
    await msg.answer(
        f"🔗 Ссылка обновлена для {participant_title(uname, participant['display_name'], bold=True)}."
    )


@router.message(Command("list"))
@admin_only
async def cmd_list(msg: Message):
    rows = db_get_top()
    if not rows:
        await msg.answer("📭 Участников пока нет.")
        return

    text = "📋 <b>УЧАСТНИКИ</b>\n\n"
    for row in rows:
        link_mark = "🔗" if row["invite_link"] else "—"
        text += (
            f"• {participant_title(row['username'], row['display_name'], bold=True)}\n"
            f"  Инвайтов: {row['invite_count']} | Ссылка: {link_mark}\n"
        )

    await msg.answer(text)


@router.message(Command("info"))
@admin_only
async def cmd_info(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /info @username")
        return

    uname = normalize_username(parts[1])
    participant = db_get_participant(uname)
    if not participant:
        await msg.answer("❌ Участник не найден.")
        return

    invites = db_get_invites(uname)
    valid = db_count_valid_invites(uname)

    text = (
        f"📄 {participant_title(uname, participant['display_name'], bold=True)}\n"
        f"🆔 TG ID: {h(participant['tg_id'] or '—')}\n"
        f"🔗 Ссылка: {h(participant['invite_link'] or '—')}\n"
        f"📊 Активных инвайтов: <b>{valid}</b>\n\n"
    )

    if invites:
        text += "<b>Инвайты:</b>\n"
        for i, inv in enumerate(invites, 1):
            status = "❌ удалён" if inv["removed"] else "✅"
            text += f"{i}. {h(inv['invited_name'])} — {status} ({h(inv['added_at'])})\n"
    else:
        text += "Инвайтов пока нет."

    await msg.answer(text)


# ═══════════════ АДМИН: ИНВАЙТЫ ═══════════════
@router.message(Command("add_invite"))
@admin_only
async def cmd_add_invite(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /add_invite @username Имя_друга")
        return

    uname = normalize_username(parts[1])
    invited_name = parts[2].strip()

    if not uname:
        await msg.answer("⚠️ Укажи username.")
        return
    if not invited_name:
        await msg.answer("⚠️ Укажи имя приглашённого.")
        return

    created, participant = db_ensure_participant(uname)
    db_add_invite(uname, invited_name)
    valid = db_count_valid_invites(uname)

    extra = "\nℹ️ Участник был создан автоматически." if created else ""
    await msg.answer(
        f"✅ Инвайт <b>{h(invited_name)}</b> добавлен для "
        f"{participant_title(uname, participant['display_name'], bold=True)}.\n"
        f"Теперь инвайтов: <b>{valid}</b>{extra}"
    )

    if participant.get("tg_id"):
        try:
            await bot.send_message(
                participant["tg_id"],
                f"🎉 Тебе засчитан новый инвайт: <b>{h(invited_name)}</b>\n"
                f"Теперь у тебя <b>{valid}</b> инвайтов.",
            )
        except Exception:
            pass


@router.message(Command("remove_invite"))
@admin_only
async def cmd_remove_invite(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /remove_invite @username номер")
        return

    uname = normalize_username(parts[1])
    try:
        number = int(parts[2])
    except ValueError:
        await msg.answer("⚠️ Номер должен быть числом.")
        return

    participant = db_get_participant(uname)
    if not participant:
        await msg.answer("❌ Участник не найден.")
        return

    invite = db_get_invite_by_number(uname, number)
    if not invite:
        await msg.answer(f"⚠️ Инвайт №{number} не найден.")
        return
    if invite["removed"]:
        await msg.answer("⚠️ Этот инвайт уже удалён.")
        return

    db_set_invite_removed(invite["id"], True)
    valid = db_count_valid_invites(uname)

    await msg.answer(
        f"🗑 Инвайт №{number} удалён у {participant_title(uname, participant['display_name'], bold=True)}.\n"
        f"Теперь инвайтов: <b>{valid}</b>"
    )


@router.message(Command("restore_invite"))
@admin_only
async def cmd_restore_invite(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /restore_invite @username номер")
        return

    uname = normalize_username(parts[1])
    try:
        number = int(parts[2])
    except ValueError:
        await msg.answer("⚠️ Номер должен быть числом.")
        return

    participant = db_get_participant(uname)
    if not participant:
        await msg.answer("❌ Участник не найден.")
        return

    invite = db_get_invite_by_number(uname, number)
    if not invite:
        await msg.answer(f"⚠️ Инвайт №{number} не найден.")
        return
    if not invite["removed"]:
        await msg.answer("⚠️ Этот инвайт и так активен.")
        return

    db_set_invite_removed(invite["id"], False)
    valid = db_count_valid_invites(uname)

    await msg.answer(
        f"♻️ Инвайт №{number} восстановлен у {participant_title(uname, participant['display_name'], bold=True)}.\n"
        f"Теперь инвайтов: <b>{valid}</b>"
    )


# ═══════════════ АДМИН: АДМИНЫ ═══════════════
@router.message(Command("add_admin"))
@owner_only
async def cmd_add_admin(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /add_admin ID")
        return

    try:
        new_admin_id = int(parts[1])
    except ValueError:
        await msg.answer("⚠️ ID должен быть числом.")
        return

    if db_is_admin(new_admin_id):
        await msg.answer("⚠️ Уже админ.")
        return

    db_add_admin(new_admin_id)
    await msg.answer(f"✅ Добавлен админ <code>{new_admin_id}</code>.")


@router.message(Command("remove_admin"))
@owner_only
async def cmd_remove_admin(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /remove_admin ID")
        return

    try:
        admin_id = int(parts[1])
    except ValueError:
        await msg.answer("⚠️ ID должен быть числом.")
        return

    if admin_id == OWNER_ID:
        await msg.answer("⚠️ Нельзя удалить главного админа.")
        return
    if not db_is_admin(admin_id):
        await msg.answer("❌ Этот ID не является админом.")
        return

    db_remove_admin(admin_id)
    await msg.answer(f"🗑 Админ <code>{admin_id}</code> удалён.")


@router.message(Command("admins"))
@admin_only
async def cmd_admins(msg: Message):
    text = "👑 <b>Админы:</b>\n\n"
    for admin_id in db_get_admins():
        mark = " 👑" if admin_id == OWNER_ID else ""
        text += f"• <code>{admin_id}</code>{mark}\n"
    await msg.answer(text)


# ═══════════════ АДМИН: ПРОЧЕЕ ═══════════════
@router.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(msg: Message):
    text_to_send = (msg.text or "").partition(" ")[2].strip()
    if not text_to_send:
        await msg.answer("⚠️ Формат: /broadcast текст")
        return

    rows = db_get_top()
    sent = 0
    failed = 0
    no_id = 0

    for row in rows:
        if not row["tg_id"]:
            no_id += 1
            continue
        try:
            await bot.send_message(row["tg_id"], f"📢 <b>Сообщение от админа</b>\n\n{text_to_send}")
            sent += 1
        except Exception:
            failed += 1

    answer = f"📤 Отправлено: {sent}\n❌ Ошибок: {failed}\n🙈 Без tg_id: {no_id}"
    await msg.answer(answer)


@router.message(Command("export"))
@admin_only
async def cmd_export(msg: Message):
    rows = db_get_top()
    if not rows:
        await msg.answer("📭 Нет данных для выгрузки.")
        return

    lines = ["ТАБЛИЦА КОНКУРСА", "=" * 40, ""]
    for index, row in enumerate(rows, 1):
        uname = row["username"]
        lines.append(f"#{index} — @{uname}")
        lines.append(f"Имя: {row['display_name']}")
        lines.append(f"Инвайтов: {row['invite_count']}")
        lines.append(f"TG ID: {row['tg_id'] or '—'}")
        lines.append(f"Ссылка: {row['invite_link'] or '—'}")

        invites = db_get_invites(uname)
        if invites:
            for i, inv in enumerate(invites, 1):
                status = "УДАЛЁН" if inv["removed"] else "OK"
                lines.append(f"  {i}. {inv['invited_name']} [{status}] {inv['added_at']}")
        else:
            lines.append("  Нет инвайтов")
        lines.append("")

    result = "\n".join(lines)
    if len(result) > 3800:
        file = BufferedInputFile(result.encode("utf-8"), filename="contest_export.txt")
        await msg.answer_document(file, caption="📊 Экспорт готов")
    else:
        await msg.answer(f"<pre>{h(result)}</pre>")


@router.message(Command("reset_all"))
@owner_only
async def cmd_reset_all(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or parts[1] != "ПОДТВЕРЖДАЮ":
        await msg.answer("⚠️ Для подтверждения напиши: /reset_all ПОДТВЕРЖДАЮ")
        return

    db_reset_all()
    await msg.answer("💥 Все данные сброшены.")


# ═══════════════ АВТОДОБАВЛЕНИЕ ИЗ ГРУППЫ ═══════════════
@router.message(F.chat.type.in_([ChatType.GROUP, ChatType.SUPERGROUP]))
async def auto_register_from_group(msg: Message):
    if not msg.from_user or msg.from_user.is_bot:
        return
    if msg.text and msg.text.startswith("/"):
        return
    if not msg.from_user.username:
        return

    state = db_get_contest_state()
    if not state["active"]:
        return
    if state["chat_id"] != msg.chat.id:
        return

    created, uname = db_touch_user(msg.from_user)
    if created:
        log.info(f"Автодобавлен участник из группы: @{uname}")


# ──────────────────── ЗАЩИТА ОТ ДВУХ ИНСТАНСОВ ────────────────────
def is_conflict_error(exc: Exception) -> bool:
    text = str(exc)
    return (
        "Conflict" in text
        or "terminated by other getUpdates request" in text
        or "can't use getUpdates method while webhook is active" in text
    )


async def wait_for_clear_session():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}"

    async with ClientSession() as session:
        try:
            await session.post(
                f"{url}/deleteWebhook",
                json={"drop_pending_updates": False}
            )
        except Exception as e:
            log.warning(f"Не удалось удалить webhook перед стартом: {e}")

        for attempt in range(20):
            try:
                async with session.post(
                    f"{url}/getUpdates",
                    json={"timeout": 1, "offset": -1},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    result = await resp.json()

                    if result.get("ok"):
                        log.info(f"Сессия Telegram свободна, попытка {attempt + 1}.")
                        return

                    desc = result.get("description", "")
                    if "Conflict" in desc:
                        log.warning(
                            f"Другой инстанс ещё жив. Попытка {attempt + 1}/20, жду 3 сек..."
                        )
                    else:
                        log.warning(f"Проблема при проверке сессии: {desc}")

            except Exception as e:
                log.warning(f"Ошибка проверки сессии, попытка {attempt + 1}: {e}")

            await asyncio.sleep(3)

    log.warning("Не дождались освобождения сессии за 60 секунд, пробую запускаться дальше.")


# ──────────────────── ЗАПУСК ────────────────────
async def main():
    init_db()
    log.info("База инициализирована.")

    while True:
        try:
            await wait_for_clear_session()
            await bot.delete_webhook(drop_pending_updates=True)
            log.info("Запускаю polling...")
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            break

        except Exception as e:
            if is_conflict_error(e):
                log.warning(
                    "Обнаружен второй инстанс бота или конфликт getUpdates. "
                    "Жду 5 секунд и пробую снова..."
                )
                await asyncio.sleep(5)
                continue

            raise


if __name__ == "__main__":
    asyncio.run(main())
