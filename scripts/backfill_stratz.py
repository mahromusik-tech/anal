"""История карт из STRATZ: python -m scripts.backfill_stratz [лиг=50] [страниц_на_лигу=3] [id_лиг_через_запятую]
Один запрос отдаёт до 100 карт лиги сразу с пиками. Повторный запуск докачивает новые. Нужен STRATZ_TOKEN."""
import asyncio
import sys

from dotabot import service
from dotabot.config import load_settings
from dotabot.db import connect
from dotabot.stratz import Stratz


async def main() -> None:
    n_leagues = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    leagues = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else None
    s = load_settings()
    if not s.stratz_token:
        sys.exit("Нет STRATZ_TOKEN в .env")
    conn = connect(s.db_path)
    api = Stratz(s.stratz_token)
    try:
        new, seen, n = await service.backfill_picks(api, conn, n_leagues, pages, leagues)
    finally:
        await api.close()
    print(f"Лиг: {n}, новых карт: {new}, просмотрено с итогом: {seen}")
    if not seen:
        print("Ни одной карты с итогом: запустите python -m scripts.check_stratz и пришлите вывод.")
    print("Карт в базе:", conn.execute("SELECT COUNT(DISTINCT match_id) FROM hist_picks").fetchone()[0])


asyncio.run(main())
