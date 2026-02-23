import asyncio

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from config import API_TOKEN, PROXY_URL
import handlers


async def main() -> None:
    session = AiohttpSession(proxy=PROXY_URL)
    bot = Bot(token=API_TOKEN, session=session)

    # Получаем username бота для формирования ссылок
    me = await bot.get_me()
    handlers.set_bot_username(me.username or "")
    print(f"🚀 Бот запущен: @{me.username}")

    await bot.delete_webhook(drop_pending_updates=True)
    await handlers.dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())