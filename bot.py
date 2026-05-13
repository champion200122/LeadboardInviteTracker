import asyncio
import functools
import json
import logging
import os
import signal
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import ClientSession, web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message

# ──────────────────── ПУТИ И КОНФИГ ────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "bot.sqlite3"
DATA_JSON_PATH = BASE_DIR / "data.json"  # старый JSON, нужен только для миграции

if not CONFIG_PATH.exists():
    raise FileNotFoundError(
        "Не найден config.json. Создай его рядом с ботом. См. config.example.json"
    )

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

BOT_TOKEN: str = config["BOT_TOKEN"]
OWNER_ID: int = int(config.get("OWNER_ID", 827744412))
PING_URL: str = config.get("PING_URL", "")
PING_INTERVAL: int = int(config.get("PING_INTERVAL", 300))
WEB_PORT: int = int(os.environ.get("PORT", config.get("WEB_PORT", 10000)))

# ──────────────────── ЛОГИРОВАНИЕ ────────────────────
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("invite_bot_sqlite")

# ──────────────────── SIGTERM ────────────────────
def handle_sigterm(signum, frame):
    log.info("☠️ Получен SIGTERM/SIGINT — немедленное завершение!")
    os._exit(0)


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

# ──────────────────── БАЗА ДАННЫХ ────────────────────
def normalize_username(raw: str | None) -> str:
    return (raw or "").lower().lstrip("@").strip()


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
                name TEXT NOT NULL,
                invite_link TEXT NOT NULL DEFAULT '',
                tg_id INTEGER
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_username TEXT NOT NULL,
                name TEXT NOT NULL,
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
            """
            INSERT OR IGNORE INTO contest_state (id, active, chat_id, started_at)
            VALUES (1, 0, NULL, NULL)
            """
        )

        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id) VALUES (?)",
            (OWNER_ID,),
        )


def migrate_from_json_if_needed():
    if not DATA_JSON_PATH.exists():
        log.info("data.json не найден — миграция не требуется.")
        return

    with get_conn() as conn:
        participants_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM participants"
        ).fetchone()["cnt"]
        invites_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM invites"
        ).fetchone()["cnt"]

        # Если база уже заполнена — повторно не мигрируем.
        if participants_count > 0 or invites_count > 0:
            log.info("SQLite уже содержит данные — миграция из JSON пропущена.")
            return

    try:
        with open(DATA_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.warning(f"Не удалось прочитать data.json для миграции: {e}")
        return

    admins = data.get("admins", [])
    participants = data.get("participants", {})
    contest = data.get("contest", {}) if isinstance(data.get("contest"), dict) else {}

    imported_participants = 0
    imported_invites = 0

    with get_conn() as conn:
        for admin_id in admins:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO admins (user_id) VALUES (?)",
                    (int(admin_id),),
                )
            except Exception:
                pass

        conn.execute(
            """
            UPDATE contest_state
            SET active = ?, chat_id = ?, started_at = ?
            WHERE id = 1
            """,
            (
                1 if contest.get("active") else 0,
                contest.get("chat_id"),
                contest.get("started_at"),
            ),
        )

        if isinstance(participants, dict):
            for raw_uname, p in participants.items():
                uname = normalize_username(str(raw_uname))
                if not uname or not isinstance(p, dict):
                    continue

                conn.execute(
                    """
                    INSERT OR REPLACE INTO participants (username, name, invite_link, tg_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        uname,
                        p.get("name") or uname,
                        p.get("invite_link") or "",
                        p.get("tg_id"),
                    ),
                )
                imported_participants += 1

                invites = p.get("invites", [])
                if isinstance(invites, list):
                    for inv in invites:
                        if not isinstance(inv, dict):
                            continue
                        conn.execute(
                            """
                            INSERT INTO invites (participant_username, name, added_at, removed)
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                uname,
                                inv.get("name") or "Без имени",
                                inv.get("added_at") or "",
                                1 if inv.get("removed") else 0,
                            ),
                        )
                        imported_invites += 1

    log.info(
        f"✅ Миграция из data.json завершена: участников={imported_participants}, инвайтов={imported_invites}"
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
        result = [int(r["user_id"]) for r in rows]
        if OWNER_ID not in result:
            result.insert(0, OWNER_ID)
        return result


def db_add_admin(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id) VALUES (?)",
            (user_id,),
        )


