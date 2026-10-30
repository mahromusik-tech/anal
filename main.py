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
from dotabot.livesync import ensure_heroes, sync_live
from dotabot.pandascore import PandaScore
from dotabot.stratz import Stratz


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    s = load_settings()
    conn = connect(s.db_path)
    ps = PandaScore(s.pandascore_token)
    api = Stratz(s.stratz_token) if s.stratz_token else None
    bot = Bot(s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    fails = 0

    async def job() -> None:
        nonlocal fails
        try:
            await service.refresh(ps, conn, datetime.now(s.tz))
            fails = 0
            logging.info("данные обновлены")
        except Exception as e:
            fails += 1
            logging.exception("ошибка обновления")
            if fails == 3 and s.owner_id:  # один раз после 3 подряд
                await bot.send_message(s.owner_id, f"⚠️ Обновление данных падает 3 раза подряд: {e}")

    async def live_job() -> None:
        try:
            await sync_live(api, conn)
        except Exception:
            logging.exception("ошибка live-синхронизации STRATZ")

    async def boot() -> None:
        try:
            await service.bootstrap(ps, api, conn, log=logging.info)
        except Exception:
            logging.exception("ошибка первичной загрузки истории")

    await job()
    boot_task = asyncio.create_task(boot())  # бот отвечает, пока история докачивается
    if api:
        try:
            await ensure_heroes(api, conn)
        except Exception:
            logging.exception("не загрузились имена героев")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(job, "interval", minutes=10, max_instances=1, coalesce=True)
    if api:
        scheduler.add_job(live_job, "interval", seconds=60, max_instances=1, coalesce=True)
    scheduler.start()

    dp = Dispatcher()
    dp.include_router(build_router(conn, ps, s))
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        boot_task.cancel()
        await ps.close()
        if api:
            await api.close()
        await bot.session.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
