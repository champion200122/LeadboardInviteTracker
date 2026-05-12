"""
Telegram-бот для учёта конкурса инвайтов.
Хранение: JSON-файл (data.json)
Фреймворк: aiogram 3.x
Anti-sleep: встроенный self-ping через aiohttp
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiohttp import web, ClientSession
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ──────────────────── КОНФИГ ────────────────────
CONFIG_PATH = Path(__file__).parent / "config.json"
DATA_PATH = Path(__file__).parent / "data.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

BOT_TOKEN: str = config["BOT_TOKEN"]
OWNER_ID: int = config["OWNER_ID"]            # главный админ (Telegram user id)
PING_URL: str = config.get("PING_URL", "")     # URL самого себя для anti-sleep (заполнишь после деплоя)
PING_INTERVAL: int = 300                       # секунды между пингами (5 мин)
WEB_PORT: int = int(os.environ.get("PORT", 10000))  # Render даёт PORT

# ──────────────────── ЛОГИРОВАНИЕ ────────────────────
logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("invite_bot")

# ──────────────────── ДАННЫЕ ────────────────────
# Структура data.json:
# {
#   "admins": [123456789],            — список Telegram ID админов
#   "participants": {                  — участники конкурса
#       "<tg_id>": {
#           "name": "Имя",
#           "username": "@nick",
#           "invite_link": "https://t.me/...",
#           "invites": [               — список засчитанных инвайтов
#               {
#                   "name": "Друг",
#                   "in_chat": true,
#                   "in_club": true,
#                   "is_active": true,
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
    return {"admins": [OWNER_ID], "participants": {}}


def save_data(data: dict):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_admin(user_id: int, data: dict) -> bool:
    return user_id == OWNER_ID or user_id in data.get("admins", [])


def count_valid_invites(participant: dict) -> int:
    """Считает только активные (не удалённые) инвайты."""
    return sum(1 for inv in participant.get("invites", []) if not inv.get("removed", False))


# ──────────────────── БОТ ────────────────────
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)


# ═══════════════ ОБЩИЕ КОМАНДЫ ═══════════════

@router.message(CommandStart())
async def cmd_start(msg: Message):
    data = load_data()
    if is_admin(msg.from_user.id, data):
        await msg.answer(
            "👋 <b>Привет, админ!</b>\n\n"
            "Используй /help чтобы увидеть все команды."
        )
    else:
        await msg.answer(
            "👋 <b>Привет!</b>\n\n"
            "Этот бот ведёт учёт инвайтов для конкурса.\n"
            "Используй /mystats чтобы посмотреть свои инвайты.\n"
            "Используй /top чтобы увидеть таблицу лидеров."
        )


@router.message(Command("help"))
async def cmd_help(msg: Message):
    data = load_data()
    text = (
        "📖 <b>Команды для всех:</b>\n"
        "/start — приветствие\n"
        "/help — список команд\n"
        "/mystats — мои инвайты (если участвую)\n"
        "/top — таблица лидеров\n"
        "/myid — узнать свой Telegram ID\n"
    )
    if is_admin(msg.from_user.id, data):
        text += (
            "\n🔧 <b>Команды для админов:</b>\n\n"
            "<b>Управление участниками:</b>\n"
            "/add_participant &lt;tg_id&gt; &lt;Имя&gt; — добавить участника\n"
            "/remove_participant &lt;tg_id&gt; — удалить участника\n"
            "/set_link &lt;tg_id&gt; &lt;ссылка&gt; — задать инвайт-ссылку\n"
            "/list — список всех участников\n"
            "/info &lt;tg_id&gt; — подробная инфа об участнике\n\n"
            "<b>Управление инвайтами:</b>\n"
            "/add_invite &lt;tg_id&gt; &lt;Имя приглашённого&gt; — +1 инвайт\n"
            "/remove_invite &lt;tg_id&gt; &lt;номер&gt; — убрать инвайт (минус балл)\n"
            "/restore_invite &lt;tg_id&gt; &lt;номер&gt; — восстановить инвайт\n\n"
            "<b>Управление админами:</b>\n"
            "/add_admin &lt;tg_id&gt; — добавить админа\n"
            "/remove_admin &lt;tg_id&gt; — снять админа\n"
            "/admins — список админов\n\n"
            "<b>Прочее:</b>\n"
            "/broadcast &lt;текст&gt; — отправить всем участникам\n"
            "/reset_all — ⚠️ сбросить ВСЕ данные\n"
            "/export — выгрузить таблицу текстом\n"
        )
    await msg.answer(text)


@router.message(Command("myid"))
async def cmd_myid(msg: Message):
    await msg.answer(f"🆔 Твой Telegram ID: <code>{msg.from_user.id}</code>")


# ═══════════════ ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ═══════════════

@router.message(Command("mystats"))
async def cmd_mystats(msg: Message):
    data = load_data()
    uid = str(msg.from_user.id)
    if uid not in data["participants"]:
        await msg.answer("❌ Ты не зарегистрирован как участник конкурса.\nОбратись к админу.")
        return
    p = data["participants"][uid]
    valid = count_valid_invites(p)
    text = f"📊 <b>{p['name']}</b> — инвайтов: <b>{valid}</b>\n\n"
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
    for uid, p in data["participants"].items():
        valid = count_valid_invites(p)
        board.append((p["name"], valid, p.get("username", "")))
    board.sort(key=lambda x: x[1], reverse=True)

    medals = ["🥇", "🥈", "🥉"]
    text = "🏆 <b>ТАБЛИЦА ЛИДЕРОВ</b>\n\n"
    for i, (name, count, username) in enumerate(board):
        medal = medals[i] if i < 3 else f"  {i+1}."
        uname = f" ({username})" if username else ""
        text += f"{medal} <b>{name}</b>{uname} — {count} инвайтов\n"
    await msg.answer(text)


# ═══════════════ АДМИН: УЧАСТНИКИ ═══════════════

def admin_only(func):
    """Декоратор: только для админов."""
    async def wrapper(msg: Message, *args, **kwargs):
        data = load_data()
        if not is_admin(msg.from_user.id, data):
            await msg.answer("⛔ У тебя нет доступа к этой команде.")
            return
        return await func(msg, *args, **kwargs)
    return wrapper


@router.message(Command("add_participant"))
@admin_only
async def cmd_add_participant(msg: Message):
    """Добавить участника: /add_participant <tg_id> <Имя>"""
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /add_participant &lt;tg_id&gt; &lt;Имя&gt;")
        return
    tg_id = parts[1]
    name = parts[2]
    if not tg_id.isdigit():
        await msg.answer("⚠️ tg_id должен быть числом. Попроси участника написать боту /myid")
        return

    data = load_data()
    if tg_id in data["participants"]:
        await msg.answer(f"⚠️ Участник {tg_id} уже существует.")
        return

    data["participants"][tg_id] = {
        "name": name,
        "username": "",
        "invite_link": "",
        "invites": []
    }
    save_data(data)
    await msg.answer(f"✅ Участник <b>{name}</b> (ID: <code>{tg_id}</code>) добавлен!")


@router.message(Command("remove_participant"))
@admin_only
async def cmd_remove_participant(msg: Message):
    """Удалить участника: /remove_participant <tg_id>"""
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /remove_participant &lt;tg_id&gt;")
        return
    tg_id = parts[1]
    data = load_data()
    if tg_id not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return
    name = data["participants"][tg_id]["name"]
    del data["participants"][tg_id]
    save_data(data)
    await msg.answer(f"🗑 Участник <b>{name}</b> удалён.")


@router.message(Command("set_link"))
@admin_only
async def cmd_set_link(msg: Message):
    """Задать ссылку: /set_link <tg_id> <ссылка>"""
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /set_link &lt;tg_id&gt; &lt;ссылка&gt;")
        return
    tg_id = parts[1]
    link = parts[2]
    data = load_data()
    if tg_id not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return
    data["participants"][tg_id]["invite_link"] = link
    save_data(data)
    await msg.answer(f"🔗 Ссылка для <b>{data['participants'][tg_id]['name']}</b> установлена.")


@router.message(Command("list"))
@admin_only
async def cmd_list(msg: Message):
    """Показать всех участников."""
    data = load_data()
    if not data["participants"]:
        await msg.answer("📭 Нет участников.")
        return
    text = "📋 <b>УЧАСТНИКИ КОНКУРСА</b>\n\n"
    for uid, p in data["participants"].items():
        valid = count_valid_invites(p)
        link_status = "🔗" if p.get("invite_link") else "❌ нет ссылки"
        text += (
            f"• <b>{p['name']}</b> (ID: <code>{uid}</code>)\n"
            f"    Инвайтов: {valid} | {link_status}\n"
        )
    await msg.answer(text)


@router.message(Command("info"))
@admin_only
async def cmd_info(msg: Message):
    """Подробная инфа: /info <tg_id>"""
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /info &lt;tg_id&gt;")
        return
    tg_id = parts[1]
    data = load_data()
    if tg_id not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return
    p = data["participants"][tg_id]
    valid = count_valid_invites(p)
    text = (
        f"📄 <b>Участник:</b> {p['name']}\n"
        f"🆔 <b>ID:</b> <code>{tg_id}</code>\n"
        f"👤 <b>Username:</b> {p.get('username') or '—'}\n"
        f"🔗 <b>Ссылка:</b> {p.get('invite_link') or '—'}\n"
        f"📊 <b>Инвайтов:</b> {valid}\n\n"
        f"<b>Список инвайтов:</b>\n"
    )
    for i, inv in enumerate(p.get("invites", []), 1):
        status = "❌ УДАЛЁН" if inv.get("removed") else "✅ ок"
        text += (
            f"\n  <b>{i}.</b> {inv['name']} — {status}\n"
            f"      В чате: {'✅' if inv.get('in_chat') else '❌'} | "
            f"В клубе: {'✅' if inv.get('in_club') else '❌'} | "
            f"Активен: {'✅' if inv.get('is_active') else '❌'}\n"
            f"      Добавлен: {inv.get('added_at', '?')}\n"
        )
    if not p.get("invites"):
        text += "  Пока нет инвайтов.\n"
    await msg.answer(text)


# ═══════════════ АДМИН: ИНВАЙТЫ ═══════════════

@router.message(Command("add_invite"))
@admin_only
async def cmd_add_invite(msg: Message):
    """/add_invite <tg_id> <Имя приглашённого>"""
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /add_invite &lt;tg_id&gt; &lt;Имя приглашённого&gt;")
        return
    tg_id = parts[1]
    invite_name = parts[2]
    data = load_data()
    if tg_id not in data["participants"]:
        await msg.answer("❌ Участник не найден. Сначала /add_participant")
        return

    invite_entry = {
        "name": invite_name,
        "in_chat": True,
        "in_club": True,
        "is_active": True,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "removed": False
    }
    data["participants"][tg_id]["invites"].append(invite_entry)
    save_data(data)

    valid = count_valid_invites(data["participants"][tg_id])
    await msg.answer(
        f"✅ Инвайт <b>{invite_name}</b> добавлен для <b>{data['participants'][tg_id]['name']}</b>!\n"
        f"📊 Теперь инвайтов: <b>{valid}</b>"
    )

    # Уведомляем участника
    try:
        await bot.send_message(
            int(tg_id),
            f"🎉 Тебе засчитан новый инвайт: <b>{invite_name}</b>!\n"
            f"📊 Всего инвайтов: <b>{valid}</b>"
        )
    except Exception:
        pass  # Если участник не начал чат с ботом


@router.message(Command("remove_invite"))
@admin_only
async def cmd_remove_invite(msg: Message):
    """/remove_invite <tg_id> <номер> — минус балл (помечает удалённым)"""
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /remove_invite &lt;tg_id&gt; &lt;номер инвайта&gt;")
        return
    tg_id = parts[1]
    try:
        idx = int(parts[2]) - 1
    except ValueError:
        await msg.answer("⚠️ Номер должен быть числом.")
        return

    data = load_data()
    if tg_id not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return
    invites = data["participants"][tg_id].get("invites", [])
    if idx < 0 or idx >= len(invites):
        await msg.answer(f"⚠️ Нет инвайта с номером {idx + 1}. Используй /info {tg_id}")
        return
    if invites[idx].get("removed"):
        await msg.answer("⚠️ Этот инвайт уже удалён.")
        return

    invites[idx]["removed"] = True
    save_data(data)

    valid = count_valid_invites(data["participants"][tg_id])
    await msg.answer(
        f"🗑 Инвайт #{idx + 1} (<b>{invites[idx]['name']}</b>) удалён у "
        f"<b>{data['participants'][tg_id]['name']}</b>.\n"
        f"📊 Теперь инвайтов: <b>{valid}</b>"
    )

    # Уведомляем участника
    try:
        await bot.send_message(
            int(tg_id),
            f"⚠️ Инвайт <b>{invites[idx]['name']}</b> был аннулирован.\n"
            f"📊 Теперь инвайтов: <b>{valid}</b>"
        )
    except Exception:
        pass


@router.message(Command("restore_invite"))
@admin_only
async def cmd_restore_invite(msg: Message):
    """/restore_invite <tg_id> <номер> — восстановить удалённый инвайт"""
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("⚠️ Формат: /restore_invite &lt;tg_id&gt; &lt;номер&gt;")
        return
    tg_id = parts[1]
    try:
        idx = int(parts[2]) - 1
    except ValueError:
        await msg.answer("⚠️ Номер должен быть числом.")
        return

    data = load_data()
    if tg_id not in data["participants"]:
        await msg.answer("❌ Участник не найден.")
        return
    invites = data["participants"][tg_id].get("invites", [])
    if idx < 0 or idx >= len(invites):
        await msg.answer(f"⚠️ Нет инвайта с номером {idx + 1}.")
        return
    if not invites[idx].get("removed"):
        await msg.answer("⚠️ Этот инвайт и так активен.")
        return

    invites[idx]["removed"] = False
    save_data(data)

    valid = count_valid_invites(data["participants"][tg_id])
    await msg.answer(
        f"♻️ Инвайт #{idx + 1} (<b>{invites[idx]['name']}</b>) восстановлен!\n"
        f"📊 Теперь инвайтов: <b>{valid}</b>"
    )


# ═══════════════ АДМИН: УПРАВЛЕНИЕ АДМИНАМИ ═══════════════

@router.message(Command("add_admin"))
async def cmd_add_admin(msg: Message):
    """Только OWNER может добавлять админов."""
    if msg.from_user.id != OWNER_ID:
        await msg.answer("⛔ Только главный админ может назначать других админов.")
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /add_admin &lt;tg_id&gt;")
        return
    try:
        new_id = int(parts[1])
    except ValueError:
        await msg.answer("⚠️ ID должен быть числом.")
        return

    data = load_data()
    if new_id in data["admins"]:
        await msg.answer("⚠️ Уже админ.")
        return
    data["admins"].append(new_id)
    save_data(data)
    await msg.answer(f"✅ Пользователь <code>{new_id}</code> теперь админ.")


@router.message(Command("remove_admin"))
async def cmd_remove_admin(msg: Message):
    if msg.from_user.id != OWNER_ID:
        await msg.answer("⛔ Только главный админ.")
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("⚠️ Формат: /remove_admin &lt;tg_id&gt;")
        return
    try:
        rm_id = int(parts[1])
    except ValueError:
        await msg.answer("⚠️ ID должен быть числом.")
        return
    if rm_id == OWNER_ID:
        await msg.answer("⚠️ Нельзя снять самого себя.")
        return
    data = load_data()
    if rm_id not in data["admins"]:
        await msg.answer("❌ Не является админом.")
        return
    data["admins"].remove(rm_id)
    save_data(data)
    await msg.answer(f"🗑 Админ <code>{rm_id}</code> снят.")


@router.message(Command("admins"))
@admin_only
async def cmd_admins(msg: Message):
    data = load_data()
    text = "👑 <b>Админы бота:</b>\n\n"
    for aid in data["admins"]:
        owner_mark = " 👑 (главный)" if aid == OWNER_ID else ""
        text += f"• <code>{aid}</code>{owner_mark}\n"
    await msg.answer(text)


# ═══════════════ АДМИН: ПРОЧЕЕ ═══════════════

@router.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(msg: Message):
    """/broadcast <текст> — рассылка всем участникам."""
    text_to_send = msg.text.partition(" ")[2]
    if not text_to_send:
        await msg.answer("⚠️ Формат: /broadcast &lt;текст&gt;")
        return
    data = load_data()
    sent = 0
    failed = 0
    for uid in data["participants"]:
        try:
            await bot.send_message(int(uid), f"📢 <b>Объявление:</b>\n\n{text_to_send}")
            sent += 1
        except Exception:
            failed += 1
    await msg.answer(f"📤 Отправлено: {sent} | Не доставлено: {failed}")


@router.message(Command("export"))
@admin_only
async def cmd_export(msg: Message):
    """Выгрузить таблицу текстом."""
    data = load_data()
    if not data["participants"]:
        await msg.answer("📭 Нет данных.")
        return

    lines = ["ТАБЛИЦА КОНКУРСА ИНВАЙТОВ", "=" * 40, ""]
    board = []
    for uid, p in data["participants"].items():
        valid = count_valid_invites(p)
        board.append((valid, uid, p))
    board.sort(key=lambda x: x[0], reverse=True)

    for rank, (count, uid, p) in enumerate(board, 1):
        lines.append(f"#{rank} — {p['name']} (ID: {uid})")
        lines.append(f"     Инвайтов: {count}")
        lines.append(f"     Ссылка: {p.get('invite_link') or '—'}")
        for i, inv in enumerate(p.get("invites", []), 1):
            status = "[УДАЛЁН]" if inv.get("removed") else "[OK]"
            lines.append(f"       {i}. {inv['name']} {status} (добавлен {inv.get('added_at', '?')})")
        lines.append("")

    result = "\n".join(lines)
    # Если текст длинный — отправляем файлом
    if len(result) > 4000:
        from aiogram.types import BufferedInputFile
        file = BufferedInputFile(result.encode("utf-8"), filename="export.txt")
        await msg.answer_document(file, caption="📊 Полная выгрузка")
    else:
        await msg.answer(f"<pre>{result}</pre>")


@router.message(Command("reset_all"))
async def cmd_reset_all(msg: Message):
    """Полный сброс — только OWNER."""
    if msg.from_user.id != OWNER_ID:
        await msg.answer("⛔ Только главный админ может сбрасывать данные.")
        return
    parts = msg.text.split()
    if len(parts) < 2 or parts[1] != "ПОДТВЕРЖДАЮ":
        await msg.answer(
            "⚠️ Это удалит ВСЕ данные!\n"
            "Для подтверждения напиши: /reset_all ПОДТВЕРЖДАЮ"
        )
        return
    data = {"admins": [OWNER_ID], "participants": {}}
    save_data(data)
    await msg.answer("💥 Все данные сброшены.")


# ──────────────────── ANTI-SLEEP (self-ping) ────────────────────

async def self_ping():
    """Каждые PING_INTERVAL секунд пингуем свой URL, чтобы Render не усыпил."""
    if not PING_URL:
        log.warning("PING_URL пуст — anti-sleep отключен. Заполни config.json после деплоя.")
        return
    await asyncio.sleep(30)  # ждём пока web-сервер поднимется
    async with ClientSession() as session:
        while True:
            try:
                async with session.get(PING_URL) as resp:
                    log.info(f"Self-ping → {resp.status}")
            except Exception as e:
                log.warning(f"Self-ping error: {e}")
            await asyncio.sleep(PING_INTERVAL)


# ──────────────────── WEB-СЕРВЕР (для Render health-check и self-ping) ────────────────────

async def handle_health(request):
    return web.Response(text="OK")


async def handle_stats(request):
    """Простая веб-страница со статистикой."""
    data = load_data()
    board = []
    for uid, p in data["participants"].items():
        valid = count_valid_invites(p)
        board.append((p["name"], valid))
    board.sort(key=lambda x: x[1], reverse=True)

    rows = ""
    for i, (name, count) in enumerate(board, 1):
        rows += f"<tr><td>{i}</td><td>{name}</td><td>{count}</td></tr>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Invite Contest</title>
<style>
body {{ font-family: sans-serif; max-width: 600px; margin: 40px auto; background: #1a1a2e; color: #eee; }}
h1 {{ text-align: center; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px; border: 1px solid #444; text-align: center; }}
th {{ background: #16213e; }}
tr:nth-child(even) {{ background: #0f3460; }}
</style></head><body>
<h1>🏆 Таблица Инвайтов</h1>
<table><tr><th>#</th><th>Участник</th><th>Инвайты</th></tr>{rows}</table>
<p style="text-align:center;margin-top:20px;color:#666;">Обновление по F5</p>
</body></html>"""
    return web.Response(text=html, content_type="text/html")


# ──────────────────── ЗАПУСК ────────────────────

async def main():
    # Инициализируем data.json если нет
    if not DATA_PATH.exists():
        save_data({"admins": [OWNER_ID], "participants": {}})

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

    # Запускаем бота
    log.info("Bot starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
