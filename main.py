import asyncio
import logging
import sqlite3
import os
import aiohttp as aiohttp_client
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.environ.get("8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg")
BS_API_TOKEN = os.environ.get("eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6Ijk4Y2Q3NzViLThjZTYtNGNjMy1iNDc3LTA3YmQxZDAzOTNkNiIsImlhdCI6MTc3ODUxNjUwNCwic3ViIjoiZGV2ZWxvcGVyLzVkYmMwMDMyLTA4OGYtMTc5ZS01ZWQ5LWZlZTkxNDQ5MjNhNCIsInNjb3BlcyI6WyJicmF3bHN0YXJzIl0sImxpbWl0cyI6W3sidGllciI6ImRldmVsb3Blci9zaWx2ZXIiLCJ0eXBlIjoidGhyb3R0bGluZyJ9LHsiY2lkcnMiOlsiNzQuMjIwLjQ4LjIzNSJdLCJ0eXBlIjoiY2xpZW50In1dfQ.Z4_SyqzVyzAiUiLfUTorcEcgq8YOJWPWgYxsxxMfwp3pX4BcsFBSAhekIjafQi66KKlAJWk2ehNtllIH48Mthw")
ADMIN_IDS = [827744412]  # Замени на свои ID

# Render дает URL типа https://your-service-name.onrender.com
RENDER_EXTERNAL_URL = os.environ.get("https://leadboardinvitetracker.onrender.com")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
PORT = int(os.environ.get("PORT", 10000))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect('contest.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, 
                  username TEXT, 
                  full_name TEXT,
                  score INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS clubs 
                 (tag TEXT PRIMARY KEY, name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS verified_invites 
                 (bs_tag TEXT PRIMARY KEY, 
                  inviter_id INTEGER,
                  player_name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS pending_invites
                 (invited_user_id INTEGER PRIMARY KEY,
                  inviter_id INTEGER)''')
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect('contest.db')

def is_admin(user_id):
    return user_id in ADMIN_IDS

# ================= УТИЛИТЫ =================
async def get_public_ip():
    async with aiohttp_client.ClientSession() as session:
        async with session.get('https://api.ipify.org') as response:
            return await response.text()

async def check_bs_player(tag: str):
    clean_tag = tag.replace('#', '%23')
    url = f"https://api.brawlstars.com/v1/players/{clean_tag}"
    headers = {"Authorization": f"Bearer {BS_API_TOKEN}"}
    async with aiohttp_client.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            return None

async def get_club_info(tag: str):
    clean_tag = tag.replace('#', '%23')
    url = f"https://api.brawlstars.com/v1/clubs/{clean_tag}"
    headers = {"Authorization": f"Bearer {BS_API_TOKEN}"}
    async with aiohttp_client.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            return None

# ================= КОМАНДЫ =================

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "no_username"
    full_name = message.from_user.full_name

    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
              (user_id, username, full_name))
    c.execute("UPDATE users SET username = ?, full_name = ? WHERE user_id = ?",
              (username, full_name, user_id))

    # Проверяем реферальную ссылку
    args = message.text.split()
    if len(args) > 1:
        try:
            inviter_id = int(args[1])
            if inviter_id != user_id:
                c.execute("INSERT OR IGNORE INTO pending_invites (invited_user_id, inviter_id) VALUES (?, ?)",
                          (user_id, inviter_id))
                try:
                    await bot.send_message(inviter_id,
                        f"👀 {full_name} перешел по твоей ссылке!\n"
                        f"Теперь он должен вступить в клуб BS.\n"
                        f"Когда вступит — пусть скинет тебе свой тег, а ты отправь админу.")
                except:
                    pass
        except ValueError:
            pass

    conn.commit()
    conn.close()

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={user_id}"

    text = (
        f"👋 Привет, <b>{full_name}</b>!\n\n"
        f"🏆 <b>КОНКУРС ИНВАЙТОВ</b>\n"
        f"Приглашай друзей и выиграй PRO PASS!\n\n"
        f"📋 <b>Как участвовать:</b>\n"
        f"1️⃣ Скопируй свою ссылку (кнопка ниже)\n"
        f"2️⃣ Отправь её другу\n"
        f"3️⃣ Друг должен вступить в чат + в клуб BS\n"
        f"4️⃣ Друг скидывает тебе свой тег BS\n"
        f"5️⃣ Ты отправляешь тег админу @masuchkavince\n\n"
        f"🔗 <b>Твоя ссылка:</b>\n<code>{link}</code>\n\n"
        f"<i>Нажми на ссылку чтобы скопировать!</i>"
    )

    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="🏆 Таблица лидеров", callback_data="show_top"))
    kb.add(InlineKeyboardButton(text="📊 Мои инвайты", callback_data="my_stats"))

    await message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())


@dp.callback_query(F.data == "show_top")
async def cb_show_top(callback: types.CallbackQuery):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, full_name, score FROM users WHERE score > 0 ORDER BY score DESC LIMIT 15")
    top_users = c.fetchall()
    conn.close()

    text = "🏆 <b>ТАБЛИЦА ЛИДЕРОВ</b> 🏆\n\n"
    if not top_users:
        text += "Пока никто не набрал баллов...\nБудь первым! 🚀"
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, (uname, fname, score) in enumerate(top_users):
            medal = medals[i] if i < 3 else f"{i+1}."
            display = f"@{uname}" if uname != "no_username" else fname
            text += f"{medal} {display} — <b>{score}</b> инвайтов\n"

    await callback.message.edit_text(text, parse_mode="HTML")


@dp.callback_query(F.data == "my_stats")
async def cb_my_stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT score FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    score = row[0] if row else 0

    c.execute("SELECT bs_tag, player_name FROM verified_invites WHERE inviter_id = ?", (user_id,))
    invites = c.fetchall()
    conn.close()

    text = f"📊 <b>Твоя статистика</b>\n\n"
    text += f"✅ Подтвержденных инвайтов: <b>{score}</b>\n\n"

    if invites:
        text += "👥 <b>Приглашенные игроки:</b>\n"
        for tag, name in invites:
            text += f"  • {name} ({tag})\n"
    else:
        text += "Пока нет подтвержденных инвайтов.\nПриглашай друзей! 🔥"

    await callback.message.edit_text(text, parse_mode="HTML")


@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, full_name, score FROM users WHERE score > 0 ORDER BY score DESC LIMIT 15")
    top_users = c.fetchall()
    conn.close()

    text = "🏆 <b>ТАБЛИЦА ЛИДЕРОВ</b> 🏆\n\n"
    if not top_users:
        text += "Пока никто не набрал баллов..."
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, (uname, fname, score) in enumerate(top_users):
            medal = medals[i] if i < 3 else f"{i+1}."
            display = f"@{uname}" if uname != "no_username" else fname
            text += f"{medal} {display} — <b>{score}</b> инвайтов\n"

    await message.answer(text, parse_mode="HTML")


# ================= АДМИН КОМАНДЫ =================

@dp.message(Command("getip"))
async def cmd_getip(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    ip = await get_public_ip()
    await message.answer(
        f"🌐 <b>IP сервера:</b> <code>{ip}</code>\n\n"
        f"Вставь его на developer.brawlstars.com\n"
        f"в настройках API ключа.",
        parse_mode="HTML"
    )


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    text = (
        "🔧 <b>КОМАНДЫ АДМИНА</b>\n\n"
        "📍 <b>Клубы:</b>\n"
        "<code>/add_club #TAG Название</code> — добавить клуб\n"
        "<code>/del_club #TAG</code> — удалить клуб\n"
        "<code>/clubsinfo</code> — список клубов\n"
        "<code>/club_members #TAG</code> — участники клуба (API)\n\n"
        "📍 <b>Инвайты:</b>\n"
        "<code>/verify #BS_TAG @username</code> — проверить и засчитать\n"
        "<code>/add_point USER_ID 1</code> — добавить баллы\n"
        "<code>/add_point USER_ID -1</code> — снять баллы\n"
        "<code>/unverify #BS_TAG</code> — отменить инвайт\n\n"
        "📍 <b>Инфо:</b>\n"
        "<code>/getip</code> — IP сервера\n"
        "<code>/users</code> — все зарегистрированные\n"
        "<code>/top</code> — таблица лидеров\n"
    )
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("add_club"))
async def cmd_add_club(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = message.text.split(maxsplit=2)
        tag = parts[1].upper()
        if not tag.startswith('#'):
            tag = '#' + tag
        name = parts[2] if len(parts) > 2 else "Без названия"

        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO clubs (tag, name) VALUES (?, ?)", (tag, name))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Клуб <b>{name}</b> (<code>{tag}</code>) добавлен!", parse_mode="HTML")
    except IndexError:
        await message.answer("❌ Формат: <code>/add_club #TAG Название</code>", parse_mode="HTML")


@dp.message(Command("del_club"))
async def cmd_del_club(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        tag = message.text.split()[1].upper()
        if not tag.startswith('#'):
            tag = '#' + tag
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM clubs WHERE tag = ?", (tag,))
        conn.commit()
        conn.close()
        await message.answer(f"🗑 Клуб <code>{tag}</code> удален.", parse_mode="HTML")
    except IndexError:
        await message.answer("❌ Формат: <code>/del_club #TAG</code>", parse_mode="HTML")


@dp.message(Command("clubsinfo"))
async def cmd_clubsinfo(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT tag, name FROM clubs")
    clubs = c.fetchall()
    conn.close()

    if not clubs:
        await message.answer("📭 Список клубов пуст.\nДобавь: <code>/add_club #TAG Название</code>", parse_mode="HTML")
        return

    text = "🏰 <b>Зарегистрированные клубы:</b>\n\n"
    for tag, name in clubs:
        text += f"• <b>{name}</b> — <code>{tag}</code>\n"
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("club_members"))
async def cmd_club_members(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        tag = message.text.split()[1].upper()
        if not tag.startswith('#'):
            tag = '#' + tag

        await message.answer("⏳ Загружаю данные клуба...")
        data = await get_club_info(tag)

        if not data:
            await message.answer("❌ Клуб не найден или ошибка API.\nПроверь тег и IP ключа (/getip)")
            return

        members = data.get('memberList', [])
        text = f"🏰 <b>{data['name']}</b> ({data['tag']})\n"
        text += f"👥 Участников: {len(members)}\n"
        text += f"🏆 Трофеи: {data.get('trophies', 0)}\n\n"

        for m in members[:30]:
            role_emoji = {"president": "👑", "vicePresident": "⭐", "senior": "🔹", "member": "•"}.get(m['role'], '•')
            text += f"{role_emoji} {m['name']} ({m['tag']}) — {m['trophies']}🏆\n"

        if len(members) > 30:
            text += f"\n... и еще {len(members) - 30} участников"

        await message.answer(text, parse_mode="HTML")
    except IndexError:
        await message.answer("❌ Формат: <code>/club_members #TAG</code>", parse_mode="HTML")


@dp.message(Command("verify"))
async def cmd_verify(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer(
                "❌ Формат: <code>/verify #BS_TAG @username</code>\n"
                "или: <code>/verify #BS_TAG 123456789</code>",
                parse_mode="HTML"
            )
            return

        bs_tag = parts[1].upper()
        if not bs_tag.startswith('#'):
            bs_tag = '#' + bs_tag
        inviter_ref = parts[2]

        conn = get_db()
        c = conn.cursor()

        # Проверяем дубликат
        c.execute("SELECT inviter_id FROM verified_invites WHERE bs_tag = ?", (bs_tag,))
        existing = c.fetchone()
        if existing:
            await message.answer(f"⚠️ Тег <code>{bs_tag}</code> уже засчитан ранее (inviter ID: {existing[0]})", parse_mode="HTML")
            conn.close()
            return

        # Ищем пригласившего
        inviter_id = None
        if inviter_ref.lstrip('-').isdigit():
            inviter_id = int(inviter_ref)
        else:
            clean_name = inviter_ref.replace('@', '')
            c.execute("SELECT user_id FROM users WHERE username = ?", (clean_name,))
            res = c.fetchone()
            if res:
                inviter_id = res[0]

        if not inviter_id:
            await message.answer("❌ Пользователь не найден в базе.", parse_mode="HTML")
            conn.close()
            return

        # Проверяем игрока через API
        await message.answer("⏳ Проверяю через Brawl Stars API...")
        player_data = await check_bs_player(bs_tag)

        if not player_data:
            await message.answer(
                "❌ Игрок не найден.\n"
                "Проверь тег и убедись что IP сервера прописан в ключе API.\n"
                "Узнать IP: /getip",
                parse_mode="HTML"
            )
            conn.close()
            return

        player_name = player_data.get('name', 'Unknown')
        player_club = player_data.get('club', {})

        if not player_club:
            await message.answer(
                f"❌ Игрок <b>{player_name}</b> ({bs_tag}) не состоит ни в каком клубе!",
                parse_mode="HTML"
            )
            conn.close()
            return

        # Проверяем клуб
        player_club_tag = player_club.get('tag', '').upper()
        c.execute("SELECT name FROM clubs WHERE tag = ?", (player_club_tag,))
        club_row = c.fetchone()

        if not club_row:
            c.execute("SELECT tag, name FROM clubs")
            allowed = c.fetchall()
            allowed_text = "\n".join([f"  • {n} ({t})" for t, n in allowed]) if allowed else "  Список пуст!"

            await message.answer(
                f"❌ Игрок <b>{player_name}</b> состоит в клубе "
                f"<b>{player_club.get('name', '?')}</b> ({player_club_tag})\n\n"
                f"Но этого клуба нет в списке разрешенных:\n{allowed_text}",
                parse_mode="HTML"
            )
            conn.close()
            return

        # ЗАСЧИТЫВАЕМ
        c.execute("UPDATE users SET score = score + 1 WHERE user_id = ?", (inviter_id,))
        c.execute("INSERT INTO verified_invites (bs_tag, inviter_id, player_name) VALUES (?, ?, ?)",
                  (bs_tag, inviter_id, player_name))
        conn.commit()

        c.execute("SELECT score, username FROM users WHERE user_id = ?", (inviter_id,))
        user_row = c.fetchone()
        conn.close()

        new_score = user_row[0] if user_row else "?"
        inviter_name = user_row[1] if user_row else "?"

        await message.answer(
            f"✅ <b>ИНВАЙТ ЗАСЧИТАН!</b>\n\n"
            f"🎮 Игрок: <b>{player_name}</b> ({bs_tag})\n"
            f"🏰 Клуб: <b>{club_row[0]}</b> ({player_club_tag})\n"
            f"👤 Пригласил: @{inviter_name} (ID: {inviter_id})\n"
            f"📊 Новый счет: <b>{new_score}</b> инвайтов",
            parse_mode="HTML"
        )

        # Уведомляем пригласившего
        try:
            await bot.send_message(inviter_id,
                f"🎉 <b>Твой инвайт подтвержден!</b>\n\n"
                f"Игрок <b>{player_name}</b> засчитан.\n"
                f"Твой текущий счет: <b>{new_score}</b> инвайтов! 🔥",
                parse_mode="HTML"
            )
        except:
            pass

    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("unverify"))
async def cmd_unverify(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        bs_tag = message.text.split()[1].upper()
        if not bs_tag.startswith('#'):
            bs_tag = '#' + bs_tag

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT inviter_id, player_name FROM verified_invites WHERE bs_tag = ?", (bs_tag,))
        row = c.fetchone()

        if not row:
            await message.answer(f"❌ Тег {bs_tag} не найден в подтвержденных инвайтах.")
            conn.close()
            return

        inviter_id, player_name = row
        c.execute("DELETE FROM verified_invites WHERE bs_tag = ?", (bs_tag,))
        c.execute("UPDATE users SET score = MAX(0, score - 1) WHERE user_id = ?", (inviter_id,))
        conn.commit()
        conn.close()

        await message.answer(
            f"🗑 <b>Инвайт отменен!</b>\n\n"
            f"Игрок: {player_name} ({bs_tag})\n"
            f"Снят 1 балл у ID: {inviter_id}",
            parse_mode="HTML"
        )

        try:
            await bot.send_message(inviter_id,
                f"⚠️ Инвайт игрока <b>{player_name}</b> был отменен. -1 балл.",
                parse_mode="HTML"
            )
        except:
            pass

    except IndexError:
        await message.answer("❌ Формат: <code>/unverify #BS_TAG</code>", parse_mode="HTML")


@dp.message(Command("add_point"))
async def cmd_add_point(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = message.text.split()
        
        # Поддерживаем и @username и ID
        target = parts[1]
        amount = int(parts[2])

        conn = get_db()
        c = conn.cursor()
        
        if target.lstrip('-').isdigit():
            user_id = int(target)
        else:
            clean = target.replace('@', '')
            c.execute("SELECT user_id FROM users WHERE username = ?", (clean,))
            res = c.fetchone()
            if not res:
                await message.answer("❌ Пользователь не найден.")
                conn.close()
                return
            user_id = res[0]

        c.execute("UPDATE users SET score = MAX(0, score + ?) WHERE user_id = ?", (amount, user_id))
        conn.commit()

        c.execute("SELECT score, username FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()

        if row:
            sign = "+" if amount > 0 else ""
            await message.answer(
                f"✅ @{row[1]} (ID: {user_id}): {sign}{amount}\n"
                f"Новый счет: <b>{row[0]}</b>",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Пользователь не найден в базе.")

    except (IndexError, ValueError):
        await message.answer("❌ Формат: <code>/add_point @username 1</code>\nили: <code>/add_point USER_ID -1</code>", parse_mode="HTML")


@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id, username, full_name, score FROM users ORDER BY score DESC")
    users = c.fetchall()
    conn.close()

    if not users:
        await message.answer("📭 Нет зарегистрированных пользователей.")
        return

    text = f"👥 <b>Все участники ({len(users)}):</b>\n\n"
    for uid, uname, fname, score in users:
        text += f"• @{uname} ({fname}) — <b>{score}</b> очков [ID: <code>{uid}</code>]\n"

    # Telegram лимит 4096 символов
    if len(text) > 4000:
        parts_list = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for part in parts_list:
            await message.answer(part, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if is_admin(message.from_user.id):
        await cmd_admin(message)
    else:
        await message.answer(
            "ℹ️ <b>Как участвовать в конкурсе:</b>\n\n"
            "1. Напиши /start чтобы получить ссылку\n"
            "2. Отправь ссылку другу\n"
            "3. Друг вступает в чат + клуб BS\n"
            "4. Друг скидывает тег админу\n"
            "5. Админ подтверждает — ты получаешь балл!\n\n"
            "/top — таблица лидеров",
            parse_mode="HTML"
        )


# ================= ЗАПУСК (WEBHOOK) =================

async def on_startup(app):
    init_db()
    webhook_url = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(webhook_url)
    logging.info(f"Webhook set to {webhook_url}")

async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()

def main():
    logging.basicConfig(level=logging.INFO)

    app = web.Application()

    # Регистрируем webhook handler
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
