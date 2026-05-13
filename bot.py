import asyncio
import functools
import json
import logging
import os
import signal
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

# ──────────────────── КОНФИГ ────────────────────
CONFIG_PATH = Path(__file__).parent / "config.json"
DATA_PATH = Path(__file__).parent / "data.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

BOT_TOKEN: str = config["BOT_TOKEN"]
OWNER_ID: int = 827744412  # Telegram ID главного админа
PING_URL: str = config.get("PING_URL", "")
PING_INTERVAL: int = int(config.get("PING_INTERVAL", 300))
WEB_PORT: int = int(os.environ.get("PORT", 10000))

# ──────────────────── ЛОГИРОВАНИЕ ────────────────────
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("invite_bot")

# ──────────────────── SIGTERM ────────────────────
def handle_sigterm(signum, frame):
    log.info("☠️ Получен SIGTERM/SIGINT — немедленное завершение!")
    os._exit(0)


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

# ──────────────────── ДАННЫЕ ────────────────────
def default_data() -> dict:
    return {
        "admins": [OWNER_ID],
        "participants": {},
        "contest": {
            "active": False,
            "chat_id": None,
            "started_at": None,
        },
    }


def normalize_username(raw: str | None) -> str:
    return (raw or "").lower().lstrip("@").strip()


def load_data() -> dict:
    data = default_data()

    if DATA_PATH.exists():
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception as e:
            log.warning(f"Не удалось прочитать data.json, использую значения по умолчанию: {e}")

    if not isinstance(data.get("admins"), list):
        data["admins"] = [OWNER_ID]
    if OWNER_ID not in data["admins"]:
        data["admins"].insert(0, OWNER_ID)

    raw_participants = data.get("participants", {})
    if not isinstance(raw_participants, dict):
        raw_participants = {}

    participants: dict[str, dict] = {}
    for raw_uname, raw_participant in raw_participants.items():
        uname = normalize_username(str(raw_uname))
        if not uname:
            continue

        participant = raw_participant if isinstance(raw_participant, dict) else {}
        participants[uname] = {
            "name": participant.get("name") or uname,
            "invite_link": participant.get("invite_link") or "",
            "tg_id": participant.get("tg_id"),
            "invites": participant.get("invites") if isinstance(participant.get("invites"), list) else [],
        }

    data["participants"] = participants

    contest = data.get("contest", {})
    if not isinstance(contest, dict):
        contest = {}
    data["contest"] = {
        "active": bool(contest.get("active", False)),
        "chat_id": contest.get("chat_id"),
        "started_at": contest.get("started_at"),
    }

    return data


def save_data(data: dict):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_caller_id(msg: Message) -> int | None:
    return msg.from_user.id if msg.from_user else None


def get_caller_username(msg: Message) -> str | None:
    if msg.from_user and msg.from_user.username:
        return normalize_username(msg.from_user.username)
    return None


def is_admin(msg: Message, data: dict) -> bool:
    uid = get_caller_id(msg)
    if uid is None:
        return False
    return uid == OWNER_ID or uid in data.get("admins", [])


def is_owner(msg: Message) -> bool:
    uid = get_caller_id(msg)
    return uid == OWNER_ID


def count_valid_invites(participant: dict) -> int:
    return sum(1 for inv in participant.get("invites", []) if not inv.get("removed", False))


