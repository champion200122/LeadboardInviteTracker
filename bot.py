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

    # Пользователи
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            invite_count INTEGER DEFAULT 0,
            invited_by INTEGER,
            invited_at TIMESTAMP
        )
        """
    )

    # Логи инвайтов
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS invite_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_id INTEGER,
            invited_user_id INTEGER,
            invited_username TEXT,
            timestamp TIMESTAMP,
            status TEXT
        )
        """
    )

    conn.commit()
    conn.close()


def add_user(user_id, username):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute(
            "INSERT INTO users (user_id, username, invite_count, invited_by, invited_at) VALUES (?, ?, 0, NULL, NULL)",
            (user_id, username),
        )
    else:
        cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    conn.close()


def increment_invites(inviter_id, invited_user_id, invited_username):
    """Увеличивает счётчик инвайтов с защитой от накрутки.
    Возвращает True если засчитано, False если отклонено."""
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()

    # 1) Нельзя приглашать самого себя
    if inviter_id == invited_user_id:
        conn.close()
        return False

    # 2) Этот человек уже был приглашён кем-то ранее
    cursor.execute("SELECT invited_by FROM users WHERE user_id = ?", (invited_user_id,))
    row = cursor.fetchone()
    if row and row[0] is not None:
        conn.close()
        return False

    # 3) Этот пригласивший уже приглашал этого человека ранее (лог)
    cursor.execute(
        "SELECT id FROM invite_logs WHERE inviter_id = ? AND invited_user_id = ?",
        (inviter_id, invited_user_id),
    )
    if cursor.fetchone():
        conn.close()
        return False

    # 4) Засчитываем инвайт
    now = datetime.now()
    cursor.execute(
        "UPDATE users SET invite_count = invite_count + 1, invited_by = ?, invited_at = ? WHERE user_id = ?",
        (inviter_id, now, invited_user_id),
    )

    cursor.execute(
        "INSERT INTO invite_logs (inviter_id, invited_user_id, invited_username, timestamp, status) VALUES (?, ?, ?, ?, ?)",
        (inviter_id, invited_user_id, invited_username, now, "success"),
    )

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


def get_leaderboard(limit=10):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT username, invite_count FROM users ORDER BY invite_count DESC LIMIT ?",
        (limit,),
    )
    result = cursor.fetchall()
    conn.close()
    return result


def get_invite_logs(limit=50):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT il.inviter_id, il.invited_user_id, il.invited_username, il.timestamp, il.status, u.username as inviter_username
        FROM invite_logs il
        LEFT JOIN users u ON il.inviter_id = u.user_id
        ORDER BY il.timestamp DESC
        LIMIT ?
        """,
        (limit,),
    )
    result = cursor.fetchall()
    conn.close()
    return result


async def is_chat_admin(chat_id: int, user_id: int) -> bool:
    """Проверка, что пользователь — админ/создатель чата (работает в группах/супергруппах)."""
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

    # Обработка инвайт-ссылки: /start <inviter_id>
    args = message.text.split()
    if len(args) > 1:
        try:
            inviter_id = int(args[1])
            success = increment_invites(inviter_id, user_id, username)
            if success:
                try:
                    await bot.send_message(
                        inviter_id,
                        f"🎉 Новый участник по вашей ссылке! У вас {get_user_invites(inviter_id)} инвайтов.",
                    )
                except Exception:
                    pass
            # Если не success — просто молча не засчитываем (самозайм/повтор/уже приглашён)
        except ValueError:
            pass

    await message.answer(
        "Привет! 👋\n\n"
        "Команды:\n"
        "/getlink — Получить свою инвайт-ссылку\n"
        "/myinvites — Посмотреть свои инвайты\n"
        "/leaderboard — Топ-10 по инвайтам\n"
        "/help — Помощь"
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
        "Отправьте её друзьям. (Нельзя приглашать самого себя; один человек — один инвайт)",
        parse_mode="Markdown",
    )


@dp.message(Command("myinvites"))
async def cmd_myinvites(message: types.Message):
    user_id = message.from_user.id
    invites = get_user_invites(user_id)
    await message.answer(f"У вас {invites} инвайтов.")


@dp.message(Command("leaderboard"))
async def cmd_leaderboard(message: types.Message):
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
    await message.answer(
        "🤖 **Команды**\n\n"
        "/getlink — Получить свою инвайт-ссылку\n"
        "/myinvites — Посмотреть свои инвайты\n"
        "/leaderboard — Топ-10 по инвайтам\n"
        "/logs — (только админам чата) логи инвайтов\n"
        "/help — Помощь\n\n"
        "⚠️ Правила:\n"
        "• Нельзя приглашать самого себя\n"
        "• Один человек засчитывается только один раз\n"
        "• Инвайт засчитывается, когда человек переходит по вашей ссылке и запускает бота (/start <ваш_id>)",
    )


@dp.message(Command("logs"))
async def cmd_logs(message: types.Message):
    """Логи инвайтов. Доступ только администраторам/создателю чата, где вызвана команда."""
    # Разрешаем только в группах/супергруппах, где вызывающий — админ/создатель
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("❌ Команда /logs доступна только в группе (для админов).")
        return

    if not await is_chat_admin(message.chat.id, message.from_user.id):
        await message.answer("❌ Только администраторы группы могут просматривать логи.")
        return

    logs = get_invite_logs(30)
    if not logs:
        await message.answer("Логи пусты.")
        return

    text = "📋 **Логи инвайтов (последние 30)** 📋\n\n"
    for inviter_id, invited_user_id, invited_username, timestamp, status, inviter_username in logs:
        inviter_name = f"@{inviter_username}" if inviter_username else str(inviter_id)
        invited_name = f"@{invited_username}" if invited_username else str(invited_user_id)
        text += f"👤 {inviter_name} → {invited_name}\n"
        text += f"   🕒 {timestamp}\n"
        text += f"   ✅ {status}\n\n"

    await message.answer(text, parse_mode="Markdown")


# ================= ВЕБ-СЕРВЕР (для Render) =================
@app.route("/")
def home():
    return "Bot is running!"


@app.route("/ping")
def ping():
    return "pong"


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
