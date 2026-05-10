import asyncio
import logging
import os
import sqlite3
import threading
from datetime import datetime

from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# ================= КОНФИГУРАЦИЯ =================
API_TOKEN = "8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg"  # Вставь токен бота
# ===============================================

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()

    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, invite_count INTEGER DEFAULT 0, invited_by INTEGER, invited_at TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS invite_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, inviter_id INTEGER, invited_user_id INTEGER, invited_username TEXT, timestamp TIMESTAMP, status TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")

    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def set_setting(key, value):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def add_user(user_id, username):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO users (user_id, username, invite_count, invited_by, invited_at) VALUES (?, ?, 0, NULL, NULL)", (user_id, username))
    else:
        cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    conn.close()

def increment_invites(inviter_id, invited_user_id, invited_username):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    if inviter_id == invited_user_id:
        conn.close()
        return False
    cursor.execute("SELECT invited_by FROM users WHERE user_id = ?", (invited_user_id,))
    row = cursor.fetchone()
    if row and row[0] is not None:
        conn.close()
        return False
    cursor.execute("SELECT id FROM invite_logs WHERE inviter_id = ? AND invited_user_id = ?", (inviter_id, invited_user_id))
    if cursor.fetchone():
        conn.close()
        return False

    now = datetime.now()
    cursor.execute("UPDATE users SET invite_count = invite_count + 1, invited_by = ?, invited_at = ? WHERE user_id = ?", (inviter_id, now, invited_user_id))
    cursor.execute("INSERT INTO invite_logs (inviter_id, invited_user_id, invited_username, timestamp, status) VALUES (?, ?, ?, ?, ?)", (inviter_id, invited_user_id, invited_username, now, "success"))
    conn.commit()
    conn.close()
    return True

def get_user_invites(user_id):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("SELECT invite_count FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def get_user_by_username(username: str):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, invite_count FROM users WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()
    return result

def set_user_invites(user_id: int, new_count: int):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET invite_count = ? WHERE user_id = ?", (max(0, new_count), user_id))
    conn.commit()
    conn.close()

def add_user_invites(user_id: int, delta: int):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET invite_count = invite_count + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()

def log_manual_action(admin_id: int, target_user_id: int, target_username: str, action: str, delta_or_value: str):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO invite_logs (inviter_id, invited_user_id, invited_username, timestamp, status) VALUES (?, ?, ?, ?, ?)",
                   (admin_id, target_user_id, target_username, datetime.now(), action + ":" + str(delta_or_value)))
    conn.commit()
    conn.close()

def get_leaderboard(limit=10):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, invite_count FROM users ORDER BY invite_count DESC LIMIT ?", (limit,))
    result = cursor.fetchall()
    conn.close()
    return result

