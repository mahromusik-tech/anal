"""Клиент STRATZ GraphQL (https://api.stratz.com/graphql). Токен бесплатный: https://stratz.com/api (вход через Steam).
Все запросы собраны в начале файла: если схема не совпала, правится только здесь (проверка: scripts/check_stratz.py)."""
import asyncio

URL = "https://api.stratz.com/graphql"
PLAYERS = "players { steamAccountId heroId isRadiant }"
Q_LIVE = "{ live { matches { matchId leagueId radiantTeam { name } direTeam { name } " + PLAYERS + " } } }"
Q_MATCH = "query($id: Long!) { match(id: $id) { id didRadiantWin startDateTime " + PLAYERS + " } }"
Q_LEAGUE = ("query($id: Int!, $skip: Int!) { league(id: $id) { matches(request: {take: 100, skip: $skip}) "
            "{ id didRadiantWin startDateTime " + PLAYERS + " } } }")
Q_LEAGUES = "query($take: Int!) { leagues(request: {take: $take, skip: 0}) { id } }"
Q_HEROES = "{ constants { heroes { id displayName } } }"
QUERIES = {"live": Q_LIVE, "match": Q_MATCH, "league": Q_LEAGUE, "leagues": Q_LEAGUES, "heroes": Q_HEROES}


def _acc(v):
    return v or None


def parse_live(m: dict) -> dict | None:
    """Идущая карта -> {match_id, league_id, rs, ds, rad/dire: {name, heroes[(account_id, hero_id)]}}."""
    if not m or not m.get("matchId"):
        return None
    pl = [p for p in m.get("players") or [] if p.get("heroId")]
    return {"match_id": m["matchId"], "league_id": m.get("leagueId") or (m.get("league") or {}).get("id"),
            "rs": None, "ds": None,
            "rad": {"name": (m.get("radiantTeam") or {}).get("name"),
                    "heroes": [(_acc(p.get("steamAccountId")), p["heroId"]) for p in pl if p.get("isRadiant")]},
            "dire": {"name": (m.get("direTeam") or {}).get("name"),
                     "heroes": [(_acc(p.get("steamAccountId")), p["heroId"]) for p in pl if p.get("isRadiant") is False]}}


def parse_result(m: dict | None) -> dict | None:
    """Итог карты -> {match_id, rows для hist_picks, radiant_win}. None, если данных ещё нет."""
    if not m or m.get("didRadiantWin") is None:
        return None
    rows = []
    for p in m.get("players") or []:
        if not p.get("heroId") or p.get("isRadiant") is None:
            continue
        side = 0 if p["isRadiant"] else 1
        rows.append({"match_id": m["id"], "side": side, "hero_id": p["heroId"],
                     "account_id": _acc(p.get("steamAccountId")),
                     "won": int(bool(m["didRadiantWin"]) == (side == 0)), "start": m.get("startDateTime")})
    return ({"match_id": m["id"], "rows": rows, "radiant_win": bool(m["didRadiantWin"])}
            if len(rows) >= 10 else None)


class Stratz:
    def __init__(self, token: str):
        import httpx

        self.client = httpx.AsyncClient(timeout=60, headers={
            "Authorization": f"Bearer {token}", "User-Agent": "STRATZ_API", "Content-Type": "application/json"})

    async def close(self) -> None:
        await self.client.aclose()

    async def q(self, query: str, variables: dict | None = None) -> dict:
        for attempt in range(4):
            r = await self.client.post(URL, json={"query": query, "variables": variables or {}})
            if r.status_code in (429, 500, 502, 503, 504):
                if attempt < 3:
                    await asyncio.sleep(3 * (attempt + 1))
                continue
            break
        if r.status_code >= 400:
            raise RuntimeError(f"STRATZ HTTP {r.status_code}: {r.text[:150]}")
        body = r.json()
        if body.get("errors"):
            raise RuntimeError("STRATZ: " + "; ".join(e.get("message", "?") for e in body["errors"])[:300])
        return body.get("data") or {}

    async def live(self) -> list[dict]:
        ms = ((await self.q(Q_LIVE)).get("live") or {}).get("matches") or []
        return [x for x in (parse_live(m) for m in ms) if x]

    async def result(self, match_id: int) -> dict | None:
        return parse_result((await self.q(Q_MATCH, {"id": match_id})).get("match"))

    async def league_matches(self, league_id: int, skip: int = 0) -> tuple[int, list[dict]]:
        """(сколько матчей вернула лига, из них с готовым итогом и пиками) — до 100 карт за запрос."""
        ms = ((await self.q(Q_LEAGUE, {"id": league_id, "skip": skip})).get("league") or {}).get("matches") or []
        return len(ms), [x for x in (parse_result(m) for m in ms) if x]

    async def leagues(self, take: int = 50) -> list[int]:
        return [x["id"] for x in (await self.q(Q_LEAGUES, {"take": take})).get("leagues") or []]

    async def heroes(self) -> list[dict]:
        return ((await self.q(Q_HEROES)).get("constants") or {}).get("heroes") or []
