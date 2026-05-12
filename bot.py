import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

from aiohttp import web, ClientSession
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BufferedInputFile
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ──────────────────── КОНФИГ ────────────────────
CONFIG_PATH = Path(__file__).parent / "config.json"
DATA_PATH = Path(__file__).parent / "data.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

BOT_TOKEN: str = config["BOT_TOKEN"]
OWNER_USERNAME: str = config["OWNER_USERNAME"].lower().lstrip("@")  # главный админ
PING_URL: str = config.get("PING_URL", "")
PING_INTERVAL: int = 300
WEB_PORT: int = int(os.environ.get("PORT", 10000))

# ──────────────────── ЛОГИРОВАНИЕ ────────────────────
logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("invite_bot")

# ──────────────────── ДАННЫЕ ────────────────────
# Структура data.json:
# {
#   "admins": ["owner_username", "admin2"],     — список юзернеймов (без @, lowercase)
#   "participants": {
#       "username": {                            — ключ = юзернейм (lowercase, без @)
#           "name": "Отображаемое Имя",
#           "invite_link": "https://t.me/...",
#           "tg_id": 123456,                     — заполняется автоматически при /mystats
#           "invites": [
#               {
#                   "name": "Друг",
#                   "added_at": "2025-01-01 12:00",
#                   "removed": false
#               }
#           ]
#       }
#   }
# }

def load_data() -> dict:
    if DATA_PATH.exists():
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"admins": [OWNER_USERNAME], "participants": {}}


def save_data(data: dict):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_username(raw: str) -> str:
    """Убирает @ и приводит к lowercase."""
    return raw.lower().lstrip("@").strip()


def get_caller_username(msg: Message) -> str | None:
    """Получает username отправителя сообщения."""
    if msg.from_user and msg.from_user.username:
        return msg.from_user.username.lower()
    return None


def is_admin(msg: Message, data: dict) -> bool:
    uname = get_caller_username(msg)
    if not uname:
        return False
    return uname == OWNER_USERNAME or uname in data.get("admins", [])


def is_owner(msg: Message) -> bool:
    uname = get_caller_username(msg)
    return uname is not None and uname == OWNER_USERNAME


def count_valid_invites(participant: dict) -> int:
    return sum(1 for inv in participant.get("invites", []) if not inv.get("removed", False))


# ──────────────────── БОТ ────────────────────
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)


def require_username(func):
    """Декоратор: требует наличие username у отправителя."""
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
    """Декоратор: только для админов."""
    async def wrapper(msg: Message, *args, **kwargs):
        if not msg.from_user or not msg.from_user.username:
            await msg.answer("⚠️ У тебя нет username. Установи его в настройках Telegram.")
            return
        data = load_data()
        if not is_admin(msg, data):
            await msg.answer("⛔ У тебя нет доступа к этой команде.")
            return
        return await func(msg, *args, **kwargs)
    return wrapper


def owner_only(func):
    """Декоратор: только для главного админа."""
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
    # Автопривязка tg_id к участнику
    uname = get_caller_username(msg)
    if uname and uname in data["participants"]:
        data["participants"][uname]["tg_id"] = msg.from_user.id
        save_data(data)

    if is_admin(msg, data):
        await msg.answer(
            "👋 <b>Привет, админ!</b>\n\n"
            "Используй /help чтобы увидеть все команды."
        )
    else:
        await msg.answer(
            "👋 <b>Привет!</b>\n\n"
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
        "/start — приветствие\n"
        "/help — список команд\n"
        "/mystats — мои инвайты (если участвую)\n"
        "/top — таблица лидеров\n"
    )
    if is_admin(msg, data):
        text += (
            "\n🔧 <b>Команды для админов:</b>\n\n"
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
            "/add_admin @user — назначить\n"
            "/remove_admin @user — снять\n"
            "/admins — список\n\n"
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
        await msg.answer("❌ Ты не зарегистрирован как участник конкурса.\nОбратись к админу.")
        return

    # Автопривязка tg_id
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
    board.sort(key=lambda x: x[1], reverse=True)

    medals = ["🥇", "🥈", "🥉"]
    text = "🏆 <b>ТАБЛИЦА ЛИДЕРОВ</b>\n\n"
    for i, (name, count, uname) in enumerate(board):
        medal = medals[i] if i < 3 else f"  {i+1}."
        text += f"{medal} <b>{name}</b> (@{uname}) — {count} инвайтов\n"
    await msg.answer(text)


# ═══════════════ АДМИН: УЧАСТНИКИ ═══════════════

@router.message(Command("add_participant"))
@admin_only
async def cmd_add_participant(msg: Message):
    """/add_participant @username Имя"""
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /add_participant @username Имя")
        return
    uname = normalize_username(parts[1])
    name = parts[2]
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
        "invites": []
    }
    save_data(data)
    await msg.answer(f"✅ Участник <b>{name}</b> (@{uname}) добавлен!")


