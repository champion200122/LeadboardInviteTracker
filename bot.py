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

    # Таблица настроек (для сохранения group_id и т.п.)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

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


def get_setting(key, default=None):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else default


def set_setting(key, value):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
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
    """Увеличивает счётчик инвайтов с защитой от накрутки."""
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()

    required_group_id = get_setting("required_group_id", None)
    
    # Проверка 1: нельзя приглашать самого себя
    if inviter_id == invited_user_id:
        conn.close()
        return False

    # Если группа не настроена — игнорируем инвайт
    if not required_group_id:
        conn.close()
        return False

    # Проверка 2: этот человек уже был приглашён кем-то ранее
    cursor.execute("SELECT invited_by FROM users WHERE user_id = ?", (invited_user_id,))
    row = cursor.fetchone()
    if row and row[0] is not None:
        conn.close()
        return False

    # Проверка 3: этот пригласивший уже приглашал этого человека ранее (лог)
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


def get_user_by_username(username: str):
    conn = sqlite3.connect("invites.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, invite_count FROM users WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()
    return result  # (user_id, username, invite_count) или None


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
    cursor.execute(
        "INSERT INTO invite_logs (inviter_id, invited_user_id, invited_username, timestamp, status) VALUES (?, ?, ?, ?, ?)",
        (admin_id, target_user_id, target_username, datetime.now(), action + ":" + str(delta_or_value)),
    )
    conn.commit()
    conn.close()


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
    """Проверка, что пользователь — админ/создатель чата."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def check_user_in_required_group(user_id: int) -> bool:
    """Проверяет, состоит ли пользователь в требуемой группе."""
    required_group_id = get_setting("required_group_id", None)
    if not required_group_id:
        return False  # Группа не настроена
    try:
        member = await bot.get_chat_member(required_group_id, user_id)
        return member.status in ("member", "administrator", "creator")
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

            # Проверяем, что пользователь в нужной группе
            if not await check_user_in_required_group(user_id):
                await message.answer(
                    "❌ Вы должны состоять в нашей группе, чтобы зачесть инвайт."
                )
                return

            # Увеличиваем счётчик инвайтов
            success = increment_invites(inviter_id, user_id, username)
            if success:
                try:
                    await bot.send_message(
                        inviter_id,
                        f"🎉 Новый участник по вашей ссылке! У вас {get_user_invites(inviter_id)} инвайтов.",
                    )
                except Exception:
                    pass
            else:
                await message.answer(
                    "❌ Этот человек уже был приглашён ранее или вы уже приглашали его.",
                    parse_mode="Markdown",
                )
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
        "Отправьте её друзьям. Они должны вступить в нашу группу, чтобы засчитался инвайт!",
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
        "/logs — (админы чата) логи инвайтов\n"
        "/addinvites @user N — (админы чата) добавить инвайты\n"
        "/removeinvites @user N — (админы чата) убрать инвайты\n"
        "/setinvites @user N — (админы чата) установить инвайты\n"
        "/setgroup — (админы чата) определить текущую группу как нужную\n"
        "/statusgroup — проверить статус настройки группы\n"
        "/help — Помощь\n\n"
        "⚠️ Правила:\n"
        "• Нельзя приглашать самого себя\n"
        "• Один человек засчитывается только один раз\n"
        "• Человек должен быть в нужной группе, чтобы засчитался инвайт\n"
        "• Админ должен назначить нужную группу командой /setgroup",
    )


# ================= АДМИНСКИЕ КОМАНДЫ =================
@dp.message(Command("setgroup"))
async def cmd_setgroup(message: types.Message):
    """Назначить текущую группу как нужную. Только админы чата."""
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("❌ Команда доступна только в группе (для админов).")
        return

    if not await is_chat_admin(message.chat.id, message.from_user.id):
        await message.answer("❌ Только администраторы группы могут использовать эту команду.")
        return

    # Сохраняем ID группы
    set_setting("required_group_id", str(message.chat.id))

    await message.answer(
        f"✅ Текущая группа установлена как нужная!\n"
        f"ID группы: `{message.chat.id}`\n"
        f"Теперь инвайты будут засчитываться только если человек вступит в эту группу.",
        parse_mode="Markdown",
    )


@dp.message(Command("statusgroup"))
async def cmd_statusgroup(message: types.Message):
    """Проверить текущий статус настройки группы."""
    required_group_id = get_setting("required_group_id", None)

    if not required_group_id:
        await message.answer(
            "❓ Нужная группа не назначена.\n\n"
            "Используйте команду `/setgroup` в нужной группе, чтобы назначить её.",
        )
        return

    try:
        # Проверяем, существует ли группа
        group_info = await bot.get_chat(int(required_group_id))
        group_title = getattr(group_info, 'title', f'Чат {required_group_id}')
        await message.answer(
            f"✅ Нужная группа назначена!\n\n"
            f"Название: `{group_title}`\n"
            f"ID: `{required_group_id}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await message.answer(f"⚠️ Группа не найдена или недоступна: {e}")


@dp.message(Command("logs"))
async def cmd_logs(message: types.Message):
    """Логи инвайтов. Доступ только администраторам/создателю чата, где вызвана команда."""
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
        text += f"   🧾 {status}\n\n"

    await message.answer(text, parse_mode="Markdown")


# ============ АДМИН-КОМАНДЫ УПРАВЛЕНИЯ ИНВАЙТАМИ ============

@dp.message(Command("addinvites"))
async def cmd_addinvites(message: types.Message):
    """Добавить инвайты пользователю. Только админы чата."""
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("❌ Команда доступна только в группе (для админов).")
        return

    if not await is_chat_admin(message.chat.id, message.from_user.id):
        await message.answer("❌ Только администраторы группы могут использовать эту команду.")
        return

    try:
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Использование: /addinvites @username 5")
            return

        username = args[1].lstrip("@")
        delta = int(args[2])

        target = get_user_by_username(username)
        if not target:
            await message.answer(
                f"❌ Пользователь @{username} не найден. Он должен хотя бы раз запустить бота (/start)."
            )
            return

        target_id, target_username_db, current = target
        add_user_invites(target_id, delta)
        new_count = get_user_invites(target_id)

        log_manual_action(message.from_user.id, target_id, target_username_db or username, "manual_add", delta)

        await message.answer(f"✅ Добавлено {delta} инвайтов @{username}. Теперь у него {new_count} инвайтов.")
    except ValueError:
        await message.answer("❌ Количество должно быть целым числом. Пример: /addinvites @username 5")
    except Exception as e:
        logging.exception(e)
        await message.answer("❌ Ошибка при выполнении команды.")


@dp.message(Command("removeinvites"))
async def cmd_removeinvites(message: types.Message):
    """Убрать инвайты у пользователя. Только админы чата."""
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("❌ Команда доступна только в группе (для админов).")
        return

    if not await is_chat_admin(message.chat.id, message.from_user.id):
        await message.answer("❌ Только администраторы группы могут использовать эту команду.")
        return

    try:
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Использование: /removeinvites @username 2")
            return

        username = args[1].lstrip("@")
        delta = int(args[2])

        target = get_user_by_username(username)
        if not target:
            await message.answer(
                f"❌ Пользователь @{username} не найден. Он должен хотя бы раз запустить бота (/start)."
            )
            return

        target_id, target_username_db, current = target
        add_user_invites(target_id, -delta)
        new_count = get_user_invites(target_id)

        log_manual_action(message.from_user.id, target_id, target_username_db or username, "manual_remove", delta)

        await message.answer(f"✅ Убрано {delta} инвайтов у @{username}. Теперь у него {new_count} инвайтов.")
    except ValueError:
        await message.answer("❌ Количество должно быть целым числом. Пример: /removeinvites @username 2")
    except Exception as e:
        logging.exception(e)
        await message.answer("❌ Ошибка при выполнении команды.")


@dp.message(Command("setinvites"))
async def cmd_setinvites(message: types.Message):
    """Установить точное количество инвайтов пользователю. Только админы чата."""
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("❌ Команда доступна только в группе (для админов).")
        return

    if not await is_chat_admin(message.chat.id, message.from_user.id):
        await message.answer("❌ Только администраторы группы могут использовать эту команду.")
        return

    try:
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Использование: /setinvites @username 10")
            return

        username = args[1].lstrip("@")
        new_count = int(args[2])

        target = get_user_by_username(username)
        if not target:
            await message.answer(
                f"❌ Пользователь @{username} не найден. Он должен хотя бы раз запустить бота (/start)."
            )
            return

        target_id, target_username_db, current = target
        set_user_invites(target_id, new_count)

        log_manual_action(message.from_user.id, target_id, target_username_db or username, "manual_set", new_count)

        await message.answer(f"✅ Установлено {new_count} инвайтов для @{username}.")
    except ValueError:
        await message.answer("❌ Количество должно быть целым числом. Пример: /setinvites @username 10")
    except Exception as e:
        logging.exception(e)
        await message.answer("❌ Ошибка при выполнении команды.")


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
