import os
import json
import asyncio
import aiohttp
from urllib.parse import quote
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ChatMemberOwner, ChatMemberAdministrator
from aiohttp import web

# ------------------------------------------------------------------
# ⚙️ КОНФИГУРАЦИЯ
BOT_TOKEN = "8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg"
BS_API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6Ijk3NDVhOTdkLWI1NjUtNDZjNi1hYjk2LWQyNzA4ZTYwYzY0ZCIsImlhdCI6MTc3ODUxMTE0OCwic3ViIjoiZGV2ZWxvcGVyLzVkYmMwMDMyLTA4OGYtMTc5ZS01ZWQ5LWZlZTkxNDQ5MjNhNCIsInNjb3BlcyI6WyJicmF3bHN0YXJzIl0sImxpbWl0cyI6W3sidGllciI6ImRldmVsb3Blci9zaWx2ZXIiLCJ0eXBlIjoidGhyb3R0bGluZyJ9LHsiY2lkcnMiOlsiMC4wLjAuMCJdLCJ0eXBlIjoiY2xpZW50In1dfQ.e5A40jmtz88Zx4lzrLQADT3HaABHAdos5gbpZpgoc8hXS41lnVSEOLgSqAJIWxC0a_28xBTDm2eTKOrADM2K9A"
BASE_URL = "https://leadboardinvitetracker.onrender.com"
PORT = int(os.getenv("PORT", 8080))

CHECK_INTERVAL = 600
PING_INTERVAL = 300

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
DATA_FILE = "clubs_data.json"

# ------------------------------------------------------------------
# 💾 БАЗА ДАННЫХ
def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ------------------------------------------------------------------
# 🛡️ ПРОВЕРКА АДМИНИСТРАТОРА (ИСПРАВЛЕННАЯ)
async def is_admin(message: Message) -> bool:
    # В личке всегда админ
    if message.chat.type == "private":
        return True

    # Если пишет анонимно от имени чата
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True

    # Если from_user вообще нет
    if not message.from_user:
        return False

    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return isinstance(member, (ChatMemberOwner, ChatMemberAdministrator))
    except Exception as e:
        print(f"[ADMIN CHECK ERROR] {e}")
        return False

