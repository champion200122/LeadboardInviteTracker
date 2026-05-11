import os
import json
import asyncio
import aiohttp
from urllib.parse import quote
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ChatMemberAdministrator
from aiohttp import web

# ------------------------------------------------------------------
# ⚙️ КОНФИГУРАЦИЯ
BOT_TOKEN = "8248125855:AAHjxfoCvTXhVh7xdesTXLBiw5ABcQE3uQg"
BS_API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6Ijk3NDVhOTdkLWI1NjUtNDZjNi1hYjk2LWQyNzA4ZTYwYzY0ZCIsImlhdCI6MTc3ODUxMTE0OCwic3ViIjoiZGV2ZWxvcGVyLzVkYmMwMDMyLTA4OGYtMTc5ZS01ZWQ5LWZlZTkxNDQ5MjNhNCIsInNjb3BlcyI6WyJicmF3bHN0YXJzIl0sImxpbWl0cyI6W3sidGllciI6ImRldmVsb3Blci9zaWx2ZXIiLCJ0eXBlIjoidGhyb3R0bGluZyJ9LHsiY2lkcnMiOlsiMC4wLjAuMCJdLCJ0eXBlIjoiY2xpZW50In1dfQ.e5A40jmtz88Zx4lzrLQADT3HaABHAdos5gbpZpgoc8hXS41lnVSEOLgSqAJIWxC0a_28xBTDm2eTKOrADM2K9A"
BASE_URL = "https://leadboardinvitetracker.onrender.com"
PORT = int(os.getenv("PORT", 8080))

CHECK_INTERVAL = 600  # Проверка каждые 10 минут
PING_INTERVAL = 300   # Пинг каждые 5 минут

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
DATA_FILE = "clubs_data.json"

# ------------------------------------------------------------------
# 💾 БАЗА ДАННЫХ (Теперь храним по ЧАТУ, не по ЮЗЕРУ)
def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)

# ------------------------------------------------------------------
# 🛡️ ПРОВЕРКА АДМИНИСТРАТОРА
async def is_admin(message: Message) -> bool:
    if message.chat.type == 'private':
        return True  # В личных сообщениях все считаются админами
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.is_administrator or member.status == 'creator'
    except:
        return False

# ------------------------------------------------------------------
# 🌐 BRAWL STARS API
async def bs_api(path: str):
    """Запрос к официальному API. Возвращает (данные, статус_код)"""
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
# 🤖 КОМАНДЫ

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not await is_admin(message):
        return await message.answer(
            "👋 Привет! Этот бот мониторинга клубов.\n\n"
            "/clubsinfo — посмотреть информацию о клубах этого чата",
            parse_mode="Markdown"
        )
    
    await message.answer(
        "👋 Привет, админ! Я мониторю клубы Brawl Stars для этого чата.\n\n"
        "⚠️ *ВАЖНО:* Напиши `/getip` один раз и добавь IP на developer.brawlstars.com\n\n"
        "*Команды для Админов:*\n"
        "/addclub `#ТЕГ` — добавить клуб в мониторинг\n"
        "/removeclub `#ТЕГ` — удалить клуб из мониторинга\n"
        "/resetclubs — сбросить список клубов в этом чате\n\n"
        "*Для всех участников:*\n"
        "/clubsinfo — показать инфо обо всех клубах\n"
        "/clubinfo `#ТЕГ` — подробная информация о конкретном клубе",
        parse_mode="Markdown"
    )

@dp.message(Command("getip"))
async def cmd_getip(message: Message):
    if not await is_admin(message):
        return await message.answer("❗ Команда доступна только администраторам.", parse_mode="Markdown")
    
    await message.reply("⏳ Узнаю свой IP-адрес...")
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.ipify.org?format=json") as resp:
            data = await resp.json()
            ip = data.get("ip")
            
    await message.answer(
        f"🌐 *Мой текущий IP:* `{ip}`\n\n"
        f"📝 *Что делать:*\n"
        f"1. Зайди на [developer.brawlstars.com](https://developer.brawlstars.com)\n"
        f"2. Нажми *Edit* на своём ключе\n"
        f"3. В поле *Allowed IPs* добавь: `{ip}`\n"
        f"4. Нажми *Save*\n"
        f"5. Теперь добавляй клубы через `/addclub`",
        parse_mode="Markdown", disable_web_page_preview=True
    )

# ─── ДЛЯ ВСЕХ (НЕ ТОЛЬКО АДМИНЫ) ──────────────────────────────────────
@dp.message(Command("clubsinfo"))
async def cmd_clubsinfo(message: Message):
    data = load_data()
    chat_id = str(message.chat.id)
    
    if chat_id not in data or not data[chat_id]:
        return await message.reply("📭 В этом чате пока нет отслеживаемых клубов.", parse_mode="Markdown")
    
    text = "🏢 *Клубы этого чата:*\n\n"
    for tag, info in data[chat_id].items():
        text += (
            f"🔹 *{info['name']}* (`{tag}`)\n"
            f"   👥 {info['memberCount']}/30 | 🏆 {info['trophies']} | 🚪 Порог: {info['requiredTrophies']}\n\n"
        )
    
    await message.reply(text.strip(), parse_mode="Markdown")

