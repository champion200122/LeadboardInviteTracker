import os
import json
import asyncio
import aiohttp
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

# ------------------------------------------------------------------
# Конфигурация из переменных окружения
BOT_TOKEN = os.getenv("8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg")
BS_API_TOKEN = os.getenv("eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6Ijk3NDVhOTdkLWI1NjUtNDZjNi1hYjk2LWQyNzA4ZTYwYzY0ZCIsImlhdCI6MTc3ODUxMTE0OCwic3ViIjoiZGV2ZWxvcGVyLzVkYmMwMDMyLTA4OGYtMTc5ZS01ZWQ5LWZlZTkxNDQ5MjNhNCIsInNjb3BlcyI6WyJicmF3bHN0YXJzIl0sImxpbWl0cyI6W3sidGllciI6ImRldmVsb3Blci9zaWx2ZXIiLCJ0eXBlIjoidGhyb3R0bGluZyJ9LHsiY2lkcnMiOlsiMC4wLjAuMCJdLCJ0eXBlIjoiY2xpZW50In1dfQ.e5A40jmtz88Zx4lzrLQADT3HaABHAdos5gbpZpgoc8hXS41lnVSEOLgSqAJIWxC0a_28xBTDm2eTKOrADM2K9A")
BASE_URL = os.getenv("https://leadboardinvitetracker.onrender.com")          # например https://your-bot.onrender.com
PORT = int(os.getenv("PORT", 8080))       # Render сам задаёт порт
CHECK_INTERVAL = 900                      # 15 минут между проверками (в секундах)
PING_INTERVAL = 600                       # 10 минут самопинга

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Файл для хранения клубов и подписчиков
DATA_FILE = "clubs_data.json"

