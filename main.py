import os
import asyncio
import logging
import sqlite3
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= ТОКЕНЫ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BS_API_TOKEN = os.getenv("BS_API_TOKEN")

# Защита от запуска без токенов
if not BOT_TOKEN:
    raise Exception("Ошибка: BOT_TOKEN не задан в переменных окружения!")
if not BS_API_TOKEN:
    raise Exception("Ошибка: BS_API_TOKEN не задан в переменных окружения!")

ADMIN_IDS = [827744412]  # ← Твой ID (можно оставить навсегда)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect('contest.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, username TEXT, score INTEGER DEFAULT 0, invited_count INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS clubs 
                 (tag TEXT PRIMARY KEY, name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS verified_invites 
                 (bs_tag TEXT PRIMARY KEY, inviter_id INTEGER)''')
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect('contest.db')

# ================= УТИЛИТЫ =================
def is_admin(user_id):
    return user_id in ADMIN_IDS

async def get_public_ip():
    async with aiohttp.ClientSession() as session:
        async with session.get('https://api.ipify.org') as response:
            return await response.text()

async def check_bs_player(tag: str):
    tag = tag.replace('#', '%23')
    url = f"https://api.brawlstars.com/v1/players/{tag}"
    headers = {"Authorization": f"Bearer {BS_API_TOKEN}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            return None

# ================= КОМАНДЫ =================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    args = message.text.split()
    
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    
    if len(args) > 1:
        inviter_id = int(args[1])
        if inviter_id != user_id:
            c.execute("UPDATE users SET invited_count = invited_count + 1 WHERE user_id = ?", (inviter_id,))
            await bot.send_message(inviter_id, "Кто-то перешёл по твоей ссылке!")
            
    conn.commit()
    conn.close()

    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="Получить мою ссылку", callback_data="get_link"))
    kb.add(InlineKeyboardButton(text="Таблица лидеров", callback_data="show_top"))
    
    await message.answer(
        f"Привет, {message.from_user.full_name}!\n\n"
        "Готов забрать PRO PASS?\n"
        "Приглашай друзей — побеждай!\n\n"
        "Нажми кнопку ниже, чтобы получить свою реферальную ссылку:",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "get_link")
async def get_link_callback(callback: types.CallbackQuery):
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={callback.from_user.id}"
    await callback.answer(f"Твоя ссылка:\n{link}\n\nСкопируй и отправь друзьям!", show_alert=True)

@dp.callback_query(F.data == "show_top")
@dp.message(Command("top"))
async def cmd_top(event):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, score FROM users WHERE score > 0 ORDER BY score DESC LIMIT 10")
    top = c.fetchall()
    conn.close()
    
    text = "ТОП-10 УЧАСТНИКОВ\n\n"
    if not top:
        text += "Пока пусто..."
    else:
        for i, (name, score) in enumerate(top, 1):
            text += f"{i}. @{name or 'Unknown'} — {score} инвайтов\n"
    
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown")
    else:
        await event.answer(text, parse_mode="Markdown")

# ================= АДМИН КОМАНДЫ =================
@dp.message(Command("getip"))
async def cmd_getip(message: types.Message):
    if not is_admin(message.from_user.id): return
    ip = await get_public_ip()
    await message.answer(f"IP сервера: <code>{ip}</code>\nВставь его в настройки API ключа на developer.brawlstars.com", parse_mode="HTML")

@dp.message(Command("add_club"))
async def cmd_add_club(message: types.Message):
    if not is_admin(message.from_user.id): return
    try:
        parts = message.text.split(maxsplit=2)
        tag = parts[1].upper()
        name = parts[2]
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO clubs (tag, name) VALUES (?, ?)", (tag, name))
        conn.commit()
        conn.close()
        await message.answer(f"Клуб {name} ({tag}) добавлен!")
    except:
        await message.answer("Формат: /add_club #TAG Название клуба")

@dp.message(Command("clubsinfo"))
async def cmd_clubsinfo(message: types.Message):
    if not is_admin(message.from_user.id): return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT tag, name FROM clubs")
    clubs = c.fetchall()
    conn.close()
    if not clubs:
        await message.answer("Клубы не добавлены.")
        return
    text = "Зарегистрированные клубы:\n"
    for tag, name in clubs:
        text += f"• {name} — {tag}\n"
    await message.answer(text)

@dp.message(Command("verify"))
async def cmd_verify(message: types.Message):
    if not is_admin(message.from_user.id): return
    # Пример: /verify #2PP00 @username
    try:
        parts = message.text.split()
        bs_tag = parts[1].upper()
        inviter_ref = parts[2].lstrip('@')

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT inviter_id FROM verified_invites WHERE bs_tag = ?", (bs_tag,))
        if c.fetchone():
            await message.answer("Этот игрок уже засчитан!")
            conn.close()
            return

        c.execute("SELECT user_id FROM users WHERE username = ?", (inviter_ref,))
        row = c.fetchone()
        if not row:
            await message.answer("Не найден пользователь с таким username")
            conn.close()
            return
        inviter_id = row[0]

        player = await check_bs_player(bs_tag)
        if not player:
            await message.answer("Игрок не найден в Brawl Stars")
            conn.close()
            return

        club = player.get('club', {})
        if not club:
            await message.answer(f"{player['name']} не состоит в клубе")
            conn.close()
            return

        c.execute("SELECT tag FROM clubs")
        allowed = [row[0] for row in c.fetchall()]
        
        if club['tag'].upper() in allowed:
            c.execute("UPDATE users SET score = score + 1 WHERE user_id = ?", (inviter_id,))
            c.execute("INSERT INTO verified_invites (bs_tag, inviter_id) VALUES (?, ?)", (bs_tag, inviter_id))
            conn.commit()
            conn.close()
            await message.answer(f"ЗАСЧИТАНО!\n{player['name']} в клубе {club['name']}\n+1 балл @{inviter_ref}")
            try:
                await bot.send_message(inviter_id, f"Твой инвайт {player['name']} подтверждён! +1 балл")
            except: pass
        else:
            await message.answer(f"Игрок в клубе {club['name']}, но он не в списке разрешённых")
        conn.close()
    except Exception as e:
        await message.answer(f"Ошибка: {e}\nФормат: /verify #TAG @username")

@dp.message(Command("add_point"))
async def cmd_add_point(message: types.Message):
    if not is_admin(message.from_user.id): return
    try:
        parts = message.text.split()
        uid = int(parts[1])
        amount = int(parts[2])
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET score = score + ? WHERE user_id = ?", (amount, uid))
        conn.commit()
        conn.close()
        await message.answer(f"Баллы изменены у {uid} на {amount}")
    except:
        await message.answer("Формат: /add_point USER_ID AMOUNT")

# ================= ЗАПУСК =================
async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)

    # Немного подождать, чтобы Render успел поднять контейнер
    if os.getenv("RENDER"):
        await asyncio.sleep(5)

    print("Бот запущен и работает!")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