def register_participant(data: dict, tg_user) -> tuple[bool, str | None]:
    uname = normalize_username(getattr(tg_user, "username", None))
    if not uname:
        return False, None

    created = uname not in data["participants"]

    if created:
        data["participants"][uname] = {
            "name": tg_user.full_name,
            "invite_link": "",
            "tg_id": tg_user.id,
            "invites": [],
        }
    else:
        participant = data["participants"][uname]
        participant.setdefault("name", tg_user.full_name)
        participant.setdefault("invite_link", "")
        participant.setdefault("invites", [])
        participant["tg_id"] = tg_user.id
        if not participant.get("name"):
            participant["name"] = tg_user.full_name

    return created, uname


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
        data = load_data()
        if not is_admin(msg, data):
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
    data = load_data()
    created, uname = register_participant(data, msg.from_user)
    save_data(data)

    if is_admin(msg, data):
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
    data = load_data()
    text = (
        "📖 <b>Команды для всех:</b>\n"
        "/start — регистрация / приветствие\n"
        "/help — список команд\n"
        "/mystats — мои инвайты\n"
        "/top — таблица лидеров\n"
    )

    if is_admin(msg, data):
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
            "/add_admin ID — назначить (Telegram ID)\n"
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
    data = load_data()
    uname = get_caller_username(msg)

    if uname not in data["participants"]:
        created, _ = register_participant(data, msg.from_user)
        save_data(data)
        if created:
            await msg.answer(
                "✅ Ты автоматически зарегистрирован в конкурсе.\n"
                "Пока у тебя 0 инвайтов."
            )
        else:
            await msg.answer("❌ Ты не зарегистрирован как участник конкурса.")
        return

    data["participants"][uname]["tg_id"] = msg.from_user.id
    save_data(data)

    p = data["participants"][uname]
    valid = count_valid_invites(p)
    text = f"📊 <b>{p['name']}</b> (@{uname}) — инвайтов: <b>{valid}</b>\n\n"

    if p.get("invite_link"):
        text += f"🔗 Твоя ссылка: {p['invite_link']}\n\n"

    for i, inv in enumerate(p.get("invites", []), 1):
        status = "❌ УДАЛЁН" if inv.get("removed") else "✅"
        text += f"  {i}. {inv['name']} {status}\n"

    if not p.get("invites"):
        text += "  Пока нет инвайтов."

    await msg.answer(text)


@router.message(Command("top"))
async def cmd_top(msg: Message):
    data = load_data()

    if not data["participants"]:
        await msg.answer("📭 Пока нет участников.")
        return

    board = []
    for uname, p in data["participants"].items():
        valid = count_valid_invites(p)
        board.append((p["name"], valid, uname))

    board.sort(key=lambda x: (-x[1], x[0].lower(), x[2]))
    medals = ["🥇", "🥈", "🥉"]

    text = "🏆 <b>ТАБЛИЦА ЛИДЕРОВ</b>\n\n"
    for i, (name, count, uname) in enumerate(board):
        medal = medals[i] if i < 3 else f"{i + 1}."
        text += f"{medal} <b>{name}</b> (@{uname}) — {count} инвайтов\n"

    await msg.answer(text)