def db_remove_admin(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM admins WHERE user_id = ?",
            (user_id,),
        )


def db_register_participant(tg_user) -> tuple[bool, str | None]:
    uname = normalize_username(getattr(tg_user, "username", None))
    if not uname:
        return False, None

    with get_conn() as conn:
        row = conn.execute(
            "SELECT username, name FROM participants WHERE username = ?",
            (uname,),
        ).fetchone()

        if row is None:
            conn.execute(
                """
                INSERT INTO participants (username, name, invite_link, tg_id)
                VALUES (?, ?, '', ?)
                """,
                (uname, tg_user.full_name, tg_user.id),
            )
            return True, uname

        current_name = row["name"] if row["name"] else tg_user.full_name
        conn.execute(
            """
            UPDATE participants
            SET tg_id = ?, name = COALESCE(NULLIF(name, ''), ?)
            WHERE username = ?
            """,
            (tg_user.id, current_name, uname),
        )
        return False, uname


def db_add_participant_manual(username: str, name: str):
    uname = normalize_username(username)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO participants (username, name, invite_link, tg_id)
            VALUES (?, ?, '', NULL)
            """,
            (uname, name),
        )


def db_remove_participant(username: str):
    uname = normalize_username(username)
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM participants WHERE username = ?",
            (uname,),
        )


def db_participant_exists(username: str) -> bool:
    uname = normalize_username(username)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM participants WHERE username = ?",
            (uname,),
        ).fetchone()
        return row is not None


def db_set_participant_link(username: str, link: str):
    uname = normalize_username(username)
    with get_conn() as conn:
        conn.execute(
            "UPDATE participants SET invite_link = ? WHERE username = ?",
            (link, uname),
        )


def db_get_participant(username: str) -> dict | None:
    uname = normalize_username(username)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT username, name, invite_link, tg_id
            FROM participants
            WHERE username = ?
            """,
            (uname,),
        ).fetchone()
        return dict(row) if row else None


def db_get_participant_by_username(username: str) -> dict | None:
    return db_get_participant(username)


def db_get_invites(username: str) -> list[dict]:
    uname = normalize_username(username)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, added_at, removed
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


