"""История карт из Valve: python -m scripts.backfill_steam [матчей_за_запуск=300] [лиг=200] [id_лиг_через_запятую]
Идёт по самым новым лигам, берёт все карты лиги и их детали. Повторный запуск докачивает следующие.
Нужен STEAM_KEY в .env."""
import asyncio
import sys

from dotabot import service
from dotabot.config import load_settings
from dotabot.db import connect
from dotabot.steam import Steam, parse_details


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    n_leagues = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    s = load_settings()
    if not s.steam_key:
        sys.exit("Нет STEAM_KEY в .env")
    conn = connect(s.db_path)
    st = Steam(s.steam_key)
    done = empty = fails = 0
    try:
        service.store_heroes(conn, await st.heroes())
        if len(sys.argv) > 3:  # лиги заданы вручную
            leagues = [int(x) for x in sys.argv[3].split(",")]
        else:
            leagues = (await st.leagues())[:n_leagues]
            try:  # лиги, у которых прямо сейчас идут матчи
                leagues += [x for x in {g.get("league_id") for g in await st.live_games()} - {None, 0} if x not in leagues]
            except Exception as e:
                print("GetLiveLeagueGames:", e)
            leagues += [r[0] for r in conn.execute("SELECT DISTINCT league_id FROM live_games WHERE league_id IS NOT NULL")
                        if r[0] not in leagues]
        if not leagues:
            print("Список лиг Valve недоступен (GetLeagueListing удалён). Варианты:\n"
                  " 1) передайте id лиг: python -m scripts.backfill_steam 300 200 ID1,ID2\n"
                  "    (id лиги — число в адресе на dotabuff.com/esports/leagues/ID-название)\n"
                  " 2) запустите main.py на время матчей: лиги и карты соберутся сами.")
            return
        print("Лиг к обработке:", len(leagues))
        have = {r[0] for r in conn.execute("SELECT match_id FROM match_done")}
        for lid in leagues:
            start = None
            for _ in range(5):
                res = await st.history(lid, start)
                ms = res.get("matches") or []
                if not ms:
                    break
                for mm in ms:
                    if mm["match_id"] in have or done >= limit:
                        continue
                    try:
                        d = parse_details(await st.match(mm["match_id"], mm.get("match_seq_num")))
                    except Exception as e:
                        fails += 1
                        print("пропуск", mm["match_id"], e)
                        if fails >= 10:
                            print("Детали карт не отдаются (10 ошибок подряд), останавливаюсь.")
                            limit = done
                        continue
                    fails = 0
                    service.store_hist(conn, mm["match_id"], d["rows"] if d else [])
                    have.add(mm["match_id"])
                    done += 1
                    empty += not d
                    if done % 25 == 0:
                        print(f"  ...{done}")
                    await asyncio.sleep(1.0)
                if done >= limit or not res.get("results_remaining"):
                    break
                start = min(mm["match_id"] for mm in ms) - 1
            if done >= limit:
                break
    finally:
        await st.close()
    print(f"Обработано карт: {done}, без данных: {empty}")
    if done and empty == done:
        print("Данных нет ни в одной карте: пришлите ответ GetMatchDetails для одного match_id.")
    print("Карт Valve в базе:", conn.execute("SELECT COUNT(DISTINCT match_id) FROM hist_picks").fetchone()[0])


asyncio.run(main())