@router.message(Command("remove_participant"))
@admin_only
async def cmd_remove_participant(msg: Message):
    """/remove_participant @username"""
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
    """/set_link @username ссылка"""
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /set_link @username ссылка")
        return
    uname = normalize_username(parts[1])
    link = parts[2]
    data = load_data()
    if uname not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return
    data["participants"][uname]["invite_link"] = link
    save_data(data)
    await msg.answer(f"🔗 Ссылка для <b>{data['participants'][uname]['name']}</b> (@{uname}) установлена.")


@router.message(Command("list"))
@admin_only
async def cmd_list(msg: Message):
    data = load_data()
    if not data["participants"]:
        await msg.answer("📭 Нет участников.")
        return
    text = "📋 <b>УЧАСТНИКИ КОНКУРСА</b>\n\n"
    # Сортируем по инвайтам
    items = sorted(data["participants"].items(),
                   key=lambda x: count_valid_invites(x[1]), reverse=True)
    for uname, p in items:
        valid = count_valid_invites(p)
        link_status = "🔗" if p.get("invite_link") else "❌ нет ссылки"
        text += (
            f"• <b>{p['name']}</b> (@{uname})\n"
            f"    Инвайтов: {valid} | {link_status}\n"
        )
    await msg.answer(text)


@router.message(Command("info"))
@admin_only
async def cmd_info(msg: Message):
    """/info @username"""
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
        f"🔗 <b>Ссылка:</b> {p.get('invite_link') or '—'}\n"
        f"📊 <b>Инвайтов:</b> {valid}\n\n"
        f"<b>Список инвайтов:</b>\n"
    )
    for i, inv in enumerate(p.get("invites", []), 1):
        status = "❌ УДАЛЁН" if inv.get("removed") else "✅ ок"
        text += (
            f"\n  <b>{i}.</b> {inv['name']} — {status}\n"
            f"      Добавлен: {inv.get('added_at', '?')}\n"
        )
    if not p.get("invites"):
        text += "  Пока нет инвайтов.\n"
    await msg.answer(text)


# ═══════════════ АДМИН: ИНВАЙТЫ ═══════════════

@router.message(Command("add_invite"))
@admin_only
async def cmd_add_invite(msg: Message):
    """/add_invite @username Имя_приглашённого"""
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /add_invite @username Имя_друга")
        return
    uname = normalize_username(parts[1])
    invite_name = parts[2]
    data = load_data()
    if uname not in data["participants"]:
        await msg.answer("❌ Участник не найден. Сначала /add_participant")
        return

    invite_entry = {
        "name": invite_name,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "removed": False
    }
    data["participants"][uname]["invites"].append(invite_entry)
    save_data(data)

    valid = count_valid_invites(data["participants"][uname])
    await msg.answer(
        f"✅ Инвайт <b>{invite_name}</b> добавлен для <b>{data['participants'][uname]['name']}</b> (@{uname})!\n"
        f"📊 Теперь инвайтов: <b>{valid}</b>"
    )

    # Уведомляем участника если знаем его tg_id
    tg_id = data["participants"][uname].get("tg_id")
    if tg_id:
        try:
            await bot.send_message(
                tg_id,
                f"🎉 Тебе засчитан новый инвайт: <b>{invite_name}</b>!\n"
                f"📊 Всего инвайтов: <b>{valid}</b>"
            )
        except Exception:
            pass


@router.message(Command("remove_invite"))
@admin_only
async def cmd_remove_invite(msg: Message):
    """/remove_invite @username номер"""
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
                f"⚠️ Инвайт <b>{invites[idx]['name']}</b> был аннулирован.\n"
                f"📊 Теперь инвайтов: <b>{valid}</b>"
            )
        except Exception:
            pass