def db_get_top_rows() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                p.username,
                p.name,
                p.invite_link,
                p.tg_id,
                COALESCE(SUM(CASE WHEN i.removed = 0 THEN 1 ELSE 0 END), 0) AS invite_count
            FROM participants p
            LEFT JOIN invites i ON i.participant_username = p.username
            GROUP BY p.username, p.name, p.invite_link, p.tg_id
            ORDER BY invite_count DESC, LOWER(p.name) ASC, p.username ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def db_get_all_participants() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT username, name, invite_link, tg_id
            FROM participants
            ORDER BY LOWER(name) ASC, username ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def db_add_invite(username: str, invite_name: str, added_at: str):
    uname = normalize_username(username)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO invites (participant_username, name, added_at, removed)
            VALUES (?, ?, ?, 0)
            """,
            (uname, invite_name, added_at),
        )


def db_get_invite_by_number(username: str, number: int) -> dict | None:
    uname = normalize_username(username)
    offset = number - 1
    if offset < 0:
        return None

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, name, added_at, removed
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


def db_get_broadcast_targets() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT username, name, tg_id FROM participants ORDER BY username ASC"
        ).fetchall()
        return [dict(r) for r in rows]


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
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id) VALUES (?)",
            (OWNER_ID,),
        )
        conn.execute(
            "UPDATE contest_state SET active = 0, chat_id = NULL, started_at = NULL WHERE id = 1"
        )


# ──────────────────── ВСПОМОГАТЕЛЬНЫЕ ────────────────────
def get_caller_id(msg: Message) -> int | None:
    return msg.from_user.id if msg.from_user else None


def get_caller_username(msg: Message) -> str | None:
    if msg.from_user and msg.from_user.username:
        return normalize_username(msg.from_user.username)
    return None


def is_owner(msg: Message) -> bool:
    uid = get_caller_id(msg)
    return uid == OWNER_ID


# ──────────────────── БОТ ────────────────────
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ──────────────────── ДЕКОРАТОРЫ ────────────────────
def require_username(func):
    @functools.wraps(func)
    async def wrapper(msg: Message, *args, **kwargs):
        if not msg.from_user or not msg.from_user.username:
            await msg.answer(
                "⚠️ У тебя не установлен Telegram username.\n"
                "Установи его в настройках Telegram, затем попробуй снова."
            )
            return
        return await func(msg, *args, **kwargs)

    return wrapper



def admin_only(func):
    @functools.wraps(func)
    async def wrapper(msg: Message, *args, **kwargs):
        if not db_is_admin(get_caller_id(msg)):
            await msg.answer("⛔ У тебя нет доступа к этой команде.")
            return
        return await func(msg, *args, **kwargs)

    return wrapper



def owner_only(func):
    @functools.wraps(func)
    async def wrapper(msg: Message, *args, **kwargs):
        if not is_owner(msg):
            await msg.answer("⛔ Только главный админ может использовать эту команду.")
            return
        return await func(msg, *args, **kwargs)

    return wrapper


# ═══════════════ ОБЩИЕ КОМАНДЫ ═══════════════
@router.message(CommandStart())
@require_username
async def cmd_start(msg: Message):
    created, _ = db_register_participant(msg.from_user)

    if db_is_admin(get_caller_id(msg)):
        await msg.answer(
            "👋 <b>Привет, админ!</b>\n\n"
            "Используй /help чтобы увидеть все команды."
        )
        return

    if created:
        await msg.answer(
            "✅ <b>Ты зарегистрирован в конкурсе!</b>\n\n"
            "/mystats — мои инвайты\n"
            "/top — таблица лидеров"
        )
    else:
        await msg.answer(
            "👋 <b>С возвращением!</b>\n\n"
            "Этот бот ведёт учёт инвайтов для конкурса.\n"
            "/mystats — мои инвайты\n"
            "/top — таблица лидеров"
        )


@router.message(Command("help"))
@require_username
async def cmd_help(msg: Message):
    text = (
        "📖 <b>Команды для всех:</b>\n"
        "/start — регистрация / приветствие\n"
        "/help — список команд\n"
        "/mystats — мои инвайты\n"
        "/top — таблица лидеров\n"
    )

    if db_is_admin(get_caller_id(msg)):
        text += (
            "\n🔧 <b>Команды для админов:</b>\n\n"
            "<b>Конкурс:</b>\n"
            "/contest_start — запустить конкурс в текущей группе\n"
            "/contest_stop — остановить автодобавление\n\n"
            "<b>Участники:</b>\n"
            "/add_participant @user Имя — добавить\n"
            "/remove_participant @user — удалить\n"
            "/set_link @user ссылка — задать ссылку\n"
            "/list — список всех\n"
            "/info @user — подробно\n\n"
            "<b>Инвайты:</b>\n"
            "/add_invite @user Имя_друга — +1\n"
            "/remove_invite @user номер — −1\n"
            "/restore_invite @user номер — восстановить\n\n"
            "<b>Админы:</b>\n"
            "/add_admin ID — назначить\n"
            "/remove_admin ID — снять\n"
            "/admins — список\n"
            "/myid — узнать свой ID\n\n"
            "<b>Прочее:</b>\n"
            "/broadcast текст — рассылка\n"
            "/export — выгрузка таблицы\n"
            "/reset_all ПОДТВЕРЖДАЮ — ⚠️ сброс\n"
        )

    await msg.answer(text)


# ═══════════════ ПОЛЬЗОВАТЕЛЬСКИЕ ═══════════════
@router.message(Command("mystats"))
@require_username
async def cmd_mystats(msg: Message):
    uname = get_caller_username(msg)
    participant = db_get_participant(uname)

    if not participant:
        created, _ = db_register_participant(msg.from_user)
        if created:
            await msg.answer(
                "✅ Ты автоматически зарегистрирован в конкурсе.\n"
                "Пока у тебя 0 инвайтов."
            )
        else:
            await msg.answer("❌ Ты не зарегистрирован как участник конкурса.")
        return

    invites = db_get_invites(uname)
    valid = db_count_valid_invites(uname)

    text = f"📊 <b>{participant['name']}</b> (@{uname}) — инвайтов: <b>{valid}</b>\n\n"

    if participant.get("invite_link"):
        text += f"🔗 Твоя ссылка: {participant['invite_link']}\n\n"

    for i, inv in enumerate(invites, 1):
        status = "❌ УДАЛЁН" if inv.get("removed") else "✅"
        text += f"  {i}. {inv['name']} {status}\n"

    if not invites:
        text += "  Пока нет инвайтов."

    await msg.answer(text)


@router.message(Command("top"))
async def cmd_top(msg: Message):
    board = db_get_top_rows()

    if not board:
        await msg.answer("📭 Пока нет участников.")
        return

    medals = ["🥇", "🥈", "🥉"]
    text = "🏆 <b>ТАБЛИЦА ЛИДЕРОВ</b>\n\n"

    for i, row in enumerate(board):
        medal = medals[i] if i < 3 else f"{i + 1}."
        text += (
            f"{medal} <b>{row['name']}</b> (@{row['username']}) — "
            f"{row['invite_count']} инвайтов\n"
        )

    await msg.answer(text)


# ═══════════════ АДМИН: КОНКУРС ═══════════════
@router.message(Command("contest_start"))
@admin_only
async def cmd_contest_start(msg: Message):
    if msg.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await msg.answer("⚠️ Эту команду нужно запускать в группе.")
        return

    db_set_contest_state(
        active=True,
        chat_id=msg.chat.id,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    await msg.answer(
        "🚀 <b>Конкурс запущен!</b>\n\n"
        "Теперь каждый пользователь с username, который напишет сообщение в этом чате,\n"
        "будет автоматически добавлен в участники.\n\n"
        "Важно: у бота должен быть выключен Privacy Mode в BotFather, иначе он не увидит обычные сообщения."
    )


@router.message(Command("contest_stop"))
@admin_only
async def cmd_contest_stop(msg: Message):
    db_set_contest_state(active=False, chat_id=None, started_at=None)
    await msg.answer("⏹ <b>Конкурс остановлен.</b>")


@router.message(F.chat.type.in_([ChatType.GROUP, ChatType.SUPERGROUP]))
async def auto_register_from_group(msg: Message):
    if not msg.from_user or msg.from_user.is_bot:
        return

    if not msg.from_user.username:
        return

    state = db_get_contest_state()
    if not state.get("active"):
        return

    if state.get("chat_id") != msg.chat.id:
        return

    created, uname = db_register_participant(msg.from_user)
    if created:
        log.info(f"Автодобавление участника из группы: @{uname}")


# ═══════════════ АДМИН: УЧАСТНИКИ ═══════════════
@router.message(Command("add_participant"))
@admin_only
async def cmd_add_participant(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /add_participant @username Имя")
        return

    uname = normalize_username(parts[1])
    name = parts[2].strip()

    if not uname:
        await msg.answer("⚠️ Укажи @username участника.")
        return

    if db_participant_exists(uname):
        await msg.answer(f"⚠️ Участник @{uname} уже существует.")
        return

    db_add_participant_manual(uname, name)
    await msg.answer(f"✅ Участник <b>{name}</b> (@{uname}) добавлен!")


@router.message(Command("remove_participant"))
@admin_only
async def cmd_remove_participant(msg: Message):
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /remove_participant @username")
        return

    uname = normalize_username(parts[1])
    participant = db_get_participant(uname)
    if not participant:
        await msg.answer("❌ Участник не найден.")
        return

    db_remove_participant(uname)
    await msg.answer(f"🗑 Участник <b>{participant['name']}</b> (@{uname}) удалён.")


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

    db_set_participant_link(uname, link)
    await msg.answer(f"🔗 Ссылка для <b>{participant['name']}</b> (@{uname}) установлена.")


@router.message(Command("list"))
@admin_only
async def cmd_list(msg: Message):
    rows = db_get_top_rows()
    if not rows:
        await msg.answer("📭 Нет участников.")
        return

    text = "📋 <b>УЧАСТНИКИ КОНКУРСА</b>\n\n"
    for row in rows:
        link_status = "🔗 ссылка есть" if row.get("invite_link") else "❌ нет ссылки"
        text += (
            f"• <b>{row['name']}</b> (@{row['username']})\n"
            f"    Инвайтов: {row['invite_count']} | {link_status}\n"
        )

    await msg.answer(text)


@router.message(Command("info"))
@admin_only
async def cmd_info(msg: Message):
    parts = (msg.text or "").split()
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
        f"📄 <b>Участник:</b> {participant['name']}\n"
        f"👤 <b>Username:</b> @{uname}\n"
        f"🆔 <b>TG ID:</b> {participant.get('tg_id') or '—'}\n"
        f"🔗 <b>Ссылка:</b> {participant.get('invite_link') or '—'}\n"
        f"📊 <b>Инвайтов:</b> {valid}\n\n"
        f"<b>Список инвайтов:</b>\n"
    )

    for i, inv in enumerate(invites, 1):
        status = "❌ УДАЛЁН" if inv.get("removed") else "✅ ок"
        text += f"\n  <b>{i}.</b> {inv['name']} — {status}\n      Добавлен: {inv.get('added_at', '?')}\n"

    if not invites:
        text += "  Пока нет инвайтов.\n"

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
    invite_name = parts[2].strip()
    participant = db_get_participant(uname)
    if not participant:
        await msg.answer("❌ Участник не найден. Сначала /add_participant")
        return

    db_add_invite(uname, invite_name, datetime.now().strftime("%Y-%m-%d %H:%M"))
    valid = db_count_valid_invites(uname)

    await msg.answer(
        f"✅ Инвайт <b>{invite_name}</b> добавлен для <b>{participant['name']}</b> (@{uname})!\n"
        f"📊 Теперь инвайтов: <b>{valid}</b>"
    )

    tg_id = participant.get("tg_id")
    if tg_id:
        try:
            await bot.send_message(
                tg_id,
                f"🎉 Тебе засчитан новый инвайт: <b>{invite_name}</b>!\n"
                f"📊 Всего инвайтов: <b>{valid}</b>",
            )
        except Exception:
            pass


@router.message(Command("remove_invite"))
@admin_only
async def cmd_remove_invite(msg: Message):
    parts = (msg.text or "").split()
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
        await msg.answer(f"⚠️ Нет инвайта с номером {number}. Используй /info @{uname}")
        return

    if invite.get("removed"):
        await msg.answer("⚠️ Этот инвайт уже удалён.")
        return

    db_set_invite_removed(invite["id"], True)
    valid = db_count_valid_invites(uname)

    await msg.answer(
        f"🗑 Инвайт #{number} (<b>{invite['name']}</b>) удалён у <b>{participant['name']}</b> (@{uname}).\n"
        f"📊 Теперь инвайтов: <b>{valid}</b>"
    )

    tg_id = participant.get("tg_id")
    if tg_id:
        try:
            await bot.send_message(
                tg_id,
                f"⚠️ Инвайт <b>{invite['name']}</b> был аннулирован.\n"
                f"📊 Теперь инвайтов: <b>{valid}</b>",
            )
        except Exception:
            pass


@router.message(Command("restore_invite"))
@admin_only
async def cmd_restore_invite(msg: Message):
    parts = (msg.text or "").split()
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
        await msg.answer(f"⚠️ Нет инвайта с номером {number}.")
        return

    if not invite.get("removed"):
        await msg.answer("⚠️ Этот инвайт и так активен.")
        return

    db_set_invite_removed(invite["id"], False)
    valid = db_count_valid_invites(uname)

    await msg.answer(
        f"♻️ Инвайт #{number} (<b>{invite['name']}</b>) восстановлен!\n"
        f"📊 Теперь инвайтов: <b>{valid}</b>"
    )


# ═══════════════ АДМИН: УПРАВЛЕНИЕ АДМИНАМИ ═══════════════
@router.message(Command("add_admin"))
@owner_only
async def cmd_add_admin(msg: Message):
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /add_admin <ID>\nПример: /add_admin 123456789")
        return

    try:
        new_admin_id = int(parts[1])
    except ValueError:
        await msg.answer("⚠️ Укажи числовой Telegram ID.\nПример: /add_admin 123456789")
        return

    if db_is_admin(new_admin_id):
        await msg.answer("⚠️ Уже админ.")
        return

    db_add_admin(new_admin_id)
    await msg.answer(f"✅ Пользователь с ID <code>{new_admin_id}</code> теперь админ.")


@router.message(Command("remove_admin"))
@owner_only
async def cmd_remove_admin(msg: Message):
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /remove_admin <ID>")
        return

    try:
        rm_admin_id = int(parts[1])
    except ValueError:
        await msg.answer("⚠️ Укажи числовой Telegram ID.")
        return

    if rm_admin_id == OWNER_ID:
        await msg.answer("⚠️ Нельзя снять самого себя.")
        return

    if not db_is_admin(rm_admin_id):
        await msg.answer("❌ Не является админом.")
        return

    db_remove_admin(rm_admin_id)
    await msg.answer(f"🗑 Пользователь с ID <code>{rm_admin_id}</code> больше не админ.")


@router.message(Command("admins"))
@admin_only
async def cmd_admins(msg: Message):
    text = "👑 <b>Админы бота:</b>\n\n"
    for admin_id in db_get_admins():
        owner_mark = " 👑 (главный)" if admin_id == OWNER_ID else ""
        text += f"• <code>{admin_id}</code>{owner_mark}\n"
    await msg.answer(text)


# ═══════════════ АДМИН: ПРОЧЕЕ ═══════════════
@router.message(Command("myid"))
async def cmd_myid(msg: Message):
    uid = get_caller_id(msg)
    await msg.answer(f"🆔 Твой Telegram ID: <code>{uid}</code>")


@router.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(msg: Message):
    text_to_send = (msg.text or "").partition(" ")[2].strip()
    if not text_to_send:
        await msg.answer("⚠️ Формат: /broadcast текст")
        return

    sent = 0
    failed = 0
    no_id = 0

    for p in db_get_broadcast_targets():
        tg_id = p.get("tg_id")
        if not tg_id:
            no_id += 1
            continue

        try:
            await bot.send_message(tg_id, f"📢 <b>Объявление:</b>\n\n{text_to_send}")
            sent += 1
        except Exception:
            failed += 1

    result = f"📤 Отправлено: {sent} | Не доставлено: {failed}"
    if no_id:
        result += f"\n⚠️ {no_id} участников ещё не написали боту /start"

    await msg.answer(result)


@router.message(Command("export"))
@admin_only
async def cmd_export(msg: Message):
    board = db_get_top_rows()
    if not board:
        await msg.answer("📭 Нет данных.")
        return

    lines = ["ТАБЛИЦА КОНКУРСА ИНВАЙТОВ", "=" * 40, ""]

    for rank, row in enumerate(board, 1):
        uname = row["username"]
        lines.append(f"#{rank} — {row['name']} (@{uname})")
        lines.append(f"     Инвайтов: {row['invite_count']}")
        lines.append(f"     TG ID: {row.get('tg_id') or '—'}")
        lines.append(f"     Ссылка: {row.get('invite_link') or '—'}")
        for i, inv in enumerate(db_get_invites(uname), 1):
            status = "[УДАЛЁН]" if inv.get("removed") else "[OK]"
            lines.append(f"       {i}. {inv['name']} {status} (добавлен {inv.get('added_at', '?')})")
        lines.append("")

    result = "\n".join(lines)
    if len(result) > 4000:
        file = BufferedInputFile(result.encode("utf-8"), filename="export.txt")
        await msg.answer_document(file, caption="📊 Полная выгрузка")
    else:
        await msg.answer(f"<pre>{result}</pre>")


@router.message(Command("reset_all"))
@owner_only
async def cmd_reset_all(msg: Message):
    parts = (msg.text or "").split()
    if len(parts) < 2 or parts[1] != "ПОДТВЕРЖДАЮ":
        await msg.answer("⚠️ Это удалит ВСЕ данные!\nДля подтверждения: /reset_all ПОДТВЕРЖДАЮ")
        return

    db_reset_all()
    await msg.answer("💥 Все данные сброшены.")


# ──────────────────── ANTI-SLEEP ────────────────────
async def self_ping():
    if not PING_URL:
        log.warning("PING_URL пуст — anti-sleep отключен.")
        return

    await asyncio.sleep(30)
    async with ClientSession() as session:
        while True:
            try:
                async with session.get(PING_URL) as resp:
                    log.info(f"Self-ping → {resp.status}")
            except Exception as e:
                log.warning(f"Self-ping error: {e}")

            await asyncio.sleep(PING_INTERVAL)


# ──────────────────── WEB-СЕРВЕР ────────────────────
async def handle_health(request):
    return web.Response(text="OK")


async def handle_stats(request):
    board = db_get_top_rows()

    rows = ""
    for i, row in enumerate(board, 1):
        rows += (
            f"<tr><td>{i}</td><td>{row['name']}</td>"
            f"<td>@{row['username']}</td><td>{row['invite_count']}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\">
  <title>Invite Contest</title>
  <style>
    body {{ font-family: sans-serif; max-width: 700px; margin: 40px auto; background: #1a1a2e; color: #eee; }}
    h1 {{ text-align: center; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px; border: 1px solid #444; text-align: center; }}
    th {{ background: #16213e; }}
    tr:nth-child(even) {{ background: #0f3460; }}
  </style>
</head>
<body>
  <h1>🏆 Таблица Инвайтов</h1>
  <table>
    <tr><th>#</th><th>Участник</th><th>Username</th><th>Инвайты</th></tr>
    {rows}
  </table>
  <p style=\"text-align:center;margin-top:20px;color:#666;\">Обновление по F5</p>
</body>
</html>"""

    return web.Response(text=html, content_type="text/html")


