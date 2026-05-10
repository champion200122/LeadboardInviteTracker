import asyncio
import logging
import sqlite3
import threading
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from datetime import datetime

# ================= КОНФИГУРАЦИЯ =================
API_TOKEN = '8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg'
GROUP_ID = -1003726194322  # ID вашей группы
ADMIN_ID = 827744412  # ID админа (ваш ID)
# ===============================================

app = Flask(__name__)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            invite_count INTEGER DEFAULT 0,
            invited_by INTEGER,
            invited_at TIMESTAMP
        )
    ''')
    
    # Таблица логов инвайтов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invite_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_id INTEGER,
            invited_user_id INTEGER,
            invited_username TEXT,
            timestamp TIMESTAMP,
            status TEXT
        )
    ''')
    
    # Таблица баллов (для админа)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS points (
            user_id INTEGER PRIMARY KEY,
            points INTEGER DEFAULT 0
        )
    ''')
    
    conn.commit()
    conn.close()

def add_user(user_id, username):
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO users (user_id, username, invite_count, invited_by, invited_at) VALUES (?, ?, 0, NULL, NULL)', (user_id, username))
    else:
        cursor.execute('UPDATE users SET username = ? WHERE user_id = ?', (username, user_id))
    conn.commit()
    conn.close()

def increment_invites(inviter_id, invited_user_id, invited_username):
    """Увеличивает счетчик инвайтов с проверкой на накрутку"""
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    
    # Проверяем, не приглашал ли этот пользователь уже этого человека
    cursor.execute('SELECT * FROM invite_logs WHERE inviter_id = ? AND invited_user_id = ?', (inviter_id, invited_user_id))
    if cursor.fetchone():
        conn.close()
        return False  # Уже приглашал этого человека
    
    # Проверяем, не был ли этот пользователь уже приглашен кем-то
    cursor.execute('SELECT invited_by FROM users WHERE user_id = ?', (invited_user_id,))
    result = cursor.fetchone()
    if result and result[0]:
        conn.close()
        return False  # Уже был приглашен кем-то
    
    # Увеличиваем счетчик инвайтов
    cursor.execute('UPDATE users SET invite_count = invite_count + 1, invited_by = ?, invited_at = ? WHERE user_id = ?', 
                   (invited_user_id, datetime.now(), inviter_id))
    
    # Записываем в логи
    cursor.execute('INSERT INTO invite_logs (inviter_id, invited_user_id, invited_username, timestamp, status) VALUES (?, ?, ?, ?, ?)',
                   (inviter_id, invited_user_id, invited_username, datetime.now(), 'success'))
    
    conn.commit()
    conn.close()
    return True

def get_user_invites(user_id):
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('SELECT invite_count FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def get_leaderboard(limit=10):
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('SELECT username, invite_count FROM users ORDER BY invite_count DESC LIMIT ?', (limit,))
    result = cursor.fetchall()
    conn.close()
    return result

def get_invite_logs(limit=50):
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT il.inviter_id, il.invited_user_id, il.invited_username, il.timestamp, il.status, u.username as inviter_username
        FROM invite_logs il
        LEFT JOIN users u ON il.inviter_id = u.user_id
        ORDER BY il.timestamp DESC
        LIMIT ?
    ''', (limit,))
    result = cursor.fetchall()
    conn.close()
    return result

