"""Пики сыгранных карт для статистики героев: python -m scripts.backfill_picks 300
Лимит PandaScore (по умолчанию ~1000 запросов/час): запускайте порциями."""
import asyncio
import sys

from dotabot import service
from dotabot.config import load_settings
from dotabot.db import connect
from dotabot.pandascore import PandaScore


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    s = load_settings()
    conn = connect(s.db_path)
    ps = PandaScore(s.pandascore_token)
    try:
        fetched, found = await service.sync_picks(ps, conn, limit=limit)
        print(f"Запрошено карт: {fetched}, с пиками: {found}")
        if fetched and not found:
            print("Пиков нет: либо тариф без деталей карт, либо структура ответа /games/{id} другая — "
                  "проверьте parse_game_picks.")
        print("Всего записей picks:", conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0])
    finally:
        await ps.close()


asyncio.run(main())