def get_invite_logs(limit=50):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT il.inviter_id, il.invited_user_id, il.invited_username, il.timestamp, il.status, u.username as inviter_username
        FROM invite_logs il LEFT JOIN users u ON il.inviter_id = u.user_id ORDER BY il.timestamp DESC LIMIT ?
    """, (limit,))
    result = cursor.fetchall()
    conn.close()
    return result

async def is_chat_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

# ================= КОМАНДЫ =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    add_user(user_id, username)

    args = message.text.split()
    if len(args) > 1:
        try:
            inviter_id = int(args[1])
            
            # 1. Получаем ID привязанной группы
            group_id_str = get_setting("group_id")
            if not group_id_str:
                await message.answer("⚠️ Бот еще не привязан к группе. Попросите админа написать /setgroup в нужном чате.")
                return
            
            group_id = int(group_id_str)
            
            # 2. Проверяем, состоит ли пользователь в группе
            try:
                member = await bot.get_chat_member(group_id, user_id)
                is_member = member.status in ("member", "administrator", "creator")
            except Exception:
                is_member = False
            
            # 3. Если не состоит - отправляем в группу
            if not is_member:
                try:
                    chat = await bot.get_chat(group_id)
                    group_link = f"https://t.me/{chat.username}" if chat.username else "привязанный чат (узнайте у админа)"
                except Exception:
                    group_link = "привязанный чат"
                
                await message.answer(
                    f"❌ Чтобы инвайт засчитался, ты должен сначала вступить в группу!\n"
                    f"👉 {group_link}\n\n"
                    f"После вступления нажми /start еще раз."
                )
                return
            
            # 4. Если состоит - засчитываем
            success = increment_invites(inviter_id, user_id, username)
            if success:
                try:
                    await bot.send_message(inviter_id, f"🎉 Новый участник по вашей ссылке! У вас {get_user_invites(inviter_id)} инвайтов.")
                except Exception:
                    pass
        except ValueError:
            pass
    else:
        await message.answer(
            "Привет! 👋\n\n"
            "Команды:\n"
            "/getlink — Получить свою инвайт-ссылку\n"
            "/myinvites — Посмотреть свои инвайты\n"
            "/leaderboard — Топ-10\n"
            "/help — Помощь"
        )

@dp.message(Command("setgroup"))
async def cmd_setgroup(message: types.Message):
    """Привязка группы (только для админов внутри группы)"""
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("❌ Эту команду нужно писать в группе, которую нужно привязать.")
        return
    if not await is_chat_admin(message.chat.id, message.from_user.id):
        await message.answer("❌ Только администраторы могут привязывать группу.")
        return
    
    set_setting("group_id", str(message.chat.id))
    await message.answer(f"✅ Группа успешно привязана!\nТеперь инвайты будут засчитываться только тем, кто вступит в этот чат.")

@dp.message(Command("getlink"))
async def cmd_getlink(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    add_user(user_id, username)
    bot_username = (await bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start={user_id}"
    await message.answer(f"Ваша инвайт-ссылка:\n`{invite_link}`\n\nОтправьте её друзьям. (Инвайт засчитается только после вступления друга в группу)", parse_mode="Markdown")

@dp.message(Command("myinvites"))
async def cmd_myinvites(message: types.Message):
    await message.answer(f"У вас {get_user_invites(message.from_user.id)} инвайтов.")

@dp.message(Command("leaderboard"))
async def cmd_leaderboard(message: types.Message):
    leaderboard = get_leaderboard(10)
    if not leaderboard:
        await message.answer("Топ пуст.")
        return
    text = "🏆 **Топ-10 по инвайтам** 🏆\n\n"
    for i, (username, count) in enumerate(leaderboard, 1):
        text += f"{i}. @{username} — {count} инвайтов\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("🤖 **Команды**\n\n"
                         "/getlink — Получить ссылку\n"
                         "/myinvites — Мои инвайты\n"
                         "/leaderboard — Топ-10\n"
                         "/logs — (админы) логи\n"
                         "/addinvites @user N — (админы) добавить\n"
                         "/removeinvites @user N — (админы) убрать\n"
                         "/setinvites @user N — (админы) установить\n"
                         "/setgroup — (админы в группе) привязать чат")

@dp.message(Command("logs"))
async def cmd_logs(message: types.Message):
    if message.chat.type not in ("group", "supergroup") or not await is_chat_admin(message.chat.id, message.from_user.id):
        await message.answer("❌ Только для админов в группе.")
        return
    logs = get_invite_logs(30)
    if not logs: return await message.answer("Логи пусты.")
    text = "📋 **Логи (последние 30)** 📋\n\n"
    for inviter_id, invited_user_id, invited_username, timestamp, status, inviter_username in logs:
        inviter_name = f"@{inviter_username}" if inviter_username else str(inviter_id)
        invited_name = f"@{invited_username}" if invited_username else str(invited_user_id)
        text += f"👤 {inviter_name} → {invited_name} | {status}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("addinvites"))
async def cmd_addinvites(message: types.Message):
    if message.chat.type not in ("group", "supergroup") or not await is_chat_admin(message.chat.id, message.from_user.id):
        return await message.answer("❌ Только для админов в группе.")
    try:
        args = message.text.split()
        if len(args) < 3: return await message.answer("❌ /addinvites @username 5")
        username, delta = args[1].lstrip("@"), int(args[2])
        target = get_user_by_username(username)
        if not target: return await message.answer("❌ Пользователь не найден (он должен запустить бота).")
        add_user_invites(target[0], delta)
        log_manual_action(message.from_user.id, target[0], target[1] or username, "manual_add", delta)
        await message.answer(f"✅ Добавлено {delta} инвайтов @{username}. Теперь: {get_user_invites(target[0])}")
    except ValueError:
        await message.answer("❌ Введите число.")

@dp.message(Command("removeinvites"))
async def cmd_removeinvites(message: types.Message):
    if message.chat.type not in ("group", "supergroup") or not await is_chat_admin(message.chat.id, message.from_user.id):
        return await message.answer("❌ Только для админов в группе.")
    try:
        args = message.text.split()
        if len(args) < 3: return await message.answer("❌ /removeinvites @username 2")
        username, delta = args[1].lstrip("@"), int(args[2])
        target = get_user_by_username(username)
        if not target: return await message.answer("❌ Пользователь не найден.")
        add_user_invites(target[0], -delta)
        log_manual_action(message.from_user.id, target[0], target[1] or username, "manual_remove", delta)
        await message.answer(f"✅ Убрано {delta} инвайтов у @{username}. Теперь: {get_user_invites(target[0])}")
    except ValueError:
        await message.answer("❌ Введите число.")

@dp.message(Command("setinvites"))
async def cmd_setinvites(message: types.Message):
    if message.chat.type not in ("group", "supergroup") or not await is_chat_admin(message.chat.id, message.from_user.id):
        return await message.answer("❌ Только для админов в группе.")
    try:
        args = message.text.split()
        if len(args) < 3: return await message.answer("❌ /setinvites @username 10")
        username, new_count = args[1].lstrip("@"), int(args[2])
        target = get_user_by_username(username)
        if not target: return await message.answer("❌ Пользователь не найден.")
        set_user_invites(target[0], new_count)
        log_manual_action(message.from_user.id, target[0], target[1] or username, "manual_set", new_count)
        await message.answer(f"✅ Установлено {new_count} инвайтов для @{username}.")
    except ValueError:
        await message.answer("❌ Введите число.")

# ================= ВЕБ-СЕРВЕР (для Render) =================
@app.route("/")
def home(): return "Bot is running!"

@app.route("/ping")
def ping(): return "pong"

# ================= ЗАПУСК =================
async def run_polling():
    init_db()
    await dp.start_polling(bot, skip_updates=True)

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(run_polling())
