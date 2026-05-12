import asyncio
import os
import sys
import csv
import logging
import urllib.request
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties # <-- ОЧЕНЬ ВАЖНО: Добавляем этот импорт
import aiosqlite

# ================== НАСТРОЙКИ ==================
# Токен и ID админов берутся из переменных Render
TOKEN = os.getenv("8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg")
ADMIN_IDS_STR = os.getenv("827744412")
ADMIN_IDS = set(int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip().isdigit())

if not TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не задан в Environment Variables на Render!")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# <-- ИСПРАВЛЕНИЕ: Используем DefaultBotProperties для parse_mode
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML")) 
dp = Dispatcher()
DB_NAME = "contest.db"

# ================== БАЗА ДАННЫХ ==================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT UNIQUE,
                invites INTEGER DEFAULT 0,
                banned INTEGER DEFAULT 0,
                added_date TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        # Дефолтный текст про клубы
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('clubs_info', 'Информация о клубах пока не добавлена. Администрация скоро её заполнит.')")
        await db.commit()
    logging.info("✅ База данных инициализирована.")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def get_or_create_user(user_id: int, username: str):
    """Получает пользователя из БД или создает нового, если его нет."""
    async with aiosqlite.connect(DB_NAME) as db:
        # Пытаемся получить пользователя
        async with db.execute("SELECT user_id, username, invites, banned, added_date FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if not user:
            # Пользователь не существует, вставляем его
            current_date = datetime.now().strftime("%Y-%m-%d")
            await db.execute("INSERT INTO users (user_id, username, invites, banned, added_date) VALUES (?, ?, 0, 0, ?)",
                             (user_id, username, current_date))
            await db.commit()
            logging.info(f"Новый пользователь зарегистрирован: @{username} ({user_id})")
            return (user_id, username, 0, 0, current_date)
        else:
            # Пользователь существует, проверяем, не изменился ли его username
            if user[1] != username:
                await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
                await db.commit()
                logging.info(f"Username обновлен для {user_id}: {user[1]} -> {username}")
                return (user[0], username, user[2], user[3], user[4]) # Возвращаем обновленный кортеж
            return user # Возвращаем существующий кортеж пользователя

# ================== КОМАНДЫ ДЛЯ ИГРОКОВ ==================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    
    user_data = await get_or_create_user(user_id, username)
    
    if user_data[3] == 1:  # banned
        await message.answer("⛔ Вы заблокированы в системе.")
        return

    response_text = (
        f"👋 Привет, @{user_data[1]}!\n\n"
        f"Ты участвуешь в конкурсе инвайтов!\n"
        f"Твоих инвайтов: <b>{user_data[2]}</b>\n\n"
        f"Используй команду /stats, чтобы увидеть свою текущую статистику."
    )
    
    if is_admin(user_id):
        response_text += "\n\n⚙️ Ты администратор. Используй /admin для списка команд."

    await message.answer(response_text)

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    
    user_data = await get_or_create_user(user_id, username) # Получаем актуальные данные
    
    if user_data[3] == 1: # banned
        await message.answer("⛔ Вы заблокированы в системе.")
        return

    await message.answer(f"📊 <b>Твоя статистика:</b>\n✅ Инвайтов: {user_data[2]}", parse_mode="HTML")

@dp.message(Command("clubsinfo"))
async def cmd_clubsinfo(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'clubs_info'") as cursor:
            row = await cursor.fetchone()
    info_text = row[0] if row else "Информация о клубах пока не добавлена."
    await message.answer(f"🏆 <b>Информация о клубах:</b>\n\n{info_text}", parse_mode="HTML")

@dp.message(Command("top"))
async def cmd_top(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, invites FROM users WHERE banned = 0 ORDER BY invites DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer("Пока нет участников в топе.")
        return
    text = "🏆 <b>ТОП-10 участников:</b>\n\n"
    for i, row in enumerate(rows, 1):
        text += f"{i}. @{row[0] or 'NoName'} — {row[1]} инвайтов\n"
    await message.answer(text, parse_mode="HTML")

# ================== АДМИНСКИЕ КОМАНДЫ ==================
@dp.message(Command("admin"))
async def cmd_admin_help(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    
    help_text = (
        "⚙️ <b>Админ-панель:</b>\n\n"
        "<b>Управление инвайтами:</b>\n"
        "<code>/addinvite @username [кол-во]</code> — добавить инвайты (по умолчанию +1)\n"
        "<code>/removeinvite @username [кол-во]</code> — отнять инвайты (по умолчанию -1)\n"
        "<code>/setinvite @username &lt;кол-во&gt;</code> — установить точное кол-во инвайтов\n\n"
        "<b>Управление информацией:</b>\n"
        "<code>/setclubsinfo &lt;текст&gt;</code> — обновить инфо о клубах (отображается по /clubsinfo)\n\n"
        "<b>Модерация:</b>\n"
        "<code>/ban @username</code> — забанить пользователя\n"
        "<code>/unban @username</code> — разбанить пользователя\n\n"
        "<b>Выгрузка данных:</b>\n"
        "<code>/export</code> — получить CSV файл со всеми данными\n\n"
        "<b>Техническое:</b>\n"
        "<code>/getip</code> — получить IP сервера для Brawl Stars Devs"
    )
    await message.answer(help_text, parse_mode="HTML")


@dp.message(Command("getip"))
async def cmd_getip(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    try:
        ip = urllib.request.urlopen('https://api.ipify.org').read().decode('utf8')
        await message.answer(f"🌐 <b>IP-адрес этого сервера:</b>\n<code>{ip}</code>\n\nВставляй его на страницу devs Brawl Stars.", parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка получения IP: {e}")
        await message.answer(f"Ошибка получения IP: {e}")

@dp.message(Command("setclubsinfo"))
async def cmd_setclubsinfo(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    
    text = message.text.replace("/setclubsinfo", "", 1).strip()
    if not text:
        await message.answer("Использование: /setclubsinfo <текст>")
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE settings SET value = ? WHERE key = 'clubs_info'", (text,))
        await db.commit()
    await message.answer("✅ Информация о клубах обновлена!")

@dp.message(Command("addinvite"))
async def cmd_addinvite(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    try:
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Использование: /addinvite @username [кол-во]")
            return
        
        target_username = parts[1].replace("@", "")
        amount = 1 # Значение по умолчанию
        
        if len(parts) > 2:
            try:
                amount = int(parts[2])
                if amount <= 0:
                    await message.answer("Количество инвайтов должно быть положительным числом.")
                    return
            except ValueError:
                await message.answer("Количество инвайтов должно быть числом.")
                return
        
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id, invites FROM users WHERE username = ?", (target_username,)) as cursor:
                user_data = await cursor.fetchone()
            
            if not user_data:
                await message.answer(f"❌ Пользователь @{target_username} не найден. Он должен сначала написать боту /start.")
                return
            
            target_user_id, old_invites = user_data[0], user_data[1]
            new_invites = old_invites + amount
            
            await db.execute("UPDATE users SET invites = ? WHERE username = ?", (new_invites, target_username))
            await db.commit()
            
            await message.answer(f"✅ Добавлено {amount} инвайтов для @{target_username}. Всего: {new_invites}")
            
            # Уведомление пользователя
            try:
                await bot.send_message(target_user_id, f"🎉 Администратор добавил тебе {amount} инвайтов! Всего: {new_invites}")
            except Exception as e:
                logging.warning(f"Не удалось отправить уведомление пользователю {target_user_id}: {e}")

    except IndexError:
        await message.answer("Использование: /addinvite @username [кол-во]")
    except Exception as e:
        logging.error(f"Ошибка в /addinvite: {e}")
        await message.answer(f"Произошла ошибка: {e}")

@dp.message(Command("removeinvite"))
async def cmd_removeinvite(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    try:
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Использование: /removeinvite @username [кол-во]")
            return
        
        target_username = parts[1].replace("@", "")
        amount = 1 # Значение по умолчанию
        
        if len(parts) > 2:
            try:
                amount = int(parts[2])
                if amount <= 0:
                    await message.answer("Количество инвайтов должно быть положительным числом.")
                    return
            except ValueError:
                await message.answer("Количество инвайтов должно быть числом.")
                return
        
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id, invites FROM users WHERE username = ?", (target_username,)) as cursor:
                user_data = await cursor.fetchone()
            
            if not user_data:
                await message.answer(f"❌ Пользователь @{target_username} не найден.")
                return
            
            target_user_id, old_invites = user_data[0], user_data[1]
            new_invites = max(0, old_invites - amount) # Инвайты не могут быть меньше 0
            
            await db.execute("UPDATE users SET invites = ? WHERE username = ?", (new_invites, target_username))
            await db.commit()
            
            await message.answer(f"📉 Отнято {amount} инвайтов у @{target_username}. Всего: {new_invites}")
            
            # Уведомление пользователя
            try:
                await bot.send_message(target_user_id, f"⚠️ Администратор отнял у тебя {amount} инвайтов. Всего: {new_invites}")
            except Exception as e:
                logging.warning(f"Не удалось отправить уведомление пользователю {target_user_id}: {e}")

    except IndexError:
        await message.answer("Использование: /removeinvite @username [кол-во]")
    except Exception as e:
        logging.error(f"Ошибка в /removeinvite: {e}")
        await message.answer(f"Произошла ошибка: {e}")

@dp.message(Command("setinvite"))
async def cmd_setinvite(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    try:
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("Использование: /setinvite @username <точное кол-во>")
            return
        
        target_username = parts[1].replace("@", "")
        try:
            amount = int(parts[2])
            if amount < 0:
                await message.answer("Количество инвайтов не может быть отрицательным.")
                return
        except ValueError:
            await message.answer("Количество инвайтов должно быть числом.")
            return
        
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id FROM users WHERE username = ?", (target_username,)) as cursor:
                target_user_id = await cursor.fetchone()
            
            if not target_user_id:
                await message.answer(f"❌ Пользователь @{target_username} не найден.")
                return
            
            await db.execute("UPDATE users SET invites = ? WHERE username = ?", (amount, target_username))
            await db.commit()
            
            await message.answer(f"🔧 Установлено ровно {amount} инвайтов для @{target_username}.")
            
            # Уведомление пользователя
            try:
                await bot.send_message(target_user_id[0], f"🔧 Администратор установил тебе {amount} инвайтов. Всего: {amount}")
            except Exception as e:
                logging.warning(f"Не удалось отправить уведомление пользователю {target_user_id[0]}: {e}")

    except IndexError:
        await message.answer("Использование: /setinvite @username <точное кол-во>")
    except Exception as e:
        logging.error(f"Ошибка в /setinvite: {e}")
        await message.answer(f"Произошла ошибка: {e}")

@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    try:
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Использование: /ban @username")
            return
        target_username = parts[1].replace("@", "")
        
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id FROM users WHERE username = ?", (target_username,)) as cursor:
                target_user_id = await cursor.fetchone()
            if not target_user_id:
                await message.answer(f"❌ Пользователь @{target_username} не найден.")
                return
            
            await db.execute("UPDATE users SET banned = 1 WHERE username = ?", (target_username,))
            await db.commit()
        await message.answer(f"⛔ @{target_username} забанен. Он больше не сможет пользоваться ботом.")
        # Уведомление пользователя
        try:
            await bot.send_message(target_user_id[0], "⛔ Вы были забанены администрацией и больше не можете пользоваться ботом.")
        except Exception as e:
            logging.warning(f"Не удалось отправить уведомление забаненному пользователю {target_user_id[0]}: {e}")

    except Exception as e:
        logging.error(f"Ошибка в /ban: {e}")
        await message.answer(f"Произошла ошибка: {e}")

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    try:
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Использование: /unban @username")
            return
        target_username = parts[1].replace("@", "")
        
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id FROM users WHERE username = ?", (target_username,)) as cursor:
                target_user_id = await cursor.fetchone()
            if not target_user_id:
                await message.answer(f"❌ Пользователь @{target_username} не найден.")
                return
            
            await db.execute("UPDATE users SET banned = 0 WHERE username = ?", (target_username,))
            await db.commit()
        await message.answer(f"✅ @{target_username} разбанен. Теперь он снова может пользоваться ботом.")
        # Уведомление пользователя
        try:
            await bot.send_message(target_user_id[0], "✅ Вы были разбанены администрацией и снова можете пользоваться ботом.")
        except Exception as e:
            logging.warning(f"Не удалось отправить уведомление разбаненному пользователю {target_user_id[0]}: {e}")

    except Exception as e:
        logging.error(f"Ошибка в /unban: {e}")
        await message.answer(f"Произошла ошибка: {e}")


@dp.message(Command("export"))
async def cmd_export(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, username, invites, banned, added_date FROM users ORDER BY invites DESC") as cursor:
            rows = await cursor.fetchall()
            
    filename = "export.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Username", "Invites", "Banned", "Added Date"])
        writer.writerows(rows)
        
    await message.answer_document(types.FSInputFile(filename), caption="📁 Таблица участников")

# ================== ВЕБ-СЕРВЕР (ЧТОБЫ RENDER НЕ УСЫПЛЯЛ БОТА) ==================
async def on_startup(app):
    await init_db()
    # Запускаем поллинг бота как фоновую задачу
    asyncio.create_task(dp.start_polling(bot))
    logging.info("🚀 Бот запущен и готов к работе.")

async def on_shutdown(app):
    await bot.session.close()
    logging.info("👋 Бот остановлен.")

def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    # Render требует, чтобы приложение слушало порт
    port = int(os.environ.get("PORT", 10000))
    logging.info(f"Веб-сервер запущен на порту {port}")
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
