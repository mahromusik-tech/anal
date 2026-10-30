"""История про-матчей с пиками из OpenDota: python -m scripts.backfill_opendota 300
Каждый запуск докачивает следующие N матчей (от новых к старым). Без ключа ~2000 запросов в сутки;
необязательный ключ: OPENDOTA_KEY в .env."""
import asyncio
import os
import sys

from dotabot import service
from dotabot.config import load_settings
from dotabot.db import connect
from dotabot.opendota import OpenDota, parse_od_match


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    s = load_settings()
    conn = connect(s.db_path)
    od = OpenDota(os.getenv("OPENDOTA_KEY"))
    done = empty = 0
    try:
        patches = {p["id"]: p["name"] for p in await od.patches()}
        service.store_heroes(conn, await od.heroes())
        have = {r[0] for r in conn.execute("SELECT match_id FROM od_done")}
        last = None
        while done < limit:
            batch = await od.pro_matches(last)
            if not batch:
                break
            last = min(b["match_id"] for b in batch)
            for b in batch:
                if b["match_id"] in have:
                    continue
                try:
                    m = await od.match(b["match_id"])
                except Exception as e:
                    print("пропуск", b["match_id"], e)
                    continue
                rows = parse_od_match(m, patches)
                service.store_hist(conn, b["match_id"], rows)
                done += 1
                empty += not rows
                if done % 25 == 0:
                    print(f"  ...{done}")
                await asyncio.sleep(1.1)
                if done >= limit:
                    break
    finally:
        await od.close()
    print(f"Обработано матчей: {done}, без пиков: {empty}")
    if done and empty == done:
        print("Пиков нет ни в одном матче: структура ответа OpenDota другая — пришлите JSON /matches/{id}.")
    n = conn.execute("SELECT COUNT(*) FROM hist_picks").fetchone()[0]
    print("Всего строк в hist_picks:", n)
    r = conn.execute("SELECT patch, COUNT(*)/10 FROM hist_picks GROUP BY patch ORDER BY MAX(start_time) DESC LIMIT 3").fetchall()
    print("Свежие патчи (матчей):", [tuple(x) for x in r])


asyncio.run(main())