# ──────────────────── ЗАПУСК ────────────────────
async def wait_for_clear_session():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}"

    async with ClientSession() as session:
        await session.post(f"{url}/deleteWebhook", json={"drop_pending_updates": True})

        for attempt in range(20):
            try:
                async with session.post(
                    f"{url}/getUpdates",
                    json={"timeout": 1, "offset": -1},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    result = await resp.json()
                    if result.get("ok"):
                        log.info(f"✅ Попытка {attempt + 1}: сессия свободна!")
                        return

                    desc = result.get("description", "")
                    if "Conflict" in desc:
                        log.info(f"⏳ Попытка {attempt + 1}/20: старый инстанс жив, ждём 3 сек...")
                    else:
                        log.warning(f"❌ Попытка {attempt + 1}: {desc}")
            except Exception as e:
                log.warning(f"❌ Попытка {attempt + 1}: {e}")

            await asyncio.sleep(3)

    log.warning("⚠️ Не дождались очистки за 60 сек, стартуем как есть.")


async def main():
    init_db()
    migrate_from_json_if_needed()

    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/stats", handle_stats)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    log.info(f"✅ Web server on port {WEB_PORT}")

    await wait_for_clear_session()
    asyncio.create_task(self_ping())

    log.info("🚀 Starting polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
