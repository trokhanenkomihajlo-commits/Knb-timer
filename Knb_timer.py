import asyncio
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import aiosqlite

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = "9207147272:AAHhfTvMw11irSL_XPwvuUayE19ofQf1ccg"
MSK_TZ = ZoneInfo("Europe/Moscow")
DB_NAME = "knb_timers.db"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Хранилище активных асинхронных задач
active_tasks = {}

# --- Блок Базы Данных (SQLite) ---

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER PRIMARY KEY,
                duration INTEGER DEFAULT 15,
                admin_only INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS timers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                start_time TEXT,
                end_time TEXT,
                duration INTEGER,
                status TEXT DEFAULT 'active'
            )
        """)
        await db.commit()

async def get_settings(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT duration, admin_only FROM settings WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"duration": row[0], "admin_only": bool(row[1])}
            return {"duration": 15, "admin_only": True}

async def set_settings(chat_id: int, duration: int, admin_only: bool):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO settings (chat_id, duration, admin_only) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET duration = excluded.duration, admin_only = excluded.admin_only",
            (chat_id, duration, int(admin_only))
        )
        await db.commit()

async def add_timer_to_db(chat_id: int, user_id: int, start_time: datetime, end_time: datetime, duration: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "INSERT INTO timers (chat_id, user_id, start_time, end_time, duration, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (chat_id, user_id, start_time.isoformat(), end_time.isoformat(), duration)
        )
        await db.commit()
        return cursor.lastrowid

async def cancel_timer_in_db(timer_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE timers SET status = 'cancelled' WHERE id = ?", (timer_id,))
        await db.commit()

async def complete_timer_in_db(timer_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE timers SET status = 'completed' WHERE id = ?", (timer_id,))
        await db.commit()

async def get_active_timers(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, user_id, start_time, end_time, duration FROM timers WHERE chat_id = ? AND status = 'active'",
            (chat_id,)
        ) as cursor:
            return await cursor.fetchall()

# --- Вспомогательные функции ---

async def is_admin(chat: types.Chat, user_id: int) -> bool:
    if chat.type == "private":
        return True
    member = await chat.get_member(user_id)
    return member.status in ["administrator", "creator"]

def get_cancel_keyboard(timer_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отменить", callback_data=f"cancel_timer:{timer_id}")
    return builder.as_markup()

# --- Обработчики команд ---

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    await message.reply(
        "👋 Приветствую. Я автоматизированная система управления таймерами для турниров.\n\n"
        "Чтобы увидеть полный список доступных команд и настроек, введите команду: <b>/tphelp</b>"
    )

@dp.message(F.text.in_(["/tphelp", ".тп хелп", ".тп помощь"]))
async def cmd_help(message: types.Message):
    text = (
        "<b>⏱ Навигация и команды:</b>\n\n"
        "<b>Запуск отсчета:</b>\n"
        "• <code>.тп</code> — с текущего времени (длительность из настроек).\n"
        "• <code>.тп 10</code> — с текущего времени на 10 минут.\n"
        "• <code>.тп 18:16</code> — с 18:16 МСК на время из настроек.\n"
        "• <code>.тп 18:16 10</code> — с 18:16 МСК ровно на 10 минут.\n\n"
        "<b>Управление и контроль:</b>\n"
        "• <code>.тп статус</code> — список запущенных таймеров в чате.\n"
        "• <code>.тп стоп</code> — отмена всех активных таймеров.\n"
        "• <code>.тп настройки &lt;мин&gt; &lt;админ: 1/0&gt;</code> — параметры чата (пример: <code>.тп настройки 15 1</code>).\n\n"
        "<i>Ограничение: максимум 2 активных таймера на один чат.</i>"
    )
    await message.reply(text)

@dp.message(F.text.startswith(".тп настройки"))
async def cmd_timer_settings(message: types.Message):
    if not await is_admin(message.chat, message.from_user.id):
        return await message.reply("⛔ Ошибка: Менять настройки могут только администраторы.")

    args = message.text.split()[2:]
    if len(args) != 2:
        return await message.reply("Формат: <code>.тп настройки &lt;минуты&gt; &lt;только_админ: 1/0&gt;</code>")

    try:
        duration = int(args[0])
        admin_only = bool(int(args[1]))
        if duration <= 0:
            raise ValueError
        
        await set_settings(message.chat.id, duration, admin_only)
        await message.reply(f"⚙️ <b>Настройки сохранены:</b>\n• Длительность: <b>{duration} мин.</b>\n• Доступ только админам: <b>{'Да' if admin_only else 'Нет'}</b>")
    except ValueError:
        await message.reply("⛔ Ошибка: Укажите целые положительные числа.")

@dp.message(F.text.in_([".тп статус", ".тп список"]))
async def cmd_timer_status(message: types.Message):
    timers = await get_active_timers(message.chat.id)
    if not timers:
        return await message.reply("ℹ️ В этом чате нет активных таймеров.")

    now_msk = datetime.now(MSK_TZ)
    text = "<b>📊 Активные таймеры:</b>\n\n"
    for t_id, u_id, s_str, e_str, dur in timers:
        end_time = datetime.fromisoformat(e_str)
        rem_sec = int((end_time - now_msk).total_seconds())
        if rem_sec < 0:
            rem_sec = 0
        rem_min = rem_sec // 60
        rem_s = rem_sec % 60
        text += f"⏳ <b>До {end_time.strftime('%H:%M')} МСК</b> | Осталось: <b>{rem_min}м {rem_s}с</b> (ID: {t_id})\n"

    await message.reply(text)

@dp.message(F.text.in_([".тп стоп", ".тп отмена"]))
async def cmd_timer_cancel(message: types.Message):
    settings = await get_settings(message.chat.id)
    if settings["admin_only"] and not await is_admin(message.chat, message.from_user.id):
        return await message.reply("⛔ Ошибка: Отменять таймеры разрешено только администраторам.")

    timers = await get_active_timers(message.chat.id)
    if not timers:
        return await message.reply("ℹ️ В этом чате нет активных таймеров.")

    for t_id, _, _, _, _ in timers:
        await cancel_timer_in_db(t_id)
        if t_id in active_tasks:
            active_tasks[t_id].cancel()

    await message.reply("⚠️ Все активные таймеры в чате успешно отменены.")

@dp.callback_query(F.data.startswith("cancel_timer:"))
async def cb_cancel_timer(callback: types.CallbackQuery):
    timer_id = int(callback.data.split(":")[1])
    settings = await get_settings(callback.message.chat.id)
    
    if settings["admin_only"] and not await is_admin(callback.message.chat, callback.from_user.id):
        return await callback.answer("⛔ Только администраторы могут отменять таймер.", show_alert=True)

    await cancel_timer_in_db(timer_id)
    if timer_id in active_tasks:
        active_tasks[timer_id].cancel()

    await callback.message.edit_text(f"🛑 <b>Таймер №{timer_id} был отменен.</b>")
    await callback.answer("Таймер отменен.")

@dp.message(F.text.startswith(".тп"))
async def cmd_timer_tp(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    settings = await get_settings(chat_id)
    
    # Игнорируем служебные команды
    if message.text.startswith((".тп статус", ".тп список", ".тп стоп", ".тп отмена", ".тп настройки", ".тп хелп", ".тп помощь")):
        return

    if settings["admin_only"] and not await is_admin(message.chat, user_id):
        return await message.reply("⛔ Ошибка: Использование таймера разрешено только администраторам.")

    timers = await get_active_timers(chat_id)
    if len(timers) >= 2:
        return await message.reply("⛔ Ошибка: Лимит (2 таймера) исчерпан. Отмените один через <code>.тп стоп</code>.")

    raw_args = message.text[3:].strip()
    now_msk = datetime.now(MSK_TZ)
    start_time = now_msk
    duration = settings["duration"]

    if raw_args:
        if raw_args.isdigit():
            duration = int(raw_args)
        else:
            match = re.match(r"^(\d{1,2}):(\d{2})(?:\s+(\d+))?$", raw_args)
            if not match:
                return await message.reply("⛔ Неверный формат. Примеры:\n<code>.тп</code>\n<code>.тп 10</code>\n<code>.тп 18:16</code>\n<code>.тп 18:16 10</code>")
            
            h, m, custom_dur = match.groups()
            hour, minute = int(h), int(m)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return await message.reply("⛔ Ошибка: Некорректное время.")
            
            start_time = now_msk.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if custom_dur:
                duration = int(custom_dur)

            # Проверка 1: Прошлое (более 3 часов назад)
            if start_time < now_msk and (now_msk - start_time).total_seconds() > 10800:
                return await message.reply("⛔ Ошибка: Указанное время стартовало слишком давно.")

            # Проверка 2: Будущее (более 1 часа вперед от МСК)
            if start_time > now_msk and (start_time - now_msk).total_seconds() > 3600:
                return await message.reply("⛔ Ошибка: Нельзя ставить время старта ТП более чем на 1 час вперед от текущего времени МСК.")

    end_time = start_time + timedelta(minutes=duration)
    delay_seconds = (end_time - now_msk).total_seconds()

    if delay_seconds <= 0:
        return await message.reply("⛔⚠️ Ошибка: Хуйло ты ебучая. Ты застрял в прошлом блять??? Это время с учетом длительности таймера уже вышло. Пошёл НАХУЙ и вводи актуальные данные!")

    timer_id = await add_timer_to_db(chat_id, user_id, start_time, end_time, duration)
    task = asyncio.create_task(run_timer(timer_id, chat_id, user_id, delay_seconds, end_time))
    active_tasks[timer_id] = task

    user_mention = message.from_user.mention_html()
    
    if start_time < now_msk:
        rem_min = int(delay_seconds) // 60
        rem_sec = int(delay_seconds) % 60
        status_text = f"<i>(ТП начался раньше, осталось: {rem_min}м {rem_sec}с)</i>"
    else:
        status_text = ""

    await message.reply(
        f"⏳ <b>Таймер №{timer_id} запущен!</b> {status_text}\n"
        f"• Инициатор: {user_mention}\n"
        f"• Старт ТП: <b>{start_time.strftime('%H:%M')} МСК</b>\n"
        f"• Длительность: <b>{duration} мин.</b>\n"
        f"🎯 Срабатывание: <b>{end_time.strftime('%H:%M')} МСК</b>",
        reply_markup=get_cancel_keyboard(timer_id)
    )

async def run_timer(timer_id: int, chat_id: int, user_id: int, delay: float, end_time: datetime):
    try:
        await asyncio.sleep(delay)
        await complete_timer_in_db(timer_id)
        
        user_link = f"<a href='tg://user?id={user_id}'>Пользователь</a>"
        await bot.send_message(
            chat_id,
            f"🔔 <b>ВНИМАНИЕ!</b>\n"
            f"Таймер на <b>{end_time.strftime('%H:%M')} МСК</b> сработал!\n"
            f"Запросил: {user_link}"
        )
    except asyncio.CancelledError:
        pass
    finally:
        active_tasks.pop(timer_id, None)

async def restore_timers():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, chat_id, user_id, end_time FROM timers WHERE status = 'active'") as cursor:
            rows = await cursor.fetchall()

    now_msk = datetime.now(MSK_TZ)
    for t_id, chat_id, user_id, e_str in rows:
        end_time = datetime.fromisoformat(e_str)
        delay = (end_time - now_msk).total_seconds()
        
        if delay <= 0:
            await complete_timer_in_db(t_id)
            await bot.send_message(chat_id, f"🔔 <b>[Восстановлено]</b> Таймер на <b>{end_time.strftime('%H:%M')} МСК</b> сработал во время перезагрузки!")
        else:
            task = asyncio.create_task(run_timer(t_id, chat_id, user_id, delay, end_time))
            active_tasks[t_id] = task

async def main():
    await init_db()
    await restore_timers()
    print("Бот успешно запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

