import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice, PreCheckoutQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.client.default import DefaultBotProperties

# ====================== НАСТРОЙКИ ======================
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_LINK = "https://t.me/goal90stat"
LIVE_LINK = "http://t.me/Sp0rtplusbot/sp0rt"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан в переменных окружения!")

if not PROVIDER_TOKEN:
    raise ValueError("❌ PROVIDER_TOKEN не задан в переменных окружения! Для реальных платежей получите токен от платежного провайдера.")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()
DB_NAME = "bot.db"

admin_chat_id = None
admin_message_id = None

# ====================== МОСКОВСКОЕ ВРЕМЯ ======================
MOSCOW_TZ = timezone(timedelta(hours=3))

def moscow_now():
    return datetime.now(MOSCOW_TZ)

def moscow_today():
    return moscow_now().date().isoformat()

# ====================== FSM ======================
class AdminStates(StatesGroup):
    waiting_password = State()
    waiting_match_text = State()
    waiting_match_file = State()
    waiting_support_reply = State()
    waiting_user_search = State()

class UserStates(StatesGroup):
    waiting_support = State()

# ====================== КЛАВИАТУРЫ ======================
def main_keyboard():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Матчи"), KeyboardButton(text="Лимит")],
        [KeyboardButton(text="Канал"), KeyboardButton(text="Live футбол"), KeyboardButton(text="Поддержка")],
        [KeyboardButton(text="Подписка"), KeyboardButton(text="Политика и согласие")]
    ], resize_keyboard=True)
    return kb

def payment_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Silver 2 недели — 100₽", callback_data="sub_silver_14")],
        [InlineKeyboardButton(text="Silver месяц — 200₽", callback_data="sub_silver_28")],
        [InlineKeyboardButton(text="Gold 2 недели — 150₽", callback_data="sub_gold_14")],
        [InlineKeyboardButton(text="Gold месяц — 300₽", callback_data="sub_gold_28")]
    ])

def policy_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Политика конфиденциальности", url="https://telegra.ph/Politika-konfidencialnosti-08-15-17")],
        [InlineKeyboardButton(text="Пользовательское соглашение", url="https://telegra.ph/Polzovatelskoe-soglashenie-08-15-10")]
    ])

async def get_new_tickets_count():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM support_tickets WHERE status = 'new'") as cur:
            return (await cur.fetchone())[0]

async def admin_keyboard():
    count = await get_new_tickets_count()
    support_text = "💬 Поддержка" if count == 0 else f"💬 Поддержка ({count})"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить матч", callback_data="admin_add_match")],
        [InlineKeyboardButton(text="📋 Просмотр слотов", callback_data="admin_view_slots")],
        [InlineKeyboardButton(text="📢 Опубликовать все матчи", callback_data="admin_publish")],
        [InlineKeyboardButton(text="🗑 Очистить все матчи", callback_data="admin_clear_matches")],
        [InlineKeyboardButton(text=support_text, callback_data="admin_support")],
        [InlineKeyboardButton(text="📈 Подписки", callback_data="admin_subscriptions")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")],
    ])

# ====================== СЛОТЫ ======================
async def get_all_slots():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT slot, event_text, is_published FROM matches ORDER BY slot") as cur:
            return await cur.fetchall()

