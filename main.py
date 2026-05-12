import asyncio
import logging
import sqlite3
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg"
BS_API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6Ijk4Y2Q3NzViLThjZTYtNGNjMy1iNDc3LTA3YmQxZDAzOTNkNiIsImlhdCI6MTc3ODUxNjUwNCwic3ViIjoiZGV2ZWxvcGVyLzVkYmMwMDMyLTA4OGYtMTc5ZS01ZWQ5LWZlZTkxNDQ5MjNhNCIsInNjb3BlcyI6WyJicmF3bHN0YXJzIl0sImxpbWl0cyI6W3sidGllciI6ImRldmVsb3Blci9zaWx2ZXIiLCJ0eXBlIjoidGhyb3R0bGluZyJ9LHsiY2lkcnMiOlsiNzQuMjIwLjQ4LjIzNSJdLCJ0eXBlIjoiY2xpZW50In1dfQ.Z4_SyqzVyzAiUiLfUTorcEcgq8YOJWPWgYxsxxMfwp3pX4BcsFBSAhekIjafQi66KKlAJWk2ehNtllIH48Mthw"
ADMIN_IDS = [827744412] # Замени на свой ID Telegram

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect('contest.db')
    c = conn.cursor()
    # Таблица участников
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, username TEXT, score INTEGER DEFAULT 0, invited_count INTEGER DEFAULT 0)''')
    # Таблица клубов
    c.execute('''CREATE TABLE IF NOT EXISTS clubs 
                 (tag TEXT PRIMARY KEY, name TEXT)''')
    # Таблица подтвержденных инвайтов (чтобы не засчитывать одного и того же дважды)
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
    """Проверяет игрока через Brawl Stars API"""
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
    
    # Регистрация пользователя
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    
    # Логика рефералки
    if len(args) > 1:
        inviter_id = int(args[1])
        if inviter_id != user_id:
            # Увеличиваем счетчик сырых приглашений у пригласившего
            c.execute("UPDATE users SET invited_count = invited_count + 1 WHERE user_id = ?", (inviter_id,))
            await bot.send_message(inviter_id, f"🔥 Кто-то перешел по твоей ссылке! Осталось дождаться, пока он вступит в клуб.")
            
    conn.commit()
    conn.close()

    text = (
        f"Привет, {message.from_user.full_name}! 👋\n\n"
        f"Хочешь выиграть PRO PASS? 🏆\n"
        f"1. Нажми кнопку ниже, чтобы получить свою ссылку.\n"
        f"2. Отправь её друзьям.\n"
        f"3. Друг должен вступить в наш чат И в клуб Brawl Stars.\n"
        f"4. Когда друг вступит, пришли его тег (например #2PP00) админу @masuchkavince для проверки!"
    )
    
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="🔗 Получить мою ссылку", callback_data="get_link"))
    kb.add(InlineKeyboardButton(text="🏆 Таблица лидеров", callback_data="show_top"))
    
    await message.answer(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "get_link")
async def get_link_callback(callback: types.CallbackQuery):
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={callback.from_user.id}"
    await callback.answer(f"Твоя ссылка: {link}", show_alert=True)

# --- АДМИН КОМАНДЫ ---

@dp.message(Command("getip"))
async def cmd_getip(message: types.Message):
    if not is_admin(message.from_user.id): return
    ip = await get_public_ip()
    await message.answer(f"🌐 IP этого сервера: <code>{ip}</code>\nВставь его в настройки ключа на developer.brawlstars.com", parse_mode="HTML")

@dp.message(Command("clubsinfo"))
async def cmd_clubsinfo(message: types.Message):
    if not is_admin(message.from_user.id): return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT tag, name FROM clubs")
    clubs = c.fetchall()
    conn.close()
    
    if not clubs:
        await message.answer("Список клубов пуст.")
        return
        
    text = "🏰 **Зарегистрированные клубы:**\n"
    for tag, name in clubs:
        text += f"• {name} (`{tag}`)\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("add_club"))
async def cmd_add_club(message: types.Message):
    if not is_admin(message.from_user.id): return
    try:
        # Формат: /add_club #TAG Название
        parts = message.text.split(maxsplit=2)
        tag = parts[1].upper()
        name = parts[2]
        
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO clubs (tag, name) VALUES (?, ?)", (tag, name))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Клуб {name} ({tag}) добавлен.")
    except IndexError:
        await message.answer("❌ Формат: /add_club #TAG Название")

@dp.message(Command("del_club"))
async def cmd_del_club(message: types.Message):
    if not is_admin(message.from_user.id): return
    try:
        tag = message.text.split()[1].upper()
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM clubs WHERE tag = ?", (tag,))
        conn.commit()
        conn.close()
        await message.answer(f"🗑 Клуб {tag} удален.")
    except IndexError:
        await message.answer("❌ Формат: /del_club #TAG")

@dp.message(Command("verify"))
async def cmd_verify(message: types.Message):
    """Проверка игрока через API"""
    if not is_admin(message.from_user.id): return
    
    try:
        # Формат: /verify #TAG @username_пригласившего (или ID)
        parts = message.text.split()
        bs_tag = parts[1].upper()
        inviter_ref = parts[2] # Можно передать ID или юзернейм, для простоты сделаем поиск по БД
        
        # 1. Проверяем, не засчитан ли уже этот тег
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT inviter_id FROM verified_invites WHERE bs_tag = ?", (bs_tag,))
        if c.fetchone():
            await message.answer("⚠️ Этот игрок уже был засчитан ранее!")
            conn.close()
            return

        # 2. Ищем ID пригласившего
        inviter_id = None
        if inviter_ref.isdigit():
            inviter_id = int(inviter_ref)
        else:
            c.execute("SELECT user_id FROM users WHERE username = ?", (inviter_ref.replace('@', ''),))
            res = c.fetchone()
            if res: inviter_id = res[0]

        if not inviter_id:
            await message.answer("❌ Не найден пользователь, который пригласил.")
            conn.close()
            return

        # 3. Проверяем через Brawl Stars API
        player_data = await check_bs_player(bs_tag)
        if not player_data:
            await message.answer("❌ Игрок не найден в Brawl Stars или ошибка API.")
            conn.close()
            return

        player_club = player_data.get('club', {})
        if not player_club:
             await message.answer(f"❌ Игрок {player_data['name']} не состоит в клубе.")
             conn.close()
             return

        # 4. Проверяем, является ли клуб "нашим"
        c.execute("SELECT tag FROM clubs")
        allowed_clubs = [row[0] for row in c.fetchall()]
        
        player_club_tag = player_club.get('tag', '').upper()
        
        if player_club_tag in allowed_clubs:
            # УСПЕХ! Начисляем балл
            c.execute("UPDATE users SET score = score + 1 WHERE user_id = ?", (inviter_id,))
            c.execute("INSERT INTO verified_invites (bs_tag, inviter_id) VALUES (?, ?)", (bs_tag, inviter_id))
            conn.commit()
            await message.answer(f"✅ **ЗАСЧИТАНО!**\nИгрок: {player_data['name']}\nКлуб: {player_club['name']}\nБалл начислен пользователю ID: {inviter_id}")
            
            # Уведомляем пригласившего
            try:
                await bot.send_message(inviter_id, f"🎉 Твой инвайт {player_data['name']} подтвержден! +1 балл.")
            except: pass
        else:
            await message.answer(f"❌ Игрок состоит в клубе {player_club['name']} ({player_club_tag}), но его нет в списке разрешенных.")
            
        conn.close()

    except IndexError:
        await message.answer("❌ Формат: /verify #BS_TAG @username_inviter")

@dp.message(Command("add_point"))
async def cmd_add_point(message: types.Message):
    """Ручное добавление (если API лагает или нужно наказать/поощрить)"""
    if not is_admin(message.from_user.id): return
    try:
        user_id = int(message.text.split()[1])
        amount = int(message.text.split()[2])
        
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET score = score + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Пользователю {user_id} изменен счет на {amount}.")
    except:
        await message.answer("❌ Формат: /add_point USER_ID AMOUNT")

@dp.message(Command("top"))
@dp.callback_query(F.data == "show_top")
async def cmd_top(event: types.Message | types.CallbackQuery):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, score FROM users ORDER BY score DESC LIMIT 10")
    top_users = c.fetchall()
    conn.close()
    
    text = "🏆 **ТОП-10 УЧАСТНИКОВ** 🏆\n\n"
    if not top_users:
        text += "Пока пусто..."
    else:
        for i, (uname, score) in enumerate(top_users, 1):
            text += f"{i}. @{uname} — {score} инвайтов\n"
            
    if isinstance(event, types.Message):
        await event.answer(text, parse_mode="Markdown")
    else:
        await event.message.edit_text(text, parse_mode="Markdown")

# ================= ЗАПУСК =================
async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