# ------------------------------------------------------------------
# Работа с данными
def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"clubs": {}}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ------------------------------------------------------------------
# Функции для общения с Brawl Stars API
async def fetch_club_info(tag: str):
    """Получить базовую информацию о клубе по тегу."""
    url = f"https://api.brawlstars.com/v1/clubs/%23{tag.strip('#')}"
    headers = {"Authorization": f"Bearer {BS_API_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            return None

async def fetch_club_members(tag: str):
    """Получить список участников клуба."""
    url = f"https://api.brawlstars.com/v1/clubs/%23{tag.strip('#')}/members"
    headers = {"Authorization": f"Bearer {BS_API_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("items", [])
            return None

# ------------------------------------------------------------------
# Команды бота
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я мониторю клубы Brawl Stars.\n"
        "Доступные команды:\n"
        "/addclub <ТЕГ> – добавить клуб\n"
        "/removeclub <ТЕГ> – удалить клуб\n"
        "/listclubs – список отслеживаемых\n"
        "/clubinfo <ТЕГ> – подробная инфа"
    )

@dp.message(Command("addclub"))
async def cmd_add_club(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply("Укажи тег клуба, например: /addclub #2YV8GQL0Y")
        return
    tag = args[1].strip().lstrip('#')
    club = await fetch_club_info(tag)
    if club is None:
        await message.reply("❌ Не удалось найти клуб. Проверь тег и доступность API.")
        return

    data = load_data()
    if tag in data["clubs"]:
        if message.chat.id not in data["clubs"][tag]["chat_ids"]:
            data["clubs"][tag]["chat_ids"].append(message.chat.id)
            save_data(data)
            await message.reply(f"✅ Чат подписан на уведомления о клубе {club['name']} ({tag})")
        else:
            await message.reply(f"ℹ️ Клуб {club['name']} уже отслеживается в этом чате.")
        return

    # Сохраняем метаданные и начальное состояние
    data["clubs"][tag] = {
        "name": club["name"],
        "chat_ids": [message.chat.id],
        "last_member_count": club["memberCount"],
        "last_trophies": club["trophies"],
        "last_required_trophies": club["requiredTrophies"],
        "last_members": []   # будет обновлено при первой проверке
    }
    save_data(data)
    await message.reply(
        f"✅ Клуб **{club['name']}** добавлен.\n"
        f"Участников: {club['memberCount']}/30\n"
        f"Кубков: {club['trophies']}\n"
        f"Порог входа: {club['requiredTrophies']}",
        parse_mode="Markdown"
    )

@dp.message(Command("removeclub"))
async def cmd_remove_club(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply("Укажи тег клуба: /removeclub #ТЕГ")
        return
    tag = args[1].strip().lstrip('#')
    data = load_data()
    if tag not in data["clubs"]:
        await message.reply("Этот клуб не отслеживается.")
        return
    # Удаляем этот чат из подписчиков
    if message.chat.id in data["clubs"][tag]["chat_ids"]:
        data["clubs"][tag]["chat_ids"].remove(message.chat.id)
    if not data["clubs"][tag]["chat_ids"]:
        del data["clubs"][tag]
    save_data(data)
    await message.reply("✅ Клуб удалён из отслеживания для этого чата.")

@dp.message(Command("listclubs"))
async def cmd_list_clubs(message: Message):
    data = load_data()
    if not data["clubs"]:
        await message.reply("Пока нет отслеживаемых клубов.")
        return
    lines = []
    for tag, info in data["clubs"].items():
        lines.append(
            f"🔹 {info['name']} ({tag})\n"
            f"   👥 {info['last_member_count']}/30 | 🏆 {info['last_trophies']} | 🚪 {info['last_required_trophies']}"
        )
    await message.reply("\n".join(lines), parse_mode="Markdown")

@dp.message(Command("clubinfo"))
async def cmd_club_info(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply("Формат: /clubinfo #ТЕГ")
        return
    tag = args[1].strip().lstrip('#')
    club = await fetch_club_info(tag)
    if club is None:
        await message.reply("❌ Клуб не найден.")
        return
    members = await fetch_club_members(tag)
    if members is None:
        members = []
    # Формируем список участников
    member_lines = []
    for m in sorted(members, key=lambda x: x.get("trophies", 0), reverse=True):
        member_lines.append(f"{m['name']} – {m['trophies']} 🏆")
    text = (
        f"🏠 **{club['name']}**\n"
        f"👥 Участников: {club['memberCount']}/30\n"
        f"🏆 Общих кубков: {club['trophies']}\n"
        f"🚪 Порог входа: {club['requiredTrophies']}\n"
        f"📋 Состав:\n" + ("\n".join(member_lines) if member_lines else "Нет данных")
    )
    await message.reply(text, parse_mode="Markdown")

# ------------------------------------------------------------------
# Мониторинг и уведомления об изменениях
async def check_clubs():
    data = load_data()
    for tag, info in list(data["clubs"].items()):
        club = await fetch_club_info(tag)
        if club is None:
            continue
        # Сравниваем с сохранёнными данными
        changes = []
        if club["memberCount"] != info["last_member_count"]:
            changes.append(f"👥 Участники: {info['last_member_count']} → {club['memberCount']}")
        if club["trophies"] != info["last_trophies"]:
            changes.append(f"🏆 Кубки: {info['last_trophies']} → {club['trophies']}")
        if club["requiredTrophies"] != info["last_required_trophies"]:
            changes.append(f"🚪 Порог: {info['last_required_trophies']} → {club['requiredTrophies']}")

        # Сравниваем участников (имена + кубки) – упрощённо
        old_members = info.get("last_members", [])
        new_members = await fetch_club_members(tag) or []
        old_dict = {m["tag"]: m for m in old_members}
        new_dict = {m["tag"]: m for m in new_members}
        joined = [tag for tag in new_dict if tag not in old_dict]
        left = [tag for tag in old_dict if tag not in new_dict]
        for t in joined:
            changes.append(f"➕ Вошёл: {new_dict[t]['name']} ({new_dict[t]['trophies']}🏆)")
        for t in left:
            changes.append(f"➖ Вышел: {old_dict[t]['name']}")

        if changes:
            for chat_id in info["chat_ids"]:
                try:
                    text = f"🔄 Изменения в клубе **{club['name']}** ({tag}):\n" + "\n".join(changes)
                    await bot.send_message(chat_id, text, parse_mode="Markdown")
                except Exception:
                    pass

        # Обновляем сохранённое состояние
        info["last_member_count"] = club["memberCount"]
        info["last_trophies"] = club["trophies"]
        info["last_required_trophies"] = club["requiredTrophies"]
        info["last_members"] = new_members

    save_data(data)

async def monitor_loop():
    await asyncio.sleep(10)  # небольшой стартовый задел
    while True:
        await check_clubs()
        await asyncio.sleep(CHECK_INTERVAL)

# ------------------------------------------------------------------
# Самопинг (чтобы Render не засыпал)
async def self_ping():
    if not BASE_URL:
        return
    await asyncio.sleep(10)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"{BASE_URL}/ping") as resp:
                    print(f"Ping endpoint status: {resp.status}")
            except Exception as e:
                print(f"Ping failed: {e}")
            await asyncio.sleep(PING_INTERVAL)

# Минимальный веб-сервер для ответа на пинг (и потенциальных вебхуков)
async def handle_ping(request):
    return web.Response(text="pong")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/ping", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Web server started on port {PORT}")

# ------------------------------------------------------------------
# Главная точка входа
async def main():
    # Запускаем фоновые задачи
    asyncio.create_task(self_ping())
    asyncio.create_task(run_web_server())
    asyncio.create_task(monitor_loop())

    # Запускаем поллинг бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