def get_user_points(user_id):
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('SELECT points FROM points WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def set_user_points(user_id, points):
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO points (user_id, points) VALUES (?, ?)', (user_id, points))
    conn.commit()
    conn.close()

async def check_user_in_group(user_id):
    """Проверяет, находится ли пользователь в группе"""
    try:
        member = await bot.get_chat_member(GROUP_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

# ================= КОМАНДЫ =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    
    add_user(user_id, username)
    
    # Проверяем, что пользователь в группе
    if not await check_user_in_group(user_id):
        await message.answer("❌ Сначала присоединитесь к группе!")
        return
    
    # Проверяем параметр (ID пригласившего)
    args = message.text.split()
    if len(args) > 1:
        try:
            inviter_id = int(args[1])
            
            # Проверка на накрутку: нельзя приглашать самого себя
            if inviter_id == user_id:
                await message.answer("❌ Нельзя приглашать самого себя!")
                return
            
            # Проверяем, что пригласивший тоже в группе
            if not await check_user_in_group(inviter_id):
                await message.answer("❌ Пригласивший не находится в группе!")
                return
            
            # Пытаемся увеличить счетчик
            success = increment_invites(inviter_id, user_id, username)
            
            if success:
                try:
                    await bot.send_message(
                        inviter_id,
                        f"🎉 Новый участник по вашей ссылке! У вас {get_user_invites(inviter_id)} инвайтов."
                    )
                except:
                    pass
            else:
                await message.answer("❌ Этот пользователь уже был приглашен или вы уже приглашали его!")
        except:
            pass
    
    await message.answer(
        f"Привет, {username}! 👋\n"
        f"Ты присоединился к нашей группе.\n"
        f"Используй команды:\n"
        f"/getlink - Получить ссылку\n"
        f"/myinvites - Мои инвайты\n"
        f"/leaderboard - Топ-10\n"
        f"/help - Помощь"
    )

@dp.message(Command("getlink"))
async def cmd_getlink(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    add_user(user_id, username)
    
    bot_username = (await bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start={user_id}"
    
    await message.answer(
        f"Ваша инвайт-ссылка:\n`{invite_link}`\n\n"
        f"Отправляйте её друзьям! (Нельзя приглашать самого себя)",
        parse_mode="Markdown"
    )

@dp.message(Command("myinvites"))
async def cmd_myinvites(message: types.Message):
    user_id = message.from_user.id
    invites = get_user_invites(user_id)
    points = get_user_points(user_id)
    await message.answer(f"У вас {invites} инвайтов и {points} баллов.")

@dp.message(Command("leaderboard"))
async def cmd_leaderboard(message: types.Message):
    leaderboard = get_leaderboard(10)
    if not leaderboard:
        await message.answer("Топ пуст. Начните приглашать!")
        return
    
    text = "🏆 **Топ-10 по инвайтам** 🏆\n\n"
    for i, (username, count) in enumerate(leaderboard, 1):
        text += f"{i}. @{username} — {count} инвайтов\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🤖 **Команды бота:**\n\n"
        "/getlink — Получить свою ссылку\n"
        "/myinvites — Посмотреть инвайты и баллы\n"
        "/leaderboard — Топ-10\n"
        "/help — Помощь\n\n"
        "Как работает:\n"
        "1. Получите ссылку командой /getlink\n"
        "2. Отправьте друзьям\n"
        "3. Каждый новый участник увеличит ваш счётчик!\n\n"
        "⚠️ **Важно:**\n"
        "- Нельзя приглашать самого себя\n"
        "- Каждый пользователь может быть приглашен только один раз"
    )

# ================= АДМИНСКИЕ КОМАНДЫ =================
@dp.message(Command("logs"))
async def cmd_logs(message: types.Message):
    """Просмотр логов инвайтов (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав!")
        return
    
    logs = get_invite_logs(20)
    if not logs:
        await message.answer("Логи пусты.")
        return
    
    text = "📋 **Логи инвайтов (последние 20)** 📋\n\n"
    for log in logs:
        inviter_id, invited_user_id, invited_username, timestamp, status, inviter_username = log
        text += f"👤 @{inviter_username} → @{invited_username}\n"
        text += f"   Время: {timestamp}\n"
        text += f"   Статус: {status}\n\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("addpoints"))
async def cmd_addpoints(message: types.Message):
    """Добавить баллы пользователю (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав!")
        return
    
    try:
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Использование: /addpoints @username количество")
            return
        
        username = args[1].replace('@', '')
        points = int(args[2])
        
        # Находим user_id по username
        conn = sqlite3.connect('invites.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users WHERE username = ?', (username,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            await message.answer(f"❌ Пользователь @{username} не найден!")
            return
        
        user_id = result[0]
        current_points = get_user_points(user_id)
        new_points = current_points + points
        set_user_points(user_id, new_points)
        
        await message.answer(f"✅ Добавлено {points} баллов пользователю @{username}. Теперь у него {new_points} баллов.")
    except:
        await message.answer("❌ Ошибка! Использование: /addpoints @username количество")

@dp.message(Command("removepoints"))
async def cmd_removepoints(message: types.Message):
    """Убрать баллы у пользователя (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав!")
        return
    
    try:
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Использование: /removepoints @username количество")
            return
        
        username = args[1].replace('@', '')
        points = int(args[2])
        
        # Находим user_id по username
        conn = sqlite3.connect('invites.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users WHERE username = ?', (username,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            await message.answer(f"❌ Пользователь @{username} не найден!")
            return
        
        user_id = result[0]
        current_points = get_user_points(user_id)
        new_points = max(0, current_points - points)
        set_user_points(user_id, new_points)
        
        await message.answer(f"✅ Убрано {points} баллов у пользователя @{username}. Теперь у него {new_points} баллов.")
    except:
        await message.answer("❌ Ошибка! Использование: /removepoints @username количество")

@dp.message(Command("setpoints"))
async def cmd_setpoints(message: types.Message):
    """Установить баллы пользователю (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав!")
        return
    
    try:
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Использование: /setpoints @username количество")
            return
        
        username = args[1].replace('@', '')
        points = int(args[2])
        
        # Находим user_id по username
        conn = sqlite3.connect('invites.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users WHERE username = ?', (username,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            await message.answer(f"❌ Пользователь @{username} не найден!")
            return
        
        user_id = result[0]
        set_user_points(user_id, points)
        
        await message.answer(f"✅ Установлено {points} баллов пользователю @{username}.")
    except:
        await message.answer("❌ Ошибка! Использование: /setpoints @username количество")

# ================= ВЕБ-СЕРВЕР =================
@app.route('/')
def home():
    return "Bot is running!"

@app.route('/ping')
def ping():
    return "pong"

# ================= ЗАПУСК =================
async def run_polling():
    init_db()
    await dp.start_polling(bot, skip_updates=True)

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(run_polling())

# ================= ЗАПУСК =================
import uvicorn
import threading

app = FastAPI(title="InviteTracker Bot")

@app.get("/")
async def root():
    return {"status": "✅ Bot is running!"}

@app.get("/ping")
async def ping():
    return {"ping": "pong"}

async def run_bot_polling():
    """Запуск бота в фоне"""
    init_db()
    logging.info("🤖 Bot polling started")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    # Запускаем polling бота в отдельном потоке
    bot_thread = threading.Thread(
        target=lambda: asyncio.run(run_bot_polling()), 
        daemon=True
    )
    bot_thread.start()

    # Запускаем FastAPI сервер (это важно для Render)
    uvicorn.run(app, host="0.0.0.0", port=5000)
