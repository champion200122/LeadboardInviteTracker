import os
import json
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

# ------------------------------------------------------------------
# 🔑 ТВОИ ТОКЕНЫ (УЖЕ ВСТАВЛЕНЫ)
BOT_TOKEN = "8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg"
# Это ключ от Brawlify, не от Supercell. Он выглядит как длинная рандомная строка
BRawlify_API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6Ijk3NDVhOTdkLWI1NjUtNDZjNi1hYjk2LWQyNzA4ZTYwYzY0ZCIsImlhdCI6MTc3ODUxMTE0OCwic3ViIjoiZGV2ZWxvcGVyLzVkYmMwMDMyLTA4OGYtMTc5ZS01ZWQ5LWZlZTkxNDQ5MjNhNCIsInNjb3BlcyI6WyJicmF3bHN0YXJzIl0sImxpbWl0cyI6W3sidGllciI6ImRldmVsb3Blci9zaWx2ZXIiLCJ0eXBlIjoidGhyb3R0bGluZyJ9LHsiY2lkcnMiOlsiMC4wLjAuMCJdLCJ0eXBlIjoiY2xpZW50In1dfQ.e5A40jmtz88Zx4lzrLQADT3HaABHAdos5gbpZpgoc8hXS41lnVSEOLgSqAJIWxC0a_28xBTDm2eTKOrADM2K9A" 
BASE_URL = "https://leadboardinvitetracker.onrender.com"
PORT = int(os.getenv("PORT", 8080))
CHECK_INTERVAL = 600  # Проверяем каждые 10 минут
PING_INTERVAL = 300  # Пингуем себя каждые 5 минут

# Инициализация
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
DATA_FILE = "clubs_data.json"

# ------------------------------------------------------------------
# РАБОТА С ФАЙЛОМ
def load_data():
    try:
        with open(DATA_FILE, "r") as f: return json.load(f)
    except: return {} # { "chat_id": { "clubs": {"#TAG": {data}} } }

def save_data(data):
    with open(DATA_FILE, "w") as f: json.dump(data, f, indent=2)

# ------------------------------------------------------------------
# BRAWLIFY API
async def get_club_brawlify(tag: str):
    clean_tag = tag.strip("#").upper()
    url = f"https://api.brawlify.com/v1/clubs/%23{clean_tag}"
    headers = {"Authorization": BRawlify_API_KEY} # Brawlify ждет просто ключ, без "Bearer"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            # Если клуб не найден (404) или ключ битый (401/403)
            print(f"Brawlify Error {resp.status} for {tag}")
            return None

# ------------------------------------------------------------------
# КОМАНДЫ
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Бот для мониторинга клубов (Brawlify API).\n/addclub #ТЕГ - добавить\n/myclubs - мои клубы")

@dp.message(Command("addclub"))
async def cmd_add(message: Message):
    args = message.text.split()
    if len(args) < 2: return await message.reply("Укажи тег клуба.")
    
    tag = args[1]
    if not tag.startswith("#"): tag = "#" + tag
    
    club_data = await get_club_brawlify(tag)
    if not club_data:
        return await message.reply("❌ Клуб не найден на Brawlify. Проверь тег.")
    
    data = load_data()
    chat_id = str(message.chat.id)
    
    if chat_id not in data: data[chat_id] = {"clubs": {}}
    if tag in data[chat_id]["clubs"]:
        return await message.reply("ℹ️ Ты уже следишь за этим клубом.")
        
    # Сохраняем начальные данные
    data[chat_id]["clubs"][tag] = {
        "name": club_data["name"],
        "trophies": club_data["trophies"],
        "members": club_data["members"], # Список участников
        "requiredTrophies": club_data["requiredTrophies"]
    }
    save_data(data)
    await message.reply(f"✅ Добавлен клуб **{club_data['name']}**", parse_mode="Markdown")

@dp.message(Command("myclubs"))
async def cmd_list(message: Message):
    data = load_data()
    chat_id = str(message.chat.id)
    if chat_id not in data or not data[chat_id]["clubs"]:
        return await message.reply("У тебя нет отслеживаемых клубов.")
    
    text = "📋 **Твои клубы**:\n"
    for tag, info in data[chat_id]["clubs"].items():
        text += f"• {info['name']} ({tag}) - {info['trophies']} 🏆\n"
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("removeclub"))
async def cmd_remove(message: Message):
    args = message.text.split()
    if len(args) < 2: return await message.reply("Укажи тег: /removeclub #ТЕГ")
    
    tag = args[1]
    if not tag.startswith("#"): tag = "#" + tag
    
    data = load_data()
    chat_id = str(message.chat.id)
    
    if chat_id in data and tag in data[chat_id]["clubs"]:
        del data[chat_id]["clubs"][tag]
        save_data(data)
        await message.reply("✅ Клуб удален.")
    else:
        await message.reply("❌ Ты не следил за этим клубом.")

# ------------------------------------------------------------------
# МОНИТОРИНГ (БЕКГРАУНД)
async def monitor_loop():
    await asyncio.sleep(10) # Ждем старта
    while True:
        data = load_data()
        for chat_id, user_data in data.items():
            for tag, old_info in user_data["clubs"].items():
                # Качаем свежие данные
                new_data = await get_club_brawlify(tag)
                if not new_data: continue
                
                changes = []
                # 1. Проверка кубков
                if new_data["trophies"] != old_info["trophies"]:
                    diff = new_data["trophies"] - old_info["trophies"]
                    changes.append(f"🏆 Кубки: {old_info['trophies']} → {new_data['trophies']} ({diff:+})")
                
                # 2. Проверка участников (пришел/ушел)
                old_members = {m["tag"]: m["name"] for m in old_info["members"]}
                new_members = {m["tag"]: m["name"] for m in new_data["members"]}
                
                joined = set(new_members.keys()) - set(old_members.keys())
                left = set(old_members.keys()) - set(new_members.keys())
                
                if joined: changes.append(f"📥 Пришли: {', '.join([new_members[t] for t in joined])}")
                if left: changes.append(f"📤 Ушли: {', '.join([old_members[t] for t in left])}")
                
                # 3. Если есть изменения - шлем в чат
                if changes:
                    msg = f"🔄 **{new_data['name']}** ({tag}):\n" + "\n".join(changes)
                    try:
                        await bot.send_message(chat_id, msg, parse_mode="Markdown")
                    except: pass # Если бота забанили в чате
                    
                    # Обновляем данные в файле
                    data[chat_id]["clubs"][tag]["trophies"] = new_data["trophies"]
                    data[chat_id]["clubs"][tag]["members"] = new_data["members"]
                    save_data(data)
        
        await asyncio.sleep(CHECK_INTERVAL)

# ------------------------------------------------------------------
# СИСТЕМА НЕСПЯЩЕГО БОТА (KEEP ALIVE)
async def self_pinger():
    await asyncio.sleep(15)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"{BASE_URL}/ping") as resp:
                    print(f"Ping: {resp.status}")
            except: pass
            await asyncio.sleep(PING_INTERVAL)

async def handle_ping(request):
    return web.Response(text="pong")

async def run_web():
    app = web.Application()
    app.router.add_get("/ping", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print("Web server started")

# ------------------------------------------------------------------
# ЗАПУСК
async def main():
    asyncio.create_task(monitor_loop())
    asyncio.create_task(self_pinger())
    asyncio.create_task(run_web())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
