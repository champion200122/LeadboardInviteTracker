import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
import aiosqlite
import csv

# ==================== НАСТРОЙКИ ====================
TOKEN = os.getenv("8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg")          # в Render: переменная BOT_TOKEN
ADMIN_IDS = [827744412]                 # ← ВСТАВЬ СВОИ ID админов!

DB_NAME = "contest.db"

# ==================== ИНИЦИАЛИЗАЦИЯ БД ====================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                invites INTEGER DEFAULT 0,
                ref_link TEXT UNIQUE,
                banned INTEGER DEFAULT 0
            )
        ''')
        await db.commit()

# ==================== БОТ ====================
bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher()

# Генерация ссылки (админ даёт вручную — но бот может создать шаблон)
def make_link(user_id: int) -> str:
    return f"https://t.me/{(bot.username).replace('@','')}?start=ref_{user_id}"

# ==================== КОМАНДЫ ====================

@dp.message(CommandStart())
async def start_cmd(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT invites FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            # если новый — записываем с 0 инвайтами
            await db.execute("INSERT INTO users (user_id, username) VALUES (?, ?)",
                             (user_id, message.from_user.username or "no_name"))
            await db.commit()
            invites = 0
        else:
            invites = row[0]
    await message.answer(
        f"🔥 Конкурс инвайтов!\nТвоих инвайтов: <b>{invites}</b>\nЖди ссылку от админа 👀"
    )

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT invites FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await message.answer("Ты ещё не участвуешь. /start")
            return
        invites = row[0]

        # Топ-10
        async with db.execute("SELECT username, invites FROM users WHERE banned = 0 ORDER BY invites DESC LIMIT 10") as cur:
            top = await cur.fetchall()
        top_text = "\n".join([f"{i+1}. @{u[0] or 'NoName'} — {u[1]}" for i, u in enumerate(top)]) if top else "Пусто"

    await message.answer(f"<b>Твои инвайты:</b> {invites}\n\n<b>Топ-10:</b>\n{top_text}")

# ==================== АДМИНСКИЕ КОМАНДЫ ====================

async def is_admin(m: Message) -> bool:
    return m.from_user.id in ADMIN_IDS

@dp.message(Command("addinvite"))
async def add_invite(message: Message):
    if not await is_admin(message):
        return
    try:
        user = message.text.split()[1].replace("@", "")
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET invites = invites + 1 WHERE username = ?", (user,))
            if db.total_changes == 0:
                await message.answer("Пользователь не найден")
            else:
                await db.commit()
                await message.answer(f"✅ +1 инвайт @{user}")
    except:
        await message.answer("Использование: /addinvite @username")

@dp.message(Command("removeinvite"))
async def remove_invite(message: Message):
    if not await is_admin(message):
        return
    try:
        user = message.text.split()[1].replace("@", "")
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET invites = MAX(0, invites - 1) WHERE username = ?", (user,))
            await db.commit()
            await message.answer(f"🛑 -1 инвайт @{user}")
    except:
        await message.answer("Использование: /removeinvite @username")

@dp.message(Command("givelink"))
async def givelink_cmd(message: Message):
    if not await is_admin(message):
        return
    try:
        user = message.text.split()[1].replace("@", "")
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id FROM users WHERE username = ?", (user,)) as cur:
                row = await cur.fetchone()
            if not row:
                await message.answer("Пользователь не в базе")
                return
            user_id = row[0]
            link = make_link(user_id)
        await message.answer(f"🔗 Ссылка для @{user}: {link}")
    except:
        await message.answer("Использование: /givelink @username")

@dp.message(Command("ban"))
async def ban_user(message: Message):
    if not await is_admin(message):
        return
    try:
        user = message.text.split()[1].replace("@", "")
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET banned = 1 WHERE username = ?", (user,))
            await db.commit()
        await message.answer(f"🚫 @{user} забанен (инвайты не считаются)")
    except:
        await message.answer("Использование: /ban @username")

@dp.message(Command("unban"))
async def unban_user(message: Message):
    if not await is_admin(message):
        return
    try:
        user = message.text.split()[1].replace("@", "")
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET banned = 0 WHERE username = ?", (user,))
            await db.commit()
        await message.answer(f"✅ @{user} разбанен")
    except:
        await message.answer("Использование: /unban @username")

@dp.message(Command("top"))
async def admin_top(message: Message):
    if not await is_admin(message):
        return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, invites FROM users WHERE banned = 0 ORDER BY invites DESC") as cur:
            rows = await cur.fetchall()
    text = "<b>Топ участников:</b>\n" + "\n".join([f"{i+1}. @{u[0] or 'NoName'} — {u[1]}" for i, u in enumerate(rows)])
    await message.answer(text or "Нет активных участников")

@dp.message(Command("export"))
async def export_csv(message: Message):
    if not await is_admin(message):
        return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, username, invites, banned FROM users ORDER BY invites DESC") as cur:
            rows = await cur.fetchall()
    with open("export.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    await message.answer_document(types.FSInputFile("export.csv"), caption="Экспорт таблицы")

# ==================== ЗАПУСК ДЛЯ RENDER ====================
# Фейковый веб-сервер, чтобы Render не убивал бота
from aiohttp import web
import asyncio

async def ping(request):
    return web.Response(text="OK")

async def on_startup(app):
    await init_db()

app = web.Application()
app.on_startup.append(on_startup)
app.router.add_get('/', ping)

async def main():
    # запускаем бота
    asyncio.create_task(dp.start_polling(bot))
    # запускаем веб-сервер на порту Render
