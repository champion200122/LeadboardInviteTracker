import os
import json
import asyncio
import aiohttp
from urllib.parse import quote
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

# ------------------------------------------------------------------
# ⚙️ КОНФИГУРАЦИЯ
BOT_TOKEN = "8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg"
BASE_URL = "https://leadboardinvitetracker.onrender.com"
PORT = int(os.getenv("PORT", 8080))
CHECK_INTERVAL = 600  # Проверка клубов каждые 10 минут
PING_INTERVAL = 300   # Пинг каждые 5 минут

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
DATA_FILE = "clubs_data.json"

# ------------------------------------------------------------------
# 💾 РАБОТА С БАЗОЙ ДАННЫХ (JSON)
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
# 🌐 BRAWLIFY API (БЕЗ КЛЮЧЕЙ)
async def fetch_brawlify(tag: str):
    """Получает данные клуба и его участников через Brawlify"""
    encoded_tag = quote(tag.upper(), safe='')
    url_club = f"https://api.brawlify.com/v1/clubs/{encoded_tag}"
    url_members = f"https://api.brawlify.com/v1/clubs/{encoded_tag}/members"
    
    # User-Agent нужен, чтобы Cloudflare Brawlify не блокировал обычные скрипты
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BrawlBot/1.0"}
    
    async with aiohttp.ClientSession() as session:
        # 1. Получаем инфу о клубе
        async with session.get(url_club, headers=headers) as resp:
            if resp.status != 200:
                print(f"Brawlify club error {resp.status} for {tag}")
                return None
            club = await resp.json()
            
        # 2. Получаем список участников
        async with session.get(url_members, headers=headers) as resp:
            if resp.status == 200:
                members_data = await resp.json()
                club['members_list'] = members_data.get('items', [])
            else:
                club['members_list'] = []
                
        return club

# ------------------------------------------------------------------
# 🤖 КОМАНДЫ БОТА
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я мониторю клубы Brawl Stars через Brawlify.\n\n"
        "📌 *Команды:*\n"
        "/addclub `#ТЕГ` — добавить клуб для отслеживания\n"
        "/removeclub `#ТЕГ` — удалить клуб\n"
        "/myclubs — показать мои клубы\n"
        "/clubinfo `#ТЕГ` — подробная инфа и состав",
        parse_mode="Markdown"
    )

@dp.message(Command("addclub"))
async def cmd_addclub(message: Message):
    args = message.text.split()
    if len(args) < 2:
        return await message.reply("❗ Укажи тег клуба. Пример: `/addclub #828YYUCY8`", parse_mode="Markdown")
    
    tag = args[1].upper()
    if not tag.startswith("#"): tag = "#" + tag
    
    await message.reply("⏳ Ищу клуб на Brawlify...")
    club = await fetch_brawlify(tag)
    
    if not club:
        return await message.reply("❌ Клуб не найден. Проверь правильность тега.")
    
    data = load_data()
    chat_id = str(message.chat.id)
    
    if chat_id not in data: data[chat_id] = {}
    
    if tag in data[chat_id]:
        return await message.reply(f"ℹ️ Ты уже отслеживаешь клуб *{club['name']}*.", parse_mode="Markdown")
        
    # Сохраняем начальные данные
    data[chat_id][tag] = {
        "name": club.get("name", "Без имени"),
        "trophies": club.get("trophies", 0),
        "requiredTrophies": club.get("requiredTrophies", 0),
        "memberCount": club.get("memberCount", 0),
        "members": {m["tag"]: m["name"] for m in club.get("members_list", [])}
    }
    save_data(data)
    
    await message.reply(
        f"✅ Клуб *{club['name']}* добавлен в мониторинг!\n"
        f"👥 Участников: {club.get('memberCount', 0)}/30\n"
        f"🏆 Кубков: {club.get('trophies', 0)}\n"
        f"🚪 Порог входа: {club.get('requiredTrophies', 0)}",
        parse_mode="Markdown"
    )

@dp.message(Command("myclubs"))
async def cmd_myclubs(message: Message):
    data = load_data()
    chat_id = str(message.chat.id)
    
    if chat_id not in data or not data[chat_id]:
        return await message.reply("📭 У тебя нет отслеживаемых клубов.")
        
    text = "📋 *Твои клубы:*\n\n"
    for tag, info in data[chat_id].items():
        text += f"🔹 *{info['name']}* (`{tag}`)\n   👥 {info['memberCount']}/30 | 🏆 {info['trophies']}\n"
        
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("removeclub"))
async def cmd_removeclub(message: Message):
    args = message.text.split()
    if len(args) < 2:
        return await message.reply("❗ Укажи тег: `/removeclub #ТЕГ`", parse_mode="Markdown")
        
    tag = args[1].upper()
    if not tag.startswith("#"): tag = "#" + tag
    
    data = load_data()
    chat_id = str(message.chat.id)
    
    if chat_id in data and tag in data[chat_id]:
        del data[chat_id][tag]
        save_data(data)
        await message.reply(f"✅ Клуб `{tag}` удален из мониторинга.", parse_mode="Markdown")
    else:
        await message.reply("❌ Ты и так не отслеживаешь этот клуб.")

