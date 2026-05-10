import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= КОНФИГУРАЦИЯ =================
API_TOKEN = '8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg'  # Вставьте сюда токен бота
GROUP_ID = -1003726194322  # ID вашей группы
# ===============================================

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ================= РАБОТА С БАЗОЙ ДАННЫХ =================
def init_db():
    """Инициализация базы данных SQLite."""
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            invite_count INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def add_user(user_id, username):
    """Добавляет пользователя в базу, если его нет."""
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO users (user_id, username, invite_count) VALUES (?, ?, 0)', (user_id, username))
    else:
        cursor.execute('UPDATE users SET username = ? WHERE user_id = ?', (username, user_id))
    conn.commit()
    conn.close()

def increment_invites(user_id):
    """Увеличивает счетчик инвайтов."""
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET invite_count = invite_count + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_user_invites(user_id):
    """Возвращает количество инвайтов пользователя."""
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('SELECT invite_count FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def get_leaderboard(limit=10):
    """Возвращает топ пользователей по инвайтам."""
    conn = sqlite3.connect('invites.db')
    cursor = conn.cursor()
    cursor.execute('SELECT username, invite_count FROM users ORDER BY invite_count DESC LIMIT ?', (limit,))
    result = cursor.fetchall()
    conn.close()
    return result

# ================= ОБРАБОТЧИКИ КОМАНД =================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработка команды /start (для новых участников)."""
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    
    # Добавляем пользователя
    add_user(user_id, username)
    
    # Проверяем, есть ли параметр (ID пригласившего)
    args = message.text.split()
    if len(args) > 1:
        try:
            inviter_id = int(args[1])
            # Увеличиваем счетчик пригласившего
            increment_invites(inviter_id)
            # Уведомляем пригласившего
            try:
                await bot.send_message(
                    inviter_id,
                    f"🎉 Новый участник присоединился по вашей ссылке! "
                    f"Теперь у вас {get_user_invites(inviter_id)} инвайтов."
                )
            except:
                pass
        except ValueError:
            pass
    
    await message.answer(
        f"Привет, {username}! 👋\n"
        f"Ты присоединился к нашей группе.\n"
        f"Используй команды:\n"
        f"/getlink - Получить свою ссылку для приглашения\n"
        f"/myinvites - Посмотреть свои инвайты\n"
        f"/leaderboard - Топ-10 по инвайтам\n"
        f"/help - Помощь"
    )

@dp.message(Command("getlink"))
async def cmd_getlink(message: types.Message):
    """Генерация инвайт-ссылки."""
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    
    # Добавляем пользователя, если его нет
    add_user(user_id, username)
    
    # Генерируем ссылку (в телеграме это ссылка на бота с параметром)
    bot_username = (await bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start={user_id}"
    
    await message.answer(
        f"Ваша инвайт-ссылка:\n"
        f"`{invite_link}`\n\n"
        f"Отправьте её друзьям, чтобы пригласить их в группу. "
        f"Каждый новый участник, перешедший по ссылке, увеличит ваш счетчик инвайтов.",
        parse_mode="Markdown"
    )

@dp.message(Command("myinvites"))
async def cmd_myinvites(message: types.Message):
    """Показывает количество инвайтов пользователя."""
    user_id = message.from_user.id
    invites = get_user_invites(user_id)
    await message.answer(f"У вас {invites} инвайтов.")

@dp.message(Command("leaderboard"))
async def cmd_leaderboard(message: types.Message):
    """Показывает топ-10 по инвайтам."""
    leaderboard = get_leaderboard(10)
    if not leaderboard:
        await message.answer("Топ пуст. Начните приглашать друзей!")
        return
    
    text = "🏆 **Топ-10 по инвайтам** 🏆\n\n"
    for i, (username, count) in enumerate(leaderboard, 1):
        text += f"{i}. @{username} — {count} инвайтов\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Справка по командам."""
    help_text = (
        "🤖 **Команды бота:**\n\n"
        "/getlink — Получить свою инвайт-ссылку\n"
        "/myinvites — Посмотреть свои инвайты\n"
        "/leaderboard — Топ-10 по инвайтам\n"
        "/help — Помощь\n\n"
        "Как это работает:\n"
        "1. Получите свою ссылку командой /getlink.\n"
        "2. Отправьте её друзьям.\n"
        "3. Когда друг перейдет по ссылке и присоединится к группе, "
        "ваш счетчик инвайтов увеличится."
    )
    await message.answer(help_text, parse_mode="Markdown")

# ================= ЗАПУСК =================
async def main():
    init_db()
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Bot is running!"}

@app.get("/ping")
async def ping():
    return {"message": "pong"}

async def run_polling():
    init_db()
    await dp.start_polling(bot, skip_updates=True)

# Запускаем бота в фоне
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_polling())