# ------------------------------------------------------------------
# 🌐 BRAWL STARS API
async def bs_api(path: str):
    url = f"https://api.brawlstars.com/v1{path}"
    headers = {"Authorization": f"Bearer {BS_API_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json(), 200
            return None, resp.status

def enc(tag: str):
    return quote(tag.upper().strip(), safe='')

# ------------------------------------------------------------------
# 🤖 КОМАНДЫ — ДЛЯ ВСЕХ

@dp.message(Command("start"))
async def cmd_start(message: Message):
    admin = await is_admin(message)
    if admin:
        await message.answer(
            "👋 Привет, админ!\n\n"
            "🔒 *Команды для админов:*\n"
            "/getip — узнать IP бота\n"
            "/addclub `#ТЕГ` — добавить клуб\n"
            "/removeclub `#ТЕГ` — удалить клуб\n"
            "/resetclubs — удалить все клубы\n\n"
            "🌐 *Команды для всех:*\n"
            "/clubsinfo — инфа обо всех клубах чата\n"
            "/clubinfo `#ТЕГ` — подробная инфа о клубе\n"
            "/whoami — проверить свой статус",
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "👋 Привет!\n\n"
            "🌐 *Доступные команды:*\n"
            "/clubsinfo — инфа обо всех клубах чата\n"
            "/clubinfo `#ТЕГ` — подробная инфа о клубе\n"
            "/whoami — проверить свой статус",
            parse_mode="Markdown"
        )

@dp.message(Command("whoami"))
async def cmd_whoami(message: Message):
    admin = await is_admin(message)
    status = "✅ Админ" if admin else "❌ Не админ"

    if message.from_user:
        await message.reply(
            f"👤 *Твой статус:* {status}\n"
            f"🆔 ID: `{message.from_user.id}`\n"
            f"📛 Username: @{message.from_user.username or 'нет'}\n"
            f"💬 Тип чата: `{message.chat.type}`\n"
            f"🕵️ sender\\_chat: `{message.sender_chat.id if message.sender_chat else 'None'}`",
            parse_mode="Markdown"
        )
    else:
        await message.reply(
            f"👤 *Твой статус:* {status}\n"
            f"🆔 from\\_user: None\n"
            f"🕵️ sender\\_chat: `{message.sender_chat.id if message.sender_chat else 'None'}`\n"
            f"💬 Тип чата: `{message.chat.type}`",
            parse_mode="Markdown"
        )

@dp.message(Command("clubsinfo"))
async def cmd_clubsinfo(message: Message):
    data = load_data()
    chat_id = str(message.chat.id)

    if chat_id not in data or not data[chat_id]:
        return await message.reply("📭 В этом чате нет отслеживаемых клубов.")

    text = "🏢 *Клубы этого чата:*\n\n"
    for tag, info in data[chat_id].items():
        text += (
            f"🔹 *{info['name']}* (`{tag}`)\n"
            f"   👥 {info['memberCount']}/30 | 🏆 {info['trophies']} | 🚪 {info['requiredTrophies']}\n\n"
        )
    await message.reply(text.strip(), parse_mode="Markdown")

@dp.message(Command("clubinfo"))
async def cmd_clubinfo(message: Message):
    args = message.text.split()
    if len(args) < 2:
        return await message.reply("❗ Укажи тег: `/clubinfo #ТЕГ`", parse_mode="Markdown")

    tag = args[1].upper()
    if not tag.startswith("#"):
        tag = "#" + tag

    club, status = await bs_api(f"/clubs/{enc(tag)}")
    if status == 403:
        return await message.reply("🚫 *Ошибка 403.* Админу нужно обновить IP через `/getip`.", parse_mode="Markdown")
    if not club:
        return await message.reply("❌ Клуб не найден.")

    members_data, _ = await bs_api(f"/clubs/{enc(tag)}/members")
    members = members_data.get("items", []) if members_data else []
    members.sort(key=lambda x: x.get("trophies", 0), reverse=True)

    members_text = "\n".join(
        [f"{i+1}. {m['name']} — {m['trophies']} 🏆" for i, m in enumerate(members)]
    )

    await message.reply(
        f"🏠 *{club['name']}* (`{tag}`)\n"
        f"👥 {club.get('memberCount', 0)}/30 | 🏆 {club.get('trophies', 0)} | 🚪 {club.get('requiredTrophies', 0)}\n\n"
        f"📋 *Состав:*\n{members_text if members_text else 'Нет данных'}",
        parse_mode="Markdown"
    )

# ------------------------------------------------------------------
# 🔒 КОМАНДЫ — ТОЛЬКО ДЛЯ АДМИНОВ

@dp.message(Command("getip"))
async def cmd_getip(message: Message):
    if not await is_admin(message):
        return await message.reply("❗ Только для администраторов.")

    await message.reply("⏳ Узнаю свой IP...")
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.ipify.org?format=json") as resp:
            data = await resp.json()
            ip = data.get("ip")

    await message.answer(
        f"🌐 *Мой IP:* `{ip}`\n\n"
        f"📝 *Что делать:*\n"
        f"1. Зайди на [developer.brawlstars.com](https://developer.brawlstars.com)\n"
        f"2. Создай новый ключ с IP: `{ip}`\n"
        f"3. Скопируй токен и отправь разработчику бота\n"
        f"4. Или замени токен в коде и передеплой",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

@dp.message(Command("addclub"))
async def cmd_addclub(message: Message):
    if not await is_admin(message):
        return await message.reply("❗ Только для администраторов.")

    args = message.text.split()
    if len(args) < 2:
        return await message.reply("❗ Укажи тег: `/addclub #ТЕГ`", parse_mode="Markdown")

    tag = args[1].upper()
    if not tag.startswith("#"):
        tag = "#" + tag

    club, status = await bs_api(f"/clubs/{enc(tag)}")

    if status == 403:
        return await message.reply(
            "🚫 *Ошибка 403 (IP не авторизован)*\n"
            "Напиши `/getip` чтобы узнать IP.",
            parse_mode="Markdown"
        )
    if not club:
        return await message.reply("❌ Клуб не найден. Проверь тег.")

    data = load_data()
    chat_id = str(message.chat.id)
    if chat_id not in data:
        data[chat_id] = {}

    if tag in data[chat_id]:
        return await message.reply(f"ℹ️ Клуб `{tag}` уже отслеживается.", parse_mode="Markdown")

    members_data, _ = await bs_api(f"/clubs/{enc(tag)}/members")
    members = members_data.get("items", []) if members_data else []

    data[chat_id][tag] = {
        "name": club.get("name"),
        "trophies": club.get("trophies", 0),
        "requiredTrophies": club.get("requiredTrophies", 0),
        "memberCount": club.get("memberCount", 0),
        "members": {m["tag"]: m["name"] for m in members}
    }
    save_data(data)

    await message.reply(
        f"✅ Клуб *{club['name']}* добавлен!\n"
        f"👥 {club.get('memberCount', 0)}/30 | 🏆 {club.get('trophies', 0)} | 🚪 {club.get('requiredTrophies', 0)}",
        parse_mode="Markdown"
    )

@dp.message(Command("removeclub"))
async def cmd_removeclub(message: Message):
    if not await is_admin(message):
        return await message.reply("❗ Только для администраторов.")

    args = message.text.split()
    if len(args) < 2:
        return await message.reply("❗ Укажи тег: `/removeclub #ТЕГ`", parse_mode="Markdown")

    tag = args[1].upper()
    if not tag.startswith("#"):
        tag = "#" + tag

    data = load_data()
    chat_id = str(message.chat.id)

    if chat_id in data and tag in data[chat_id]:
        del data[chat_id][tag]
        save_data(data)
        await message.reply(f"✅ Клуб `{tag}` удалён.", parse_mode="Markdown")
    else:
        await message.reply("❌ Этот клуб не отслеживается.")

@dp.message(Command("resetclubs"))
async def cmd_resetclubs(message: Message):
    if not await is_admin(message):
        return await message.reply("❗ Только для администраторов.")

    data = load_data()
    chat_id = str(message.chat.id)

    if chat_id in data and data[chat_id]:
        del data[chat_id]
        save_data(data)
        await message.reply("🔁 Все клубы в этом чате удалены.")
    else:
        await message.reply("📭 Нечего удалять.")

# ------------------------------------------------------------------
# 👁️ ФОНОВЫЙ МОНИТОРИНГ
async def monitor_loop():
    await asyncio.sleep(20)
    while True:
        data = load_data()
        ip_warning_sent = {}

        for chat_id, clubs in data.items():
            for tag, old_info in list(clubs.items()):
                club, status = await bs_api(f"/clubs/{enc(tag)}")

                if status == 403 and chat_id not in ip_warning_sent:
                    try:
                        await bot.send_message(
                            chat_id,
                            "🚨 *IP бота изменился!*\n"
                            "Админ, напиши `/getip` и обнови IP на developer.brawlstars.com",
                            parse_mode="Markdown"
                        )
                        ip_warning_sent[chat_id] = True
                    except:
                        pass
                    continue

                if not club:
                    continue

                members_data, _ = await bs_api(f"/clubs/{enc(tag)}/members")
                new_members = members_data.get("items", []) if members_data else []
                new_members_dict = {m["tag"]: m["name"] for m in new_members}

                changes = []

                if club.get("trophies", 0) != old_info["trophies"]:
                    diff = club["trophies"] - old_info["trophies"]
                    changes.append(f"🏆 Кубки: {old_info['trophies']} ➔ {club['trophies']} ({diff:+})")

                if club.get("requiredTrophies", 0) != old_info["requiredTrophies"]:
                    changes.append(f"🚪 Порог: {old_info['requiredTrophies']} ➔ {club['requiredTrophies']}")

                old_members_dict = old_info.get("members", {})
                joined = set(new_members_dict.keys()) - set(old_members_dict.keys())
                left = set(old_members_dict.keys()) - set(new_members_dict.keys())

                if joined:
                    changes.append(f"📥 *Пришли:* {', '.join([new_members_dict[t] for t in joined])}")
                if left:
                    changes.append(f"📤 *Ушли:* {', '.join([old_members_dict[t] for t in left])}")

                if changes:
                    msg = f"🔄 *Изменения в клубе {club.get('name', tag)}*\n\n" + "\n".join(changes)
                    try:
                        await bot.send_message(chat_id, msg, parse_mode="Markdown")
                    except:
                        pass

                    data[chat_id][tag]["trophies"] = club.get("trophies", 0)
                    data[chat_id][tag]["requiredTrophies"] = club.get("requiredTrophies", 0)
                    data[chat_id][tag]["memberCount"] = club.get("memberCount", 0)
                    data[chat_id][tag]["members"] = new_members_dict
                    save_data(data)

                await asyncio.sleep(1)

        await asyncio.sleep(CHECK_INTERVAL)

# ------------------------------------------------------------------
# 🏓 САМОПИНГ И ВЕБ-СЕРВЕР
async def self_pinger():
    await asyncio.sleep(10)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await session.get(f"{BASE_URL}/ping")
            except:
                pass
            await asyncio.sleep(PING_INTERVAL)

async def handle_ping(request):
    return web.Response(text="pong")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/ping", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"Web server on port {PORT}")

# ------------------------------------------------------------------
# 🚀 ЗАПУСК
async def main():
    asyncio.create_task(run_web_server())
    asyncio.create_task(self_pinger())
    asyncio.create_task(monitor_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
