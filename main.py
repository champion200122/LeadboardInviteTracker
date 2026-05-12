# main.py
import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite
import csv
from datetime import datetime

# ================== НАСТРОЙКИ ==================
TOKEN = os.getenv("BOT_TOKEN")  # ставь в переменные окружения на Render
ADMIN_IDS = [123456789, 987654321]  # ← твои Telegram ID

# ================== БД ==================
DB_NAME = "contest.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                invites INTEGER DEFAULT 0,
                ref_link TEXT UNIQUE,
                banned INTEGER DEFAULT 0,
                joined_date TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS clubs (
                tag TEXT PRIMARY KEY,
                name TEXT
            )
        ''')
        await db.commit()

# ================== БОТ ==================
bot = Bot(token=TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Генерация реф-ссылки
def generate_ref(user_id: int) -> str:
    return f"https://t.me/{(await bot.get_me()).username}?start=ref_{user_id}"

# ================== КОМАНДЫ ==================
@dp.message(CommandStart())
async def start_cmd(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    ref = None
    
    if message.text.startswith("/start ref_"):
        ref = int(message.text.split("ref_")[1])
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            
        if not row:
            ref_link = generate_ref(user_id)
            joined_date = datetime.now().strftime("%Y-%m-%d %H:%M")
            await db.execute(
                "INSERT INTO users (user_id, username, ref_link, joined_date) VALUES (?, ?, ?, ?)",
                (user_id, username, ref_link, joined_date)
            )
            await db.commit()
            
            # Если пришёл по рефералке — засчитываем инвайт пригласившему
            if ref and ref != user_id:
                async with db.execute("SELECT banned FROM users WHERE user_id = ?", (ref,)) as cursor:
                    inviter = await cursor.fetchone()
                if inviter and not inviter[0]:  # не забанен
                    await db.execute("UPDATE users SET invites = invites + 1 WHERE user_id = ?", (ref,))
                    await db.commit()
                    try:
                        await bot.send_message(ref, f"🎉 Новый игрок по твоей ссылке!\n+1 инвайт ✅\nВсего: {inviter[0]+1}")
                    except:
                        pass
        
        else:
            ref_link = row[3] if len(row) > 3 else generate_ref(user_id)

    keyboard = InlineKeyboardButton(text="Моя реферальная ссылка", url=ref_link)
    kb = InlineKeyboardMarkup(inline_keyboard=[[keyboard]])
    
    text = (
        "🔥 <b>Конкурс инвайтов запущен!</b>\n\n"
        "Приглашай друзей — главный приз PRO PASS!\n\n"
        f"Твоя персональная ссылка 👇"
    )
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT invites, ref_link FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        
        if not row:
            await message.answer("Ты ещё не участвуешь. Напиши /start")
            return
            
        invites = row[0]
        link = row[1]
        
        # Топ-10
        async with db.execute("SELECT username, invites FROM users WHERE banned = 0 ORDER BY invites DESC LIMIT 10") as cursor:
            top = await cursor.fetchall()
        
        top_text = "\n".join([f"{i+1}. @{u[0] if u[0] else 'NoName'} — {u[1]}" for i, u in enumerate(top)]) if top else "Пока пусто"
        
        text = (
            f"<b>Твоя статистика:</b>\n"
            f"Инвайтов: <b>{invites}</b>\n\n"
            f"<b>Топ-10:</b>\n{top_text}"
        )
        await message.answer(text)

# ================== АДМИНСКИЕ КОМАНДЫ ==================
async def is_admin(message: Message) -> bool:
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Доступ запрещён")
        return False
    return True

@dp.message(Command("addclub"))
async def add_club(message: Message):
    if not await is_admin(message): return
    try:
        tag = message.text.split()[1].upper()
        name = " ".join(message.text.split()[2:])
        if not name:
            await message.answer("Использование: /addclub #TAG Название клуба")
            return
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR REPLACE INTO clubs (tag, name) VALUES (?, ?)", (tag, name))
            await db.commit()
        await message.answer(f"Клуб добавлен: {tag} — {name}")
    except:
        await message.answer("Ошибка. Пример: /addclub #2PPPPP Клуб Чемпионов")

@dp.message(Command("removeclub"))
async def remove_club(message: Message):
    if not await is_admin(message): return
    try:
        tag = message.text.split()[1].upper()
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DELETE FROM clubs WHERE tag = ?", (tag,))
            await db.commit()
        await message.answer(f"Клуб {tag} удалён")
    except:
        await message.answer("Укажи тег: /removeclub #2PPPPP")

@dp.message(Command("addinvite"))
async def add_invite(message: Message):
    if not await is_admin(message): return
    try:
        username = message.text.split()[1].replace("@", "")
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET invites = invites + 1 WHERE username = ?", (username,))
            if db.total_changes == 0:
                await message.answer("Пользователь не найден")
            else:
                await db.commit()
                await message.answer(f"+1 инвайт для @{username}")
    except:
        await message.answer("Использование: /addinvite @username")

@dp.message(Command("removeinvite"))
async def remove_invite(message: Message):
    if not await is_admin(message): return
    try:
        username = message.text.split()[1].replace("@", "")
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET invites = invites - 1 WHERE username = ? AND invites > 0", (username,))
            if db.total_changes > 0:
                await db.commit()
                await message.answer(f"-1 инвайт у @{username}")
            else:
                await message.answer("Нечего снимать или пользователь не найден")
    except:
        await message.answer("Использование: /removeinvite @username")

@dp.message(Command("ban"))
async def ban_user(message: Message):
    if not await is_admin(message): return
    try:
        username = message.text.split()[1].replace("@", "")
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET banned = 1 WHERE username = ?", (username,))
            await db.commit()
        await message.answer(f"@{username} забанен")
    except:
        await message.answer("Ошибка")

@dp.message(Command("unban"))
async def unban_user(message: Message):
    if not await is_admin(message): return
    try:
        username = message.text.split()[1].replace("@", "")
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET banned = 0 WHERE username = ?", (username,))
            await db.commit()
        await message.answer(f"@{username} разбанен")
    except:
        await message.answer("Ошибка")

@dp.message(Command("top"))
async def admin_top(message: Message):
    if not await is_admin(message): return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, invites, joined_date FROM users WHERE banned = 0 ORDER BY invites DESC") as cursor:
            rows = await cursor.fetchall()
    
    text = "<b>Полный топ участников:</b>\n\n"
    for i, row in enumerate(rows):
        text += f"{i+1}. @{row[0] or 'NoName'} — {row[1]} инвайтов (с {row[2]})\n"
    
    await message.answer(text or "Пусто")

@dp.message(Command("export"))
async def export_csv(message: Message):
    if not await is_admin(message): return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, username, invites, joined_date, banned FROM users ORDER BY invites DESC") as cursor:
            rows = await cursor.fetchall()
    
    with open("export.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Username", "Invites", "Joined", "Banned"])
        writer.writerows(rows)
    
    await message.answer_document(types.FSInputFile("export.csv"), caption="Экспорт участников")

# ================== ЗАПУСК ==================
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