# ═══════════════ АДМИН: КОНКУРС ═══════════════
@router.message(Command("contest_start"))
@admin_only
async def cmd_contest_start(msg: Message):
    if msg.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await msg.answer("⚠️ Эту команду нужно запускать в группе.")
        return

    data = load_data()
    data["contest"] = {
        "active": True,
        "chat_id": msg.chat.id,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_data(data)

    await msg.answer(
        "🚀 <b>Конкурс запущен!</b>\n\n"
        "Теперь каждый пользователь с username, который напишет сообщение в этом чате,\n"
        "будет автоматически добавлен в участники.\n\n"
        "Важно: у бота должен быть выключен Privacy Mode в BotFather, иначе он не увидит обычные сообщения."
    )


@router.message(Command("contest_stop"))
@admin_only
async def cmd_contest_stop(msg: Message):
    data = load_data()
    data["contest"] = {
        "active": False,
        "chat_id": None,
        "started_at": None,
    }
    save_data(data)

    await msg.answer("⏹ <b>Конкурс остановлен.</b>")


@router.message(F.chat.type.in_([ChatType.GROUP, ChatType.SUPERGROUP]))
async def auto_register_from_group(msg: Message):
    if not msg.from_user or msg.from_user.is_bot:
        return

    if not msg.from_user.username:
        return

    # Игнорируем сервисные сообщения без текста/подписи/контента от пользователя.
    if msg.text is None and msg.caption is None and msg.content_type == "new_chat_members":
        return

    data = load_data()
    contest = data.get("contest", {})

    if not contest.get("active"):
        return

    if contest.get("chat_id") != msg.chat.id:
        return

    created, uname = register_participant(data, msg.from_user)
    if created:
        save_data(data)
        log.info(f"Автодобавление участника из группы: @{uname}")


# ═══════════════ АДМИН: УЧАСТНИКИ ═══════════════
@router.message(Command("add_participant"))
@admin_only
async def cmd_add_participant(msg: Message):
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /add_participant @username Имя")
        return

    uname = normalize_username(parts[1])
    name = parts[2].strip()

    if not uname:
        await msg.answer("⚠️ Укажи @username участника.")
        return

    data = load_data()
    if uname in data["participants"]:
        await msg.answer(f"⚠️ Участник @{uname} уже существует.")
        return

    data["participants"][uname] = {
        "name": name,
        "invite_link": "",
        "tg_id": None,
        "invites": [],
    }

    save_data(data)
    await msg.answer(f"✅ Участник <b>{name}</b> (@{uname}) добавлен!")


@router.message(Command("remove_participant"))
@admin_only
async def cmd_remove_participant(msg: Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /remove_participant @username")
        return

    uname = normalize_username(parts[1])
    data = load_data()

    if uname not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return

    name = data["participants"][uname]["name"]
    del data["participants"][uname]
    save_data(data)

    await msg.answer(f"🗑 Участник <b>{name}</b> (@{uname}) удалён.")


@router.message(Command("set_link"))
@admin_only
async def cmd_set_link(msg: Message):
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /set_link @username ссылка")
        return

    uname = normalize_username(parts[1])
    link = parts[2].strip()
    data = load_data()

    if uname not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return

    data["participants"][uname]["invite_link"] = link
    save_data(data)

    await msg.answer(
        f"🔗 Ссылка для <b>{data['participants'][uname]['name']}</b> (@{uname}) установлена."
    )


@router.message(Command("list"))
@admin_only
async def cmd_list(msg: Message):
    data = load_data()

    if not data["participants"]:
        await msg.answer("📭 Нет участников.")
        return

    text = "📋 <b>УЧАСТНИКИ КОНКУРСА</b>\n\n"
    items = sorted(
        data["participants"].items(),
        key=lambda x: (-count_valid_invites(x[1]), x[1].get("name", "").lower(), x[0]),
    )

    for uname, p in items:
        valid = count_valid_invites(p)
        link_status = "🔗 ссылка есть" if p.get("invite_link") else "❌ нет ссылки"
        text += (
            f"• <b>{p['name']}</b> (@{uname})\n"
            f"    Инвайтов: {valid} | {link_status}\n"
        )

    await msg.answer(text)


@router.message(Command("info"))
@admin_only
async def cmd_info(msg: Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /info @username")
        return

    uname = normalize_username(parts[1])
    data = load_data()

    if uname not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return

    p = data["participants"][uname]
    valid = count_valid_invites(p)
    text = (
        f"📄 <b>Участник:</b> {p['name']}\n"
        f"👤 <b>Username:</b> @{uname}\n"
        f"🆔 <b>TG ID:</b> {p.get('tg_id') or '—'}\n"
        f"🔗 <b>Ссылка:</b> {p.get('invite_link') or '—'}\n"
        f"📊 <b>Инвайтов:</b> {valid}\n\n"
        f"<b>Список инвайтов:</b>\n"
    )

    for i, inv in enumerate(p.get("invites", []), 1):
        status = "❌ УДАЛЁН" if inv.get("removed") else "✅ ок"
        text += f"\n  <b>{i}.</b> {inv['name']} — {status}\n      Добавлен: {inv.get('added_at', '?')}\n"

    if not p.get("invites"):
        text += "  Пока нет инвайтов.\n"

    await msg.answer(text)


# ═══════════════ АДМИН: ИНВАЙТЫ ═══════════════
@router.message(Command("add_invite"))
@admin_only
async def cmd_add_invite(msg: Message):
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /add_invite @username Имя_друга")
        return

    uname = normalize_username(parts[1])
    invite_name = parts[2].strip()
    data = load_data()

    if uname not in data["participants"]:
        await msg.answer("❌ Участник не найден. Сначала /add_participant")
        return

    invite_entry = {
        "name": invite_name,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "removed": False,
    }

    data["participants"][uname]["invites"].append(invite_entry)
    save_data(data)

    valid = count_valid_invites(data["participants"][uname])
    await msg.answer(
        f"✅ Инвайт <b>{invite_name}</b> добавлен для <b>{data['participants'][uname]['name']}</b> (@{uname})!\n"
        f"📊 Теперь инвайтов: <b>{valid}</b>"
    )

    tg_id = data["participants"][uname].get("tg_id")
    if tg_id:
        try:
            await bot.send_message(
                tg_id,
                f"🎉 Тебе засчитан новый инвайт: <b>{invite_name}</b>!\n📊 Всего инвайтов: <b>{valid}</b>",
            )
        except Exception:
            pass


@router.message(Command("remove_invite"))
@admin_only
async def cmd_remove_invite(msg: Message):
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /remove_invite @username номер")
        return

    uname = normalize_username(parts[1])
    try:
        idx = int(parts[2]) - 1
    except ValueError:
        await msg.answer("⚠️ Номер должен быть числом.")
        return

    data = load_data()
    if uname not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return

    invites = data["participants"][uname].get("invites", [])
    if idx < 0 or idx >= len(invites):
        await msg.answer(f"⚠️ Нет инвайта с номером {idx + 1}. Используй /info @{uname}")
        return

    if invites[idx].get("removed"):
        await msg.answer("⚠️ Этот инвайт уже удалён.")
        return

    invites[idx]["removed"] = True
    save_data(data)

    valid = count_valid_invites(data["participants"][uname])
    await msg.answer(
        f"🗑 Инвайт #{idx + 1} (<b>{invites[idx]['name']}</b>) удалён у "
        f"<b>{data['participants'][uname]['name']}</b> (@{uname}).\n"
        f"📊 Теперь инвайтов: <b>{valid}</b>"
    )

    tg_id = data["participants"][uname].get("tg_id")
    if tg_id:
        try:
            await bot.send_message(
                tg_id,
                f"⚠️ Инвайт <b>{invites[idx]['name']}</b> был аннулирован.\n📊 Теперь инвайтов: <b>{valid}</b>",
            )
        except Exception:
            pass


@router.message(Command("restore_invite"))
@admin_only
async def cmd_restore_invite(msg: Message):
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /restore_invite @username номер")
        return

    uname = normalize_username(parts[1])
    try:
        idx = int(parts[2]) - 1
    except ValueError:
        await msg.answer("⚠️ Номер должен быть числом.")
        return

    data = load_data()
    if uname not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return

    invites = data["participants"][uname].get("invites", [])
    if idx < 0 or idx >= len(invites):
        await msg.answer(f"⚠️ Нет инвайта с номером {idx + 1}.")
        return

    if not invites[idx].get("removed"):
        await msg.answer("⚠️ Этот инвайт и так активен.")
        return

    invites[idx]["removed"] = False
    save_data(data)

    valid = count_valid_invites(data["participants"][uname])
    await msg.answer(
        f"♻️ Инвайт #{idx + 1} (<b>{invites[idx]['name']}</b>) восстановлен!\n"
        f"📊 Теперь инвайтов: <b>{valid}</b>"
    )


# ═══════════════ АДМИН: УПРАВЛЕНИЕ АДМИНАМИ ═══════════════
@router.message(Command("add_admin"))
@owner_only
async def cmd_add_admin(msg: Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /add_admin <ID>\nПример: /add_admin 123456789")
        return

    try:
        new_admin_id = int(parts[1])
    except ValueError:
        await msg.answer("⚠️ Укажи числовой Telegram ID.\nПример: /add_admin 123456789")
        return

    data = load_data()
    if new_admin_id in data["admins"]:
        await msg.answer("⚠️ Уже админ.")
        return

    data["admins"].append(new_admin_id)
    save_data(data)

    await msg.answer(f"✅ Пользователь с ID <code>{new_admin_id}</code> теперь админ.")


@router.message(Command("remove_admin"))
@owner_only
async def cmd_remove_admin(msg: Message):
    parts = msg.text.split()
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

    data = load_data()
    if rm_admin_id not in data["admins"]:
        await msg.answer("❌ Не является админом.")
        return

    data["admins"].remove(rm_admin_id)
    save_data(data)

    await msg.answer(f"🗑 Пользователь с ID <code>{rm_admin_id}</code> больше не админ.")


@router.message(Command("admins"))
@admin_only
async def cmd_admins(msg: Message):
    data = load_data()
    text = "👑 <b>Админы бота:</b>\n\n"

    unique_admins = []
    seen = set()
    for admin_id in data["admins"]:
        if admin_id not in seen:
            seen.add(admin_id)
            unique_admins.append(admin_id)

    for admin_id in unique_admins:
        owner_mark = " 👑 (главный)" if admin_id == OWNER_ID else ""
        text += f"• <code>{admin_id}</code>{owner_mark}\n"

    await msg.answer(text)


# ═══════════════ АДМИН: ПРОЧЕЕ ═══════════════
@router.message(Command("myid"))
async def cmd_myid(msg: Message):
    uid = msg.from_user.id if msg.from_user else None
    await msg.answer(f"🆔 Твой Telegram ID: <code>{uid}</code>")


@router.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(msg: Message):
    text_to_send = msg.text.partition(" ")[2].strip() if msg.text else ""
    if not text_to_send:
        await msg.answer("⚠️ Формат: /broadcast текст")
        return

    data = load_data()
    sent = 0
    failed = 0
    no_id = 0

    for _, p in data["participants"].items():
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
    data = load_data()

    if not data["participants"]:
        await msg.answer("📭 Нет данных.")
        return

    lines = ["ТАБЛИЦА КОНКУРСА ИНВАЙТОВ", "=" * 40, ""]
    board = []

    for uname, p in data["participants"].items():
        valid = count_valid_invites(p)
        board.append((valid, uname, p))

    board.sort(key=lambda x: (-x[0], x[2].get("name", "").lower(), x[1]))

    for rank, (count, uname, p) in enumerate(board, 1):
        lines.append(f"#{rank} — {p['name']} (@{uname})")
        lines.append(f"     Инвайтов: {count}")
        lines.append(f"     TG ID: {p.get('tg_id') or '—'}")
        lines.append(f"     Ссылка: {p.get('invite_link') or '—'}")
        for i, inv in enumerate(p.get("invites", []), 1):
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
    parts = msg.text.split()
    if len(parts) < 2 or parts[1] != "ПОДТВЕРЖДАЮ":
        await msg.answer("⚠️ Это удалит ВСЕ данные!\nДля подтверждения: /reset_all ПОДТВЕРЖДАЮ")
        return

    data = default_data()
    save_data(data)
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
    data = load_data()
    board = []

    for uname, p in data["participants"].items():
        valid = count_valid_invites(p)
        board.append((p["name"], valid, uname))

    board.sort(key=lambda x: (-x[1], x[0].lower(), x[2]))

    rows = ""
    for i, (name, count, uname) in enumerate(board, 1):
        rows += f"<tr><td>{i}</td><td>{name}</td><td>@{uname}</td><td>{count}</td></tr>"

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
                    else:
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
    if not DATA_PATH.exists():
        save_data(default_data())
    else:
        save_data(load_data())  # нормализация структуры

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
