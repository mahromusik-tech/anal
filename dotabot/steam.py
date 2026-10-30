"""Клиент Valve Steam Web API (Dota 2) и разбор ответов. Ключ: https://steamcommunity.com/dev/apikey"""
import asyncio

BASE = "https://api.steampowered.com"
PRIVATE = (0, 4294967295)  # скрытый профиль


class Steam:
    def __init__(self, key: str):
        import httpx

        self.key = key
        self.client = httpx.AsyncClient(timeout=30)

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: dict | None = None, retries: int = 4) -> dict:
        """path содержит {v}: пробуем версии адреса, пока не получим не-404. Ключ в ошибки не попадает."""
        params = {"key": self.key, **(params or {})}
        last = None
        for v in ("v1", "v0001", "V001"):
            for attempt in range(retries):
                r = await self.client.get(BASE + path.format(v=v), params=params)
                if r.status_code in (429, 500, 502, 503):
                    if attempt < retries - 1:
                        await asyncio.sleep(3 * (attempt + 1))
                    continue
                break
            if r.status_code == 404:
                last = r.status_code
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Steam API {r.status_code} для {path.format(v=v)}")
            return r.json().get("result", {})
        raise RuntimeError(f"Steam API: адрес {path} не найден ({last}) ни в одной версии")

    async def live_games(self) -> list[dict]:
        return (await self._get("/IDOTA2Match_570/GetLiveLeagueGames/{v}/")).get("games", [])

    async def match(self, match_id: int, seq: int | None = None) -> dict:
        """GetMatchDetails у Valve сломан (500 с патча 7.36), поэтому сначала берём ту же карту через
        GetMatchHistoryBySequenceNum по её match_seq_num; GetMatchDetails — запасной путь."""
        if seq:
            try:
                res = await self._get("/IDOTA2Match_570/GetMatchHistoryBySequenceNum/{v}/",
                                      {"start_at_match_seq_num": seq, "matches_requested": 1}, retries=1)
                for m in res.get("matches") or []:
                    if m.get("match_id") == match_id:
                        return m
            except RuntimeError:
                pass
        return await self._get("/IDOTA2Match_570/GetMatchDetails/{v}/", {"match_id": match_id}, retries=1)

    async def seq_of(self, league_id: int, match_id: int) -> int | None:
        """match_seq_num карты лиги: ищем её в последних матчах лиги (GetMatchHistory)."""
        start = None
        for _ in range(3):
            res = await self.history(league_id, start)
            ms = res.get("matches") or []
            for m in ms:
                if m.get("match_id") == match_id:
                    return m.get("match_seq_num")
            if not ms or not res.get("results_remaining"):
                return None
            start = min(m["match_id"] for m in ms) - 1
        return None

    async def history(self, league_id: int, start_at: int | None = None) -> dict:
        p = {"league_id": league_id, "matches_requested": 100}
        if start_at:
            p["start_at_match_id"] = start_at
        return await self._get("/IDOTA2Match_570/GetMatchHistory/{v}/", p)

    async def leagues(self) -> list[int]:
        """id лиг. GetLeagueListing у Valve удалён; пробуем недокументированный IDOTA2League_570/GetLeagueInfoList
        (формат ответа неизвестен, поэтому id ищутся по ключам league_id/leagueid). Пусто, если недоступно."""
        for path in ("/IDOTA2League_570/GetLeagueInfoList/{v}/", "/IDOTA2Match_570/GetLeagueListing/{v}/"):
            try:
                ids = sorted(set(_find_ids(await self._get(path))), reverse=True)
            except Exception:
                continue
            if ids:
                return ids
        return []

    async def heroes(self) -> list[dict]:
        return (await self._get("/IEconDOTA2_570/GetHeroes/{v}/", {"language": "en_us"})).get("heroes", [])


def _find_ids(o) -> list[int]:
    out: list[int] = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("league_id", "leagueid") and isinstance(v, int):
                out.append(v)
            else:
                out += _find_ids(v)
    elif isinstance(o, list):
        for x in o:
            out += _find_ids(x)
    return out


def _acc(v):
    return None if v in PRIVATE else v


def parse_live(g: dict) -> dict | None:
    """Идущая карта лиги -> {match_id, rad/dire: {name, heroes[(account_id, hero_id)]}, rs, ds}."""
    if not g.get("match_id"):
        return None
    sb = g.get("scoreboard") or {}
    out = {"match_id": g["match_id"], "league_id": g.get("league_id"),
           "rs": g.get("radiant_series_wins"), "ds": g.get("dire_series_wins")}
    for key, team_key, idx in (("rad", "radiant_team", 0), ("dire", "dire_team", 1)):
        side = sb.get("radiant" if idx == 0 else "dire") or {}
        heroes = [(_acc(p.get("account_id")), p["hero_id"]) for p in side.get("players") or [] if p.get("hero_id")]
        if not heroes:  # игра ещё не началась: берём список игроков с выбранными героями
            heroes = [(_acc(p.get("account_id")), p["hero_id"]) for p in g.get("players") or []
                      if p.get("team") == idx and p.get("hero_id")]
        out[key] = {"name": (g.get(team_key) or {}).get("team_name"), "heroes": heroes}
    return out


def parse_details(r: dict) -> dict | None:
    """Итог сыгранной карты -> {rows для hist_picks, radiant_win}. None, если данных ещё нет."""
    if not r or "players" not in r or r.get("radiant_win") is None:
        return None
    rows = []
    for p in r["players"]:
        if not p.get("hero_id"):
            continue
        side = 0 if p.get("player_slot", 0) < 128 else 1
        rows.append({"match_id": r["match_id"], "side": side, "hero_id": p["hero_id"],
                     "account_id": _acc(p.get("account_id")),
                     "won": int(bool(r["radiant_win"]) == (side == 0)), "start": r.get("start_time")})
    return {"rows": rows, "radiant_win": bool(r["radiant_win"])} if len(rows) >= 10 else None
