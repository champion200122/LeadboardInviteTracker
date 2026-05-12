import asyncio
import logging
import os
import csv
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
import aiosqlite
from aiohttp import web

# ==================== НАСТРОЙКИ ====================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [123456789]   # ← ОБЯЗАТЕЛЬНО поменяй на свой Telegram ID

if not TOKEN:
    raise ValueError("BOT_TOKEN не найден! Добавь его в Environment Variables на Render.")

bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher()

DB_NAME = "contest.db"


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT UNIQUE,
                invites INTEGER DEFAULT 0,
                ref_link TEXT,
                added_at TEXT
            )
        ''')
        await db.commit()


async def is_admin(message: Message) -> bool:
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У тебя нет прав.")
        return False
    return True


# ===================== КОМАНДЫ =====================

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "Привет! Это конкурс инвайтов.\n"
        "Твой результат можно посмотреть командой /stats"
    )


@dp.message(Command("stats"))
async def stats(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT invites FROM users WHERE user_id = ?", (message.from_user.id,)) as cur:
            row = await cur.fetchone()
    
    invites = row[0] if row else 0
    await message.answer(f"Твои инвайты: <b>{invites}</b>", parse_mode="HTML")


@dp.message(Command("addinvite"))
async def add_invite(message: Message):
    if not await is_admin(message): return
    try:
        username = message.text.split()[1].replace("@", "").lower()
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO users (username, invites, added_at) VALUES (?, 1, ?) "
                "ON CONFLICT(username) DO UPDATE SET invites = invites + 1",
                (username, datetime.now().strftime("%Y-%m-%d %H:%M"))
            )
            await db.commit()
        await message.answer(f"✅ +1 инвайт для @{username}")
    except:
        await message.answer("Использование: `/addinvite @username`")


@dp.message(Command("setinvites"))
async def set_invites(message: Message):
    if not await is_admin(message): return
    try:
        username = message.text.split()[1].replace("@", "").lower()
        count = int(message.text.split()[2])
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO users (username, invites, added_at) VALUES (?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET invites = ?",
                (username, count, datetime.now().strftime("%Y-%m-%d %H:%M"), count)
            )
            await db.commit()
        await message.answer(f"✅ @{username} теперь имеет {count} инвайтов")
    except:
        await message.answer("Использование: `/setinvites @username 10`")


@dp.message(Command("givelink"))
async def givelink(message: Message):
    if not await is_admin(message): return
    try:
        username = message.text.split()[1].replace("@", "").lower()
        link = f"https://t.me/{(await bot.get_me()).username}?start=ref_{username}"
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET ref_link = ? WHERE username = ?", (link, username))
            await db.commit()
        await message.answer(f"🔗 Ссылка для <b>@{username}</b>:\n{link}")
    except:
        await message.answer("Использование: `/givelink @username`")


@dp.message(Command("top"))
async def top(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, invites FROM users ORDER BY invites DESC LIMIT 15") as cur:
            rows = await cur.fetchall()
    
    if not rows:
        await message.answer("Пока никого нет.")
        return
        
    text = "<b>🏆 ТОП УЧАСТНИКОВ</b>\n\n"
    for i, (username, invites) in enumerate(rows, 1):
        text += f"{i}. @{username} — <b>{invites}</b> инвайтов\n"
    
    await message.answer(text)


@dp.message(Command("export"))
async def export(message: Message):
    if not await is_admin(message): return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, invites, ref_link, added_at FROM users ORDER BY invites DESC") as cur:
            rows = await cur.fetchall()
    
    with open("contest_export.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Username", "Invites", "Ref_Link", "Added_At"])
        writer.writerows(rows)
    
    await message.answer_document(FSInputFile("contest_export.csv"), caption="Экспорт таблицы")


# ===================== ЗАПУСК =====================
async def ping(request):
    return web.Response(text="Bot is running!")

async def main():
    await init_db()
    logging.info("Бот запущен успешно")
    
    asyncio.create_task(dp.start_polling(bot))
    
    app = web.Application()
    app.router.add_get("/", ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {port}")
    
    await asyncio.Event().wait()  # держим процесс живым


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
