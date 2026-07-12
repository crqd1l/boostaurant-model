"""Telegram-бот официанта. Запуск: python -m bot.main

Токен — только из окружения BOT_TOKEN. В моке он лежал прямо в коде: так делать
нельзя, утёкший токен позволяет угнать бота.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import API_URL, BOT_TOKEN
from bot.handlers import router


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN не задан.\n"
            "Получи токен у @BotFather и запусти так:\n"
            "  BOT_TOKEN=<токен> python -m bot.main"
        )

    logging.info("API: %s", API_URL)

    bot = Bot(
        token=BOT_TOKEN,
        # Форматтеры размечают текст HTML-тегами (<b>, <i>).
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # MemoryStorage: состояние живёт в процессе. Для прототипа достаточно, в проде
    # — Redis, иначе при рестарте бот забудет, у кого какой диалог открыт.
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # skip_updates: не разгребаем то, что накопилось, пока бот лежал.
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