@dp.message(Command("clubinfo"))
async def cmd_clubinfo(message: Message):
    args = message.text.split()
    if len(args) < 2:
        return await message.reply("❗ Укажи тег: `/clubinfo #ТЕГ`", parse_mode="Markdown")
    
    tag = args[1].upper()
    if not tag.startswith("#"): tag = "#" + tag
    
    club, status = await bs_api(f"/clubs/{enc(tag)}")
    if status == 403:
        return await message.reply("🚫 *Ошибка 403*. Админу нужно обновить IP через `/getip`.", parse_mode="Markdown")
    if not club:
        return await message.reply("❌ Клуб не найден.")
    
    members_data, _ = await bs_api(f"/clubs/{enc(tag)}/members")
    members = members_data.get("items", []) if members_data else []
    members.sort(key=lambda x: x.get("trophies", 0), reverse=True)
    
    members_text = "\n".join([f"{i+1}. {m['name']} — {m['trophies']} 🏆" for i, m in enumerate(members)])
    
    await message.reply(
        f"🏠 *{club['name']}* (`{tag}`)\n"
        f"👥 {club.get('memberCount', 0)}/30 | 🏆 {club.get('trophies', 0)} | 🚪 {club.get('requiredTrophies', 0)}\n\n"
        f"📋 *Состав:*\n{members_text if members_text else 'Нет данных'}",
        parse_mode="Markdown"
    )

# ─── ТОЛЬКО ДЛЯ АДМИНОВ ──────────────────────────────────────────────
@dp.message(Command("addclub"))
async def cmd_addclub(message: Message):
    if not await is_admin(message):
        return await message.reply("❗ Команда доступна только администраторам.", parse_mode="Markdown")
    
    args = message.text.split()
    if len(args) < 2:
        return await message.reply("❗ Укажи тег: `/addclub #ТЕГ`", parse_mode="Markdown")
    
    tag = args[1].upper()
    if not tag.startswith("#"): tag = "#" + tag
    
    club, status = await bs_api(f"/clubs/{enc(tag)}")
    
    if status == 403:
        return await message.reply("🚫 *Ошибка 403 (IP не авторизован)*\nНапиши `/getip` чтобы узнать IP для white-list.", parse_mode="Markdown")
    if not club:
        return await message.reply(f"❌ Клуб не найден. Проверь тег.")
    
    data = load_data()
    chat_id = str(message.chat.id)
    if chat_id not in data: data[chat_id] = {}
    
    if tag in data[chat_id]:
        return await message.reply(f"ℹ️ Клуб `{tag}` уже отслеживается этим чатом.", parse_mode="Markdown")
    
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
        f"✅ Клуб *{club['name']}* добавлен в общий мониторинг!\n"
        f"👥 {club.get('memberCount', 0)}/30 | 🏆 {club.get('trophies', 0)} | 🚪 Порог: {club.get('requiredTrophies', 0)}",
        parse_mode="Markdown"
    )

@dp.message(Command("removeclub"))
async def cmd_removeclub(message: Message):
    if not await is_admin(message):
        return await message.reply("❗ Команда доступна только администраторам.", parse_mode="Markdown")
    
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
        await message.reply(f"✅ Клуб `{tag}` удалён из общего мониторинга.", parse_mode="Markdown")
    else:
        await message.reply("❌ Такого клуба нет в списке отслеживания.")

@dp.message(Command("resetclubs"))
async def cmd_resetclubs(message: Message):
    if not await is_admin(message):
        return await message.reply("❗ Команда доступна только администраторам.", parse_mode="Markdown")
    
    data = load_data()
    chat_id = str(message.chat.id)
    
    if chat_id in data and data[chat_id]:
        del data[chat_id]
        save_data(data)
        await message.reply("🔁 Все клубы в этом чате были удалены.", parse_mode="Markdown")
    else:
        await message.reply("📭 В этом чате нет отслеживаемых клубов.")

# ------------------------------------------------------------------
# 👁️ ФОНОВЫЙ МОНИТОРИНГ (Проверяет все клубы во всех чатах)
async def monitor_loop():
    await asyncio.sleep(20)
    while True:
        data = load_data()
        ip_warning_sent = {}  # Сохраняем ID чатов, которым уже прислали предупреждение
        
        for chat_id, clubs in data.items():
            for tag, old_info in list(clubs.items()):
                club, status = await bs_api(f"/clubs/{enc(tag)}")
                
                if status == 403 and chat_id not in ip_warning_sent:
                    try:
                        await bot.send_message(chat_id, 
                            "🚨 *ВНИМАНИЕ!* Мой IP-адрес изменился или устарел.\n"
                            "Администратору нужно написать `/getip` и обновить белый список на сайте.",
                            parse_mode="Markdown")
                        ip_warning_sent[chat_id] = True
                    except: pass
                    continue
                    
                if not club: continue
                
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
                
                if joined: changes.append(f"📥 *Пришли:* {', '.join([new_members_dict[t] for t in joined])}")
                if left: changes.append(f"📤 *Ушли:* {', '.join([old_members_dict[t] for t in left])}")
                
                if changes:
                    msg = f"🔄 *Изменения в клубе {club.get('name', tag)}*\n\n" + "\n".join(changes)
                    try: 
                        await bot.send_message(chat_id, msg, parse_mode="Markdown")
                    except: pass
                    
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
            try: await session.get(f"{BASE_URL}/ping")
            except: pass
            await asyncio.sleep(PING_INTERVAL)

async def handle_ping(request): return web.Response(text="pong")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/ping", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

# ------------------------------------------------------------------
# 🚀 ЗАПУСК
async def main():
    asyncio.create_task(run_web_server())
    asyncio.create_task(self_pinger())
    asyncio.create_task(monitor_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