@dp.message(Command("clubinfo"))
async def cmd_clubinfo(message: Message):
    args = message.text.split()
    if len(args) < 2:
        return await message.reply("❗ Укажи тег: `/clubinfo #ТЕГ`", parse_mode="Markdown")
        
    tag = args[1].upper()
    if not tag.startswith("#"): tag = "#" + tag
    
    club = await fetch_brawlify(tag)
    if not club:
        return await message.reply("❌ Клуб не найден.")
        
    members = club.get("members_list", [])
    members.sort(key=lambda x: x.get("trophies", 0), reverse=True)
    
    members_text = "\n".join([f"{i+1}. {m['name']} — {m['trophies']} 🏆" for i, m in enumerate(members)])
    if not members_text: members_text = "Нет данных"
    
    text = (
        f"🏠 *{club.get('name', 'Без имени')}* (`{tag}`)\n"
        f"📝 {club.get('description', 'Нет описания')}\n"
        f"👥 Участников: {club.get('memberCount', 0)}/30\n"
        f"🏆 Общих кубков: {club.get('trophies', 0)}\n"
        f"🚪 Порог входа: {club.get('requiredTrophies', 0)}\n\n"
        f"📋 *Состав:*\n{members_text}"
    )
    await message.reply(text, parse_mode="Markdown")

# ------------------------------------------------------------------
# 👁️ ФОНОВЫЙ МОНИТОРИНГ ИЗМЕНЕНИЙ
async def monitor_loop():
    await asyncio.sleep(15) # Даем боту запуститься
    while True:
        data = load_data()
        for chat_id, clubs in data.items():
            for tag, old_info in list(clubs.items()):
                new_data = await fetch_brawlify(tag)
                if not new_data: continue
                
                changes = []
                
                # 1. Изменение общих кубков
                new_trophies = new_data.get("trophies", 0)
                if new_trophies != old_info["trophies"]:
                    diff = new_trophies - old_info["trophies"]
                    changes.append(f"🏆 Кубки: {old_info['trophies']} ➔ {new_trophies} ({diff:+})")
                
                # 2. Изменение порога входа
                new_req = new_data.get("requiredTrophies", 0)
                if new_req != old_info["requiredTrophies"]:
                    changes.append(f"🚪 Порог: {old_info['requiredTrophies']} ➔ {new_req}")
                
                # 3. Пришли / Ушли участники
                new_members = {m["tag"]: m["name"] for m in new_data.get("members_list", [])}
                old_members = old_info.get("members", {})
                
                joined = set(new_members.keys()) - set(old_members.keys())
                left = set(old_members.keys()) - set(new_members.keys())
                
                if joined:
                    names = [new_members[t] for t in joined]
                    changes.append(f"📥 *Пришли:* {', '.join(names)}")
                if left:
                    names = [old_members[t] for t in left]
                    changes.append(f"📤 *Ушли:* {', '.join(names)}")
                
                # Отправляем уведомления, если есть изменения
                if changes:
                    msg = f"🔄 *Изменения в клубе {new_data.get('name', tag)}*\n\n" + "\n".join(changes)
                    try:
                        await bot.send_message(chat_id, msg, parse_mode="Markdown")
                    except Exception as e:
                        print(f"Failed to send to {chat_id}: {e}")
                        
                    # Обновляем данные в базе
                    data[chat_id][tag]["trophies"] = new_trophies
                    data[chat_id][tag]["requiredTrophies"] = new_req
                    data[chat_id][tag]["memberCount"] = new_data.get("memberCount", 0)
                    data[chat_id][tag]["members"] = new_members
                    save_data(data)
                
                await asyncio.sleep(2) # Небольшая пауза, чтобы не словить Rate Limit от Brawlify
                
        await asyncio.sleep(CHECK_INTERVAL)

# ------------------------------------------------------------------
# 🏓 СИСТЕМА САМОПИНГА (ЧТОБЫ RENDER НЕ ЗАСЫПАЛ)
async def self_pinger():
    await asyncio.sleep(10)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"{BASE_URL}/ping") as resp:
                    print(f"Self-ping: {resp.status}")
            except Exception as e:
                print(f"Ping error: {e}")
            await asyncio.sleep(PING_INTERVAL)

async def handle_ping(request):
    return web.Response(text="pong")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/ping", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Web server running on port {PORT}")

# ------------------------------------------------------------------
# 🚀 ЗАПУСК
async def main():
    # Запускаем все фоновые задачи
    asyncio.create_task(run_web_server())
    asyncio.create_task(self_pinger())
    asyncio.create_task(monitor_loop())
    
    # Запускаем Telegram бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
