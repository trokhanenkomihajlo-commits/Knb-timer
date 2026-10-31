import asyncio
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# Твой токен
BOT_TOKEN = "9207147272:AAHhfTvMw11irSL_XPwvuUayE19ofQf1ccg"
# Жесткая привязка к московскому времени
MSK_TZ = ZoneInfo("Europe/Moscow")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Словари для хранения данных в памяти (для продакшена переноси в БД)
chat_settings = {}  # Формат: {chat_id: {'duration': 15, 'admin_only': True}}
chat_timers = {}    # Формат: {chat_id: [task1, task2]}

async def is_admin(chat: types.Chat, user_id: int) -> bool:
    """Проверка прав администратора в чате."""
    if chat.type == "private":
        return True
    member = await chat.get_member(user_id)
    return member.status in ["administrator", "creator"]

@dp.message(F.text.startswith(".таймер настройки"))
async def cmd_timer_settings(message: types.Message):
    """Смена настроек. Формат: .таймер настройки <минуты> <только_админ: 1 или 0>"""
    if not await is_admin(message.chat, message.from_user.id):
        return await message.reply("Ошибка: Менять настройки могут только администраторы.")

    args = message.text.split()[2:]
    if len(args) != 2:
        return await message.reply(
            "Неверный формат.\nИспользуй: <code>.таймер настройки &lt;минуты&gt; &lt;только_админ: 1/0&gt;</code>\n"
            "Пример: <code>.таймер настройки 15 1</code>"
        )

    try:
        duration = int(args[0])
        admin_only = bool(int(args[1]))
        if duration <= 0:
            raise ValueError
        
        chat_settings[message.chat.id] = {"duration": duration, "admin_only": admin_only}
        await message.reply(f"Настройки сохранены.\nДлительность по умолчанию: {duration} мин.\nТолько для админов: {'Да' if admin_only else 'Нет'}.")
    except ValueError:
        await message.reply("Ошибка: Минуты и флаг админа должны быть целыми положительными числами.")


@dp.message(F.text.startswith(".таймер тп"))
async def cmd_timer_tp(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    # Получаем настройки чата или ставим дефолт (15 мин, только админы)
    settings = chat_settings.setdefault(chat_id, {"duration": 15, "admin_only": True})
    
    # Блок проверки прав
    if settings["admin_only"] and not await is_admin(message.chat, user_id):
        return await message.reply("Ошибка: Использование таймера разрешено только администраторам.")
        
    # Блок проверки лимитов (очистка завершенных тасок перед проверкой)
    active_tasks = chat_timers.get(chat_id, [])
    active_tasks = [task for task in active_tasks if not task.done()]
    chat_timers[chat_id] = active_tasks
    
    if len(active_tasks) >= 2:
        return await message.reply("Ошибка: В этом чате уже запущен максимальный лимит (2 таймера).")

    # Блок работы со временем
    args = message.text.replace(".таймер тп", "").strip()
    now_msk = datetime.now(MSK_TZ)
    start_time = now_msk
    
    if args:
        match = re.match(r"^(\d{1,2}):(\d{2})$", args)
        if not match:
            return await message.reply("Ошибка: Неверный формат времени. Пиши ЧЧ:ММ (например, 12:34) или отправляй команду без времени.")
            
        hour, minute = map(int, match.groups())
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return await message.reply("Ошибка: Такого времени не существует.")
            
        start_time = now_msk.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # Защита от ошибок ввода прошедшего времени
        if start_time < now_msk:
            return await message.reply(
                f"Ошибка: Время {args} по МСК уже прошло. Текущее время МСК: {now_msk.strftime('%H:%M')}."
            )
            
    end_time = start_time + timedelta(minutes=settings["duration"])
    delay_seconds = (end_time - now_msk).total_seconds()
    
    # Создание асинхронной задачи и запись в лимит
    task = asyncio.create_task(run_timer(chat_id, delay_seconds, end_time))
    chat_timers[chat_id].append(task)
    
    await message.reply(
        f"Таймер принят в работу.\n"
        f"Отсчет стартует в: {start_time.strftime('%H:%M')} (МСК)\n"
        f"Срабатывание: {end_time.strftime('%H:%M')} (МСК)"
    )

async def run_timer(chat_id: int, delay: float, end_time: datetime):
    """Асинхронная функция ожидания."""
    try:
        await asyncio.sleep(delay)
        await bot.send_message(chat_id, f"⚠️ <b>Внимание!</b> Таймер на {end_time.strftime('%H:%M')} сработал!")
    except asyncio.CancelledError:
        pass

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
