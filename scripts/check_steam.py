"""Диагностика Valve API (ключ в вывод не попадает): python -m scripts.check_steam"""
import asyncio

import httpx

from dotabot.config import load_settings

BASE = "https://api.steampowered.com"
CHECKS = [
    ("GetLiveLeagueGames", "/IDOTA2Match_570/GetLiveLeagueGames/v1/", {}),
    ("GetMatchHistory (публичные)", "/IDOTA2Match_570/GetMatchHistory/v1/", {"matches_requested": 1}),
    ("GetHeroes", "/IEconDOTA2_570/GetHeroes/v1/", {"language": "en_us"}),
    ("GetLeagueInfoList (недокументирован)", "/IDOTA2League_570/GetLeagueInfoList/v1/", {}),
    ("GetLeagueListing (старый)", "/IDOTA2Match_570/GetLeagueListing/v1/", {}),
]


async def main() -> None:
    key = load_settings().steam_key
    if not key:
        raise SystemExit("Нет STEAM_KEY в .env")
    async with httpx.AsyncClient(timeout=30) as c:
        mid = seq = None
        for name, path, p in CHECKS:
            r = await c.get(BASE + path, params={"key": key, **p})
            print(f"{name}: HTTP {r.status_code} | {r.text[:160].replace(key, '***')!r}")
            if "GetMatchHistory" in name and r.status_code == 200:
                ms = r.json().get("result", {}).get("matches") or []
                mid, seq = (ms[0]["match_id"], ms[0].get("match_seq_num")) if ms else (None, None)
        if mid:
            r = await c.get(BASE + "/IDOTA2Match_570/GetMatchDetails/v1/", params={"key": key, "match_id": mid})
            if seq:
                r2 = await c.get(BASE + "/IDOTA2Match_570/GetMatchHistoryBySequenceNum/v1/",
                                 params={"key": key, "start_at_match_seq_num": seq, "matches_requested": 1})
                m = (r2.json().get("result", {}).get("matches") or [{}])[0] if r2.status_code == 200 else {}
                print(f"BySequenceNum({seq}): HTTP {r2.status_code} | match_id совпал: {m.get('match_id') == mid} | "
                      f"players: {len(m.get('players') or [])} | radiant_win: {m.get('radiant_win')} | "
                      f"ключи: {sorted(m)[:12]}")
            print(f"GetMatchDetails({mid}): HTTP {r.status_code} | {r.text[:160].replace(key, '***')!r}")


asyncio.run(main())
