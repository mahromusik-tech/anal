import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from dotabot import service
from dotabot.bot import build_router
from dotabot.config import load_settings
from dotabot.db import connect
from dotabot.pandascore import PandaScore


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    s = load_settings()
    conn = connect(s.db_path)
    ps = PandaScore(s.pandascore_token)

    async def job() -> None:
        try:
            await service.refresh(ps, conn, datetime.now(s.tz), s.serie_id)
            logging.info("данные обновлены")
        except Exception:
            logging.exception("ошибка обновления")

    await job()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(job, "interval", minutes=10)
    scheduler.start()

    bot = Bot(s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(build_router(conn, ps, s))
    try:
        await dp.start_polling(bot)
    finally:
        await ps.close()


if __name__ == "__main__":
    asyncio.run(main())
