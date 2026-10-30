"""Клиент PandaScore (Dota 2): расписание, результаты, карты серий, пики."""
import asyncio
from datetime import datetime, timezone

BASE = "https://api.pandascore.co/dota2"
PAGE = 100
ISO = "%Y-%m-%dT%H:%M:%SZ"


def norm_ts(s):
    """Любой ISO-формат -> YYYY-MM-DDTHH:MM:SSZ (UTC) или None."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime(ISO)


def parse_match(m: dict) -> dict | None:
    """None, если соперники ещё не известны (TBD). Команды сортируются по id, чтобы порядок был стабилен."""
    opp = m.get("opponents") or []
    if len(opp) < 2:
        return None
    t1, t2 = sorted((opp[0]["opponent"], opp[1]["opponent"]), key=lambda t: t["id"])
    games = [{
        "id": g["id"], "position": g["position"], "status": g.get("status"),
        "winner_id": (g.get("winner") or {}).get("id"), "played_at": norm_ts(g.get("begin_at")),
    } for g in m.get("games") or []]
    ver = m.get("videogame_version") or {}
    return {
        "id": m["id"],
        "league": (m.get("league") or {}).get("name"),
        "serie": (m.get("serie") or {}).get("full_name") or (m.get("serie") or {}).get("name"),
        "serie_id": m.get("serie_id"),
        "tournament": (m.get("tournament") or {}).get("name"),
        "start_at": norm_ts(m.get("begin_at") or m.get("scheduled_at")),
        "status": m.get("status"),
        "number_of_games": m.get("number_of_games") or 1,
        "team1": {"id": t1["id"], "name": t1["name"]},
        "team2": {"id": t2["id"], "name": t2["name"]},
        "winner_id": m.get("winner_id"),
        "forfeit": bool(m.get("forfeit")),
        "patch": ver.get("name") if isinstance(ver, dict) else None,
        "games": games,
    }


class PandaScore:
    def __init__(self, token: str):
        import httpx  # импорт здесь, чтобы ядро тестировалось без сети

        self.client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: dict | None = None):
        for attempt in range(5):
            r = await self.client.get(BASE + path, params=params)
            if r.status_code == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("PandaScore: слишком много 429")

    async def _paged(self, path: str, params: dict, pages: int = 10**6) -> list[dict]:
        out = []
        for page in range(1, pages + 1):
            batch = await self._get(path, {**params, "page[size]": PAGE, "page[number]": page})
            out += batch
            if len(batch) < PAGE:
                break
        return out

    async def matches_between(self, start_iso: str, end_iso: str) -> list[dict]:
        return await self._paged("/matches", {"range[scheduled_at]": f"{start_iso},{end_iso}",
                                              "sort": "scheduled_at"})

    async def past_matches(self, pages: int = 1) -> list[dict]:
        return await self._paged("/matches/past", {"sort": "-begin_at"}, pages)

    async def running_matches(self) -> list[dict]:
        return await self._paged("/matches/running", {}, 1)

    async def matches_by_ids(self, ids: list[int]) -> list[dict]:
        return await self._paged("/matches", {"filter[id]": ",".join(map(str, ids))}, 1) if ids else []