@router.message(Command("restore_invite"))
@admin_only
async def cmd_restore_invite(msg: Message):
    """/restore_invite @username номер"""
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
        await msg.answer("⚠️ Формат: /add_admin @username")
        return
    new_admin = normalize_username(parts[1])
    data = load_data()
    if new_admin in data["admins"]:
        await msg.answer("⚠️ Уже админ.")
        return
    data["admins"].append(new_admin)
    save_data(data)
    await msg.answer(f"✅ @{new_admin} теперь админ.")


@router.message(Command("remove_admin"))
@owner_only
async def cmd_remove_admin(msg: Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /remove_admin @username")
        return
    rm_admin = normalize_username(parts[1])
    if rm_admin == OWNER_USERNAME:
        await msg.answer("⚠️ Нельзя снять самого себя.")
        return
    data = load_data()
    if rm_admin not in data["admins"]:
        await msg.answer("❌ Не является админом.")
        return
    data["admins"].remove(rm_admin)
    save_data(data)
    await msg.answer(f"🗑 @{rm_admin} больше не админ.")


@router.message(Command("admins"))
@admin_only
async def cmd_admins(msg: Message):
    data = load_data()
    text = "👑 <b>Админы бота:</b>\n\n"
    for a in data["admins"]:
        owner_mark = " 👑 (главный)" if a == OWNER_USERNAME else ""
        text += f"• @{a}{owner_mark}\n"
    await msg.answer(text)


# ═══════════════ АДМИН: ПРОЧЕЕ ═══════════════

@router.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(msg: Message):
    text_to_send = msg.text.partition(" ")[2]
    if not text_to_send:
        await msg.answer("⚠️ Формат: /broadcast текст")
        return
    data = load_data()
    sent = 0
    failed = 0
    no_id = 0
    for uname, p in data["participants"].items():
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
        result += f"\n⚠️ {no_id} участников ещё не написали боту /start (нет tg_id)"
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
    board.sort(key=lambda x: x[0], reverse=True)

    for rank, (count, uname, p) in enumerate(board, 1):
        lines.append(f"#{rank} — {p['name']} (@{uname})")
        lines.append(f"     Инвайтов: {count}")
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
        await msg.answer(
            "⚠️ Это удалит ВСЕ данные!\n"
            "Для подтверждения: /reset_all ПОДТВЕРЖДАЮ"
        )
        return
    data = {"admins": [OWNER_USERNAME], "participants": {}}
    save_data(data)
    await msg.answer("💥 Все данные сброшены.")


# ──────────────────── ANTI-SLEEP ────────────────────

async def self_ping():
    if not PING_URL:
        log.warning("PING_URL пуст — anti-sleep отключен. Заполни config.json после деплоя.")
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
    board.sort(key=lambda x: x[1], reverse=True)

    rows = ""
    for i, (name, count, uname) in enumerate(board, 1):
        rows += f"<tr><td>{i}</td><td>{name}</td><td>@{uname}</td><td>{count}</td></tr>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Invite Contest</title>
<style>
body {{ font-family: sans-serif; max-width: 700px; margin: 40px auto; background: #1a1a2e; color: #eee; }}
h1 {{ text-align: center; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px; border: 1px solid #444; text-align: center; }}
th {{ background: #16213e; }}
tr:nth-child(even) {{ background: #0f3460; }}
</style></head><body>
<h1>🏆 Таблица Инвайтов</h1>
<table><tr><th>#</th><th>Участник</th><th>Username</th><th>Инвайты</th></tr>{rows}</table>
<p style="text-align:center;margin-top:20px;color:#666;">Обновление по F5</p>
</body></html>"""
    return web.Response(text=html, content_type="text/html")


# ──────────────────── ЗАПУСК ────────────────────

async def on_startup():
    """Сброс webhook и pending updates при старте — решает Conflict ошибку."""
    log.info("Сбрасываем webhook и pending updates...")
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Webhook сброшен, pending updates очищены.")


async def main():
    # Инициализируем data.json если нет
    if not DATA_PATH.exists():
        save_data({"admins": [OWNER_USERNAME], "participants": {}})

    # Сбрасываем конфликт ПЕРЕД стартом polling
    await on_startup()

    # Запускаем web-сервер
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/stats", handle_stats)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    log.info(f"Web server started on port {WEB_PORT}")

    # Запускаем self-ping
    asyncio.create_task(self_ping())

    # Запускаем бота (с retry=False чтобы при конфликте не зацикливался,
    # а сразу падал и Render перезапускал)
    log.info("Bot starting polling...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        log.info("Bot stopping...")
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
