"""Загрузка истории для рейтингов: python -m scripts.backfill 40   (40 страниц ≈ 4000 матчей)"""
import asyncio
import sys

from dotabot import service
from dotabot.config import load_settings
from dotabot.db import connect
from dotabot.pandascore import PandaScore


async def main() -> None:
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    s = load_settings()
    conn = connect(s.db_path)
    ps = PandaScore(s.pandascore_token)
    try:
        n = await service.backfill(ps, conn, pages)
        print(f"Загружено матчей: {n}")
        print("Команд с рейтингом:", conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0])
    finally:
        await ps.close()


asyncio.run(main())
