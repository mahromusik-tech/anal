"""Показывает серии (турниры) с идущими сейчас матчами и их ID для TOURNAMENT_SERIE_ID."""
import asyncio

from dotabot.config import load_settings
from dotabot.pandascore import PandaScore


async def main() -> None:
    s = load_settings()
    ps = PandaScore(s.pandascore_token)
    try:
        seen = {}
        for m in await ps.running_matches():
            seen[m.get("serie_id")] = f"{(m.get('league') or {}).get('name')} / {(m.get('serie') or {}).get('full_name')}"
        for sid, name in seen.items():
            print(sid, name)
        if not seen:
            print("Сейчас нет идущих матчей. Посмотри ID серии на pandascore.co или в ответе /dota2/matches/upcoming.")
    finally:
        await ps.close()


asyncio.run(main())