async def slots_keyboard():
    slots_data = await get_all_slots()
    occupied = {row[0]: row[1][:35] + "..." if len(row[1]) > 35 else row[1] for row in slots_data}
    kb = []
    for i in range(1, 21):
        text = f"✅ Слот {i} | {occupied[i]}" if i in occupied else f"□ Слот {i} — свободен"
        kb.append([InlineKeyboardButton(text=text, callback_data=f"add_to_slot_{i}")])
    kb.append([InlineKeyboardButton(text="← В меню админа", callback_data="back_to_admin")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ====================== БАЗА ДАННЫХ ======================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, subscription TEXT DEFAULT 'free', sub_end TEXT, joined_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY AUTOINCREMENT, slot INTEGER UNIQUE, event_text TEXT, file_id TEXT, is_published INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS user_match_access (telegram_id INTEGER, match_id INTEGER, PRIMARY KEY (telegram_id, match_id));
            CREATE TABLE IF NOT EXISTS daily_usage (telegram_id INTEGER, date TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (telegram_id, date));
            CREATE TABLE IF NOT EXISTS support_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER, username TEXT, text TEXT, status TEXT DEFAULT 'new', admin_reply TEXT);
        ''')
        await db.commit()

async def get_users_count():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            return (await cur.fetchone())[0]

# ====================== ДИНАМИЧЕСКОЕ НАЗВАНИЕ БОТА ======================
def get_users_declension(count: int) -> str:
    """Правильное склонение слова "пользователь" """
    if count % 10 == 1 and count % 100 != 11:
        return "пользователь"
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "пользователя"
    return "пользователей"


async def update_bot_name():
    """Обновляет название бота (отображается в шапке чата у всех пользователей)"""
    try:
        count = await get_users_count()
        word = get_users_declension(count)
        new_name = f"Нейроаналитик 🤖 ({count} {word})"

        await bot.set_my_name(new_name, language_code="ru")
        logging.info(f"✅ Название бота обновлено: {new_name}")
    except Exception as e:
        logging.error(f"❌ Ошибка обновления названия бота: {e}")

# ====================== ЛИМИТЫ (по Москве) ======================
def get_max_matches(sub_type: str, weekday: int) -> int:
    if sub_type == "gold_28": return [5,5,5,5,10,20,20][weekday]
    if sub_type == "gold_14":   return [5,5,5,5,8,17,17][weekday]
    if sub_type == "silver_28": return [3,3,3,3,5,12,12][weekday]
    if sub_type == "silver_14": return [3,3,3,3,4,10,10][weekday]
    return [1,1,1,1,2,2,2][weekday]

def get_sub_name(sub_type: str) -> str:
    names = {
        "free": "Free (бесплатно)",
        "silver_14": "Silver • 2 недели",
        "silver_28": "Silver • 1 месяц",
        "gold_14": "Gold • 2 недели",
        "gold_28": "Gold • 1 месяц"
    }
    return names.get(sub_type, sub_type)

async def get_subscription(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT subscription, sub_end FROM users WHERE telegram_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return "free", None
            sub, end = row
            now = moscow_now().replace(tzinfo=None)
            if end and datetime.fromisoformat(end) < now:
                await db.execute("UPDATE users SET subscription='free', sub_end=NULL WHERE telegram_id=?", (user_id,))
                await db.commit()
                try:
                    await bot.send_message(user_id, "❌ <b>Ваша подписка закончилась</b>\n\nТеперь у вас тариф <b>Free</b> (бесплатно).")
                except: pass
                return "free", None
            return sub, end

# ====================== ДНЕВНОЙ СЧЁТЧИК (по Москве) ======================
async def get_daily_count(user_id: int) -> int:
    today = moscow_today()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT count FROM daily_usage WHERE telegram_id=? AND date=?", (user_id, today)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def increment_daily(user_id: int):
    today = moscow_today()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO daily_usage (telegram_id, date, count)
            VALUES (?, ?, COALESCE((SELECT count + 1 FROM daily_usage WHERE telegram_id=? AND date=?), 1))
        """, (user_id, today, user_id, today))
        await db.commit()

async def get_sub_counts():
    now_str = moscow_now().replace(tzinfo=None).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        subs = ['silver_14', 'silver_28', 'gold_14', 'gold_28']
        counts = {}
        for sub in subs:
            async with db.execute("SELECT COUNT(*) FROM users WHERE subscription = ? AND (sub_end > ? OR sub_end IS NULL)", (sub, now_str)) as cur:
                counts[sub] = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE subscription = 'free' OR sub_end <= ?", (now_str,)) as cur:
            counts['free'] = (await cur.fetchone())[0]
    return counts

async def get_all_processed_users():
    now_str = moscow_now().replace(tzinfo=None).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT telegram_id, username, subscription, sub_end FROM users ORDER BY telegram_id") as cur:
            rows = await cur.fetchall()
        processed = []
        for uid, uname, sub, end in rows:
            if end and end < now_str:
                sub = 'free'
                await db.execute("UPDATE users SET subscription='free', sub_end=NULL WHERE telegram_id=?", (uid,))
            processed.append((uid, uname or 'none', sub))
        await db.commit()
    return processed

async def get_matching_users(search: str):
    now_str = moscow_now().replace(tzinfo=None).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT telegram_id, username, subscription, sub_end FROM users WHERE username LIKE ? ORDER BY telegram_id", (f"%{search}%",)) as cur:
            rows = await cur.fetchall()
        processed = []
        for uid, uname, sub, end in rows:
            if end and end < now_str:
                sub = 'free'
                await db.execute("UPDATE users SET subscription='free', sub_end=NULL WHERE telegram_id=?", (uid,))
            processed.append((uid, uname or 'none', sub))
        await db.commit()
    return processed

# ====================== СТАРТ ======================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT subscription FROM users WHERE telegram_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            # new user, give gift
            sub_type = "silver_28"
            until = (moscow_now() + timedelta(days=5)).replace(tzinfo=None).isoformat()
            await db.execute("INSERT INTO users (telegram_id, username, subscription, sub_end) VALUES (?, ?, ?, ?)", (user_id, username, sub_type, until))
            await message.answer("🎁 Добро пожаловать! Вы получили подарок от администрации: Silver месяц на 5 дней!")
        else:
            # existing user, update username
            await db.execute("UPDATE users SET username=? WHERE telegram_id=?", (username, user_id))
        await db.commit()

    await update_bot_name()  # ← Обновляем название бота при каждом /start

    text = f"""👋 Привет я Нейроаналитик 🤖

Собираю футбольную статистику из спортивных ресурсов. 
Травмы, офиц. матчи, положение в турнирной таблице, забитые - пропущенные мячи, моменты xG. 
Анализируя всю статистику предлагаю варианты с наиболее успешным исходом."""
    
    await message.answer(text, reply_markup=main_keyboard())

# ====================== АДМИН ======================
@dp.message(Command("gol"))
async def admin_login(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_password)
    await message.answer("Введите пароль администратора:")

@dp.message(AdminStates.waiting_password)
async def check_admin_pass(message: Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        await state.clear()
        msg = await message.answer("✅ Добро пожаловать в админ-панель!", reply_markup=await admin_keyboard())
        global admin_chat_id, admin_message_id
        admin_chat_id = msg.chat.id
        admin_message_id = msg.message_id
    else:
        await message.answer("❌ Неверный пароль!")

@dp.callback_query(F.data == "admin_subscriptions")
async def admin_subscriptions(callback: CallbackQuery):
    counts = await get_sub_counts()
    text = "<b>Подписки:</b>\n"
    for sub in ['silver_14', 'silver_28', 'gold_14', 'gold_28']:
        text += f"{get_sub_name(sub)}: {counts.get(sub, 0)}\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Показать всех пользователей", callback_data="show_all_users")],
        [InlineKeyboardButton(text="Поиск по @ник", callback_data="admin_search_user")],
        [InlineKeyboardButton(text="← В меню админа", callback_data="back_to_admin")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "show_all_users")
async def show_all_users(callback: CallbackQuery):
    users = await get_all_processed_users()
    text = "<b>Все пользователи:</b>\nВыберите для управления:"
    kb = []
    for uid, uname, sub in users:
        btn_text = f"{uid} @{uname} ({get_sub_name(sub)})"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"manage_user_{uid}")])
    kb.append([InlineKeyboardButton(text="← К подпискам", callback_data="admin_subscriptions")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "admin_search_user")
async def admin_search_user(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_user_search)
    await callback.message.edit_text("Введите @ник для поиска (без @):")

@dp.message(AdminStates.waiting_user_search)
async def process_user_search(message: Message, state: FSMContext):
    search = message.text.strip()
    await state.clear()
    users = await get_matching_users(search)
    text = f"<b>Результаты поиска '{search}':</b>\nВыберите для управления:"
    kb = []
    for uid, uname, sub in users:
        btn_text = f"{uid} @{uname} ({get_sub_name(sub)})"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"manage_user_{uid}")])
    kb.append([InlineKeyboardButton(text="← К подпискам", callback_data="admin_subscriptions")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("manage_user_"))
async def manage_user(callback: CallbackQuery):
    uid = int(callback.data.split("_")[-1])
    sub, end = await get_subscription(uid)
    text = f"<b>Пользователь ID: {uid}</b>\nТекущая подписка: {get_sub_name(sub)}"
    if end:
        text += f"\nИстекает: {datetime.fromisoformat(end).strftime('%Y-%m-%d %H:%M')}"
    kb = [
        [InlineKeyboardButton(text="Подарить Silver 2 нед", callback_data=f"gift_{uid}_silver_14")],
        [InlineKeyboardButton(text="Подарить Silver месяц", callback_data=f"gift_{uid}_silver_28")],
        [InlineKeyboardButton(text="Подарить Gold 2 нед", callback_data=f"gift_{uid}_gold_14")],
        [InlineKeyboardButton(text="Подарить Gold месяц", callback_data=f"gift_{uid}_gold_28")],
        [InlineKeyboardButton(text="Удалить подписку", callback_data=f"remove_sub_{uid}")],
        [InlineKeyboardButton(text="← К подпискам", callback_data="admin_subscriptions")]
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("gift_"))
async def gift_subscription(callback: CallbackQuery):
    parts = callback.data.split("_")
    uid = int(parts[1])
    sub_type = "_".join(parts[2:])
    current_sub, current_end = await get_subscription(uid)
    days = 14 if "14" in sub_type else 28
    if current_sub == sub_type and current_end:
        start_date = datetime.fromisoformat(current_end)
    else:
        start_date = moscow_now().replace(tzinfo=None)
    until = (start_date + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET subscription=?, sub_end=? WHERE telegram_id=?", (sub_type, until, uid))
        await db.commit()
    try:
        await bot.send_message(uid, f"🎁 Администратор подарил вам подписку <b>{get_sub_name(sub_type)}</b> на {days} дней!")
    except:
        pass
    await callback.answer("Подписка подарена!")
    await callback.message.edit_text("✅ Подписка подарена!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← К подпискам", callback_data="admin_subscriptions")]]))

@dp.callback_query(F.data.startswith("remove_sub_"))
async def remove_subscription(callback: CallbackQuery):
    uid = int(callback.data.split("_")[-1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET subscription='free', sub_end=NULL WHERE telegram_id=?", (uid,))
        await db.commit()
    try:
        await bot.send_message(uid, "❌ Ваша подписка была удалена администратором.")
    except:
        pass
    await callback.answer("Подписка удалена!")
    await callback.message.edit_text("✅ Подписка удалена!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← К подпискам", callback_data="admin_subscriptions")]]))

@dp.callback_query(F.data == "admin_close")
async def admin_close(callback: CallbackQuery):
    await callback.message.delete()

# ====================== ДОБАВЛЕНИЕ МАТЧЕЙ ======================
@dp.callback_query(F.data == "admin_add_match")
async def admin_add_match(callback: CallbackQuery):
    await callback.message.edit_text("<b>Выберите слот для нового матча (1-20):</b>", reply_markup=await slots_keyboard())

@dp.callback_query(F.data.startswith("add_to_slot_"))
async def select_slot_for_match(callback: CallbackQuery, state: FSMContext):
    slot = int(callback.data.split("_")[-1])
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM matches WHERE slot=?", (slot,)) as cur:
            if await cur.fetchone():
                await callback.answer("❌ Этот слот уже занят!", show_alert=True)
                return
    await state.update_data(slot=slot)
    await state.set_state(AdminStates.waiting_match_text)
    await callback.message.edit_text(f"✅ Выбран слот <b>{slot}</b>\n\nОтправьте текст матча:")

@dp.message(AdminStates.waiting_match_text)
async def save_match_text(message: Message, state: FSMContext):
    await state.update_data(event_text=message.text)
    await state.set_state(AdminStates.waiting_match_file)
    await message.answer("Теперь отправьте файл (документ):")

@dp.message(AdminStates.waiting_match_file, F.document)
async def save_match_file(message: Message, state: FSMContext):
    data = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO matches (slot, event_text, file_id, is_published) VALUES (?, ?, ?, 0)",
                         (data["slot"], data["event_text"], message.document.file_id))
        await db.commit()
    await state.clear()
    await message.answer(f"✅ Матч сохранён в <b>Слот {data['slot']}</b>!")
    await message.answer("Админ-панель:", reply_markup=await admin_keyboard())

# ====================== ОЧИСТКА МАТЧЕЙ ======================
@dp.callback_query(F.data == "admin_clear_matches")
async def admin_clear_confirm(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, очистить ВСЁ", callback_data="confirm_clear_all")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_admin")]
    ])
    await callback.message.edit_text("<b>⚠️ ВНИМАНИЕ!</b>\n\nВы действительно хотите удалить все матчи?", reply_markup=kb)

@dp.callback_query(F.data == "confirm_clear_all")
async def confirm_clear_all_matches(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM matches")
        await db.execute("DELETE FROM user_match_access")
        await db.commit()
    await callback.message.edit_text("✅ Все матчи успешно очищены!", reply_markup=await admin_keyboard())

@dp.callback_query(F.data == "admin_publish")
async def publish_matches(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE matches SET is_published = 1 WHERE is_published = 0")
        await db.commit()
    await callback.message.edit_text("✅ Все матчи опубликованы!")

@dp.callback_query(F.data == "admin_view_slots")
async def admin_view_slots(callback: CallbackQuery):
    slots = await get_all_slots()
    text = "<b>📋 Все слоты (1-20):</b>\n\n"
    for i in range(1, 21):
        found = next((s for s in slots if s[0] == i), None)
        if found:
            status = "✅ опубликован" if found[2] else "⏳ не опубликован"
            text += f"<b>{i}.</b> {found[1][:60]}...\n   {status}\n\n"
        else:
            text += f"{i}. <i>пусто</i>\n"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="← В меню админа", callback_data="back_to_admin")]]))

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin_menu(callback: CallbackQuery):
    await callback.message.edit_text("✅ Добро пожаловать в админ-панель!", reply_markup=await admin_keyboard())

# ====================== ПОДДЕРЖКА В АДМИНКЕ ======================
@dp.callback_query(F.data == "admin_support")
async def admin_show_support(callback: CallbackQuery):
    await callback.answer()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT id, telegram_id, username, text 
            FROM support_tickets WHERE status = 'new'
        """) as cur:
            rows = await cur.fetchall()

    if not rows:
        await callback.message.answer("✅ Нет новых обращений в поддержку.")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE support_tickets SET status = 'viewed' WHERE status = 'new'")
        await db.commit()

    await callback.message.answer(f"📩 Найдено новых обращений: <b>{len(rows)}</b>")

    for r in rows:
        ticket_id, telegram_id, username, text = r
        user_link = f'<a href="tg://user?id={telegram_id}">@{username}</a>' if username else f'<a href="tg://user?id={telegram_id}">ID {telegram_id}</a>'

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Ответить", callback_data=f"reply_ticket_{ticket_id}")],
            [InlineKeyboardButton(text="✉️ Написать в ЛС", url=f"tg://user?id={telegram_id}")]
        ])
        
        await callback.message.answer(f"Обращение от {user_link}:\n\n{text}", reply_markup=kb)

    if admin_chat_id and admin_message_id:
        try:
            await bot.edit_message_reply_markup(chat_id=admin_chat_id, message_id=admin_message_id, reply_markup=await admin_keyboard())
        except:
            pass

@dp.callback_query(F.data.startswith("reply_ticket_"))
async def start_reply_ticket(callback: CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split("_")[2])
    await state.set_state(AdminStates.waiting_support_reply)
    await state.update_data(ticket_id=ticket_id)
    await callback.message.answer("Введите ответ пользователю:")
    await callback.message.delete()

@dp.message(AdminStates.waiting_support_reply)
async def save_support_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data['ticket_id']
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT telegram_id FROM support_tickets WHERE id = ?", (ticket_id,)) as cur:
            user_id = (await cur.fetchone())[0]
        await db.execute("UPDATE support_tickets SET admin_reply = ?, status = 'replied' WHERE id = ?", (message.text, ticket_id))
        await db.commit()
    await bot.send_message(user_id, f"✅ Ответ от поддержки:\n\n{message.text}")
    await state.clear()
    await message.answer("Ответ успешно отправлен!")

# ====================== МАТЧИ ======================
@dp.message(F.text == "Матчи")
async def show_matches(message: Message):
    sub, _ = await get_subscription(message.from_user.id)
    try:
        member = await bot.get_chat_member(CHANNEL_ID, message.from_user.id)
        subscribed = member.status in ["member", "administrator", "creator"]
    except:
        subscribed = False

    if sub == "free" and not subscribed:
        await message.answer(f"Чтобы смотреть матчи — подпишись на канал 👇\n{CHANNEL_LINK}")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, slot, event_text FROM matches WHERE is_published=1 ORDER BY slot") as cur:
            rows = await cur.fetchall()

    if not rows:
        await message.answer("Пока нет опубликованных матчей.")
        return

    kb = []
    for mid, slot, text in rows:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT 1 FROM user_match_access WHERE telegram_id=? AND match_id=?", (message.from_user.id, mid)) as cur:
                already = await cur.fetchone()
        display = f"{slot}. {text}"
        kb.append([InlineKeyboardButton(text=f"✅ {display}" if already else display,
                                        callback_data=f"match_{mid}" if not already else f"already_{mid}")])
    await message.answer("📋 Выберите матч по слоту:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("match_"))
async def give_match_file(callback: CallbackQuery):
    match_id = int(callback.data.split("_")[1])
    sub, _ = await get_subscription(callback.from_user.id)
    weekday = moscow_now().weekday()
    max_m = get_max_matches(sub, weekday)
    opened = await get_daily_count(callback.from_user.id)

    if opened >= max_m:
        await callback.answer("⚠️ Лимит на сегодня исчерпан. Приобретите -> 💎 подписку!", show_alert=True)
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT file_id FROM matches WHERE id=?", (match_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await callback.answer("Файл не найден")
            return
        file_id = row[0]

        cur = await db.execute("INSERT OR IGNORE INTO user_match_access (telegram_id, match_id) VALUES (?, ?)", 
                         (callback.from_user.id, match_id))
        if cur.rowcount == 0:
            await callback.answer("⚠️ Вы уже получили файл этого матча.", show_alert=True)
            return
        await db.commit()

    await increment_daily(callback.from_user.id)
    await callback.message.answer_document(file_id, caption="📊 Анализ и прогноз от Нейроаналитика")
    await callback.answer("✅ Файл отправлен!")

@dp.callback_query(F.data.startswith("already_"))
async def already_accessed(callback: CallbackQuery):
    await callback.answer("⚠️ Вы уже получили файл этого матча.")

# ====================== ПЛАТЕЖИ ======================
@dp.message(F.text == "Подписка")
async def show_sub_menu(message: Message):
    await message.answer("Выберите подписку ниже 👇\n\n<b>Silver - 2 недели </b> 72 матча\n<b>Silver - месяц</b> 164 матча \n<b>Gold - 2 недели</b> 124 матча \n<b>Gold - месяц</b> 280 матчей\n\nПосле оплаты лимиты увеличатся автоматически!", reply_markup=payment_keyboard())

@dp.callback_query(F.data.startswith("sub_"))
async def create_invoice(callback: CallbackQuery):
    plan = callback.data
    prices = {
        "sub_silver_14": ("Silver 2 недели", 10000),
        "sub_silver_28": ("Silver месяц", 20000),
        "sub_gold_14": ("Gold 2 недели", 15000),
        "sub_gold_28": ("Gold месяц", 30000)
    }
    title, amount = prices.get(plan, ("Подписка", 10000))

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=title,
        description=f"Подписка {title}",
        payload=plan,
        provider_token=PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label=title, amount=amount)]
    )

@dp.pre_checkout_query()
async def pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def payment_success(message: Message):
    payload = message.successful_payment.invoice_payload
    sub_type = payload.removeprefix("sub_")
    days = 14 if "14" in sub_type else 28
    until = (moscow_now() + timedelta(days=days)).replace(tzinfo=None).isoformat()
    user_id = message.from_user.id
    username = message.from_user.username
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("UPDATE users SET subscription=?, sub_end=? WHERE telegram_id=?", (sub_type, until, user_id))
        if cur.rowcount == 0:
            await db.execute("INSERT INTO users (telegram_id, username, subscription, sub_end) VALUES (?, ?, ?, ?)", (user_id, username, sub_type, until))
        await db.commit()

    await message.answer(f"✅ Подписка <b>{get_sub_name(sub_type)}</b> активирована на {days} дней!\nТеперь у тебя повышенные лимиты")

# ====================== ОСТАЛЬНЫЕ КНОПКИ ======================
@dp.message(F.text == "Канал")
async def send_channel(message: Message):
    await message.answer(f"Подпишись на наш канал чтобы быть в курсе событий: {CHANNEL_LINK}")

@dp.message(F.text == "Лимит")
async def show_limits(message: Message):
    sub, end = await get_subscription(message.from_user.id)
    weekday = moscow_now().weekday()
    max_m = get_max_matches(sub, weekday)
    opened = await get_daily_count(message.from_user.id)
    text = f"Ваша подписка: <b>{get_sub_name(sub)}</b>\n"
    if end:
        text += f"Истекает: {datetime.fromisoformat(end).strftime('%Y-%m-%d %H:%M')}\n"
    text += f"Лимит матчей на сегодня: {max_m}\nИспользовано сегодня: {opened}"

    days = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
    limits_list = [get_max_matches(sub, i) for i in range(7)]
    groups = []
    current_start = 0
    current_val = limits_list[0]
    for i in range(1, 7):
        if limits_list[i] != current_val:
            group_days = days[current_start] if current_start == i - 1 else f"{days[current_start]}-{days[i - 1]}"
            groups.append(f"{group_days} ~ {current_val}")
            current_start = i
            current_val = limits_list[i]
    group_days = days[current_start] if current_start == 6 else f"{days[current_start]}-{days[6]}"
    groups.append(f"{group_days} ~ {current_val}")
    text += "\n\nПо дням: " + ", ".join(groups)

    await message.answer(text)

@dp.message(F.text == "Live футбол")
async def send_live(message: Message):
    await message.answer("Live футбол:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Смотреть Live", url=LIVE_LINK)]]))

@dp.message(F.text == "Политика и согласие")
async def policy(message: Message):
    await message.answer("Выберите документ:", reply_markup=policy_keyboard())

# ====================== ПОДДЕРЖКА (пользователь) ======================
@dp.message(F.text == "Поддержка")
async def start_support(message: Message, state: FSMContext):
    await state.set_state(UserStates.waiting_support)
    await message.answer("Напишите сообщение в поддержку:")

@dp.message(UserStates.waiting_support)
async def save_support(message: Message, state: FSMContext):
    username = message.from_user.username
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("INSERT INTO support_tickets (telegram_id, username, text) VALUES (?, ?, ?)",
                               (message.from_user.id, username, message.text))
        await db.commit()
    if ADMIN_ID:
        await bot.send_message(int(ADMIN_ID), f"Новое обращение от {message.from_user.id} (@{username or 'нет'}): {message.text}")
    await state.clear()
    await message.answer("✅ Сообщение отправлено в поддержку!")

# ====================== ЗАПУСК ======================
async def main():
    await init_db()
    
    # Первичное обновление названия при запуске бота
    await update_bot_name()
    
    # Автообновление каждые 30 минут (на случай изменений в БД вручную)
    scheduler.add_job(update_bot_name, 'interval', minutes=30)
    
    scheduler.start()
    logging.info("🚀 Бот запущен (время Москвы)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
