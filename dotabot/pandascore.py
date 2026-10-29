"""Клиент PandaScore (Dota 2): расписание, результаты, карты серий."""
import asyncio

BASE = "https://api.pandascore.co/dota2"
PAGE = 100


def parse_match(m: dict) -> dict | None:
    """Приводит матч PandaScore к плоскому виду. None, если соперники ещё не известны (TBD)."""
    opp = m.get("opponents") or []
    if len(opp) < 2:
        return None
    t1, t2 = opp[0]["opponent"], opp[1]["opponent"]
    games = []
    for g in m.get("games") or []:
        games.append({
            "id": g["id"],
            "position": g["position"],
            "status": g.get("status"),
            "winner_id": (g.get("winner") or {}).get("id"),
            "played_at": g.get("begin_at"),
        })
    return {
        "id": m["id"],
        "league": (m.get("league") or {}).get("name"),
        "serie": (m.get("serie") or {}).get("full_name") or (m.get("serie") or {}).get("name"),
        "serie_id": m.get("serie_id"),
        "tournament": (m.get("tournament") or {}).get("name"),
        "start_at": m.get("begin_at") or m.get("scheduled_at"),
        "status": m.get("status"),
        "number_of_games": m.get("number_of_games") or 1,
        "team1": {"id": t1["id"], "name": t1["name"]},
        "team2": {"id": t2["id"], "name": t2["name"]},
        "winner_id": m.get("winner_id"),
        "forfeit": bool(m.get("forfeit")),
        "games": games,
    }


class PandaScore:
    def __init__(self, token: str):
        import httpx  # импорт здесь, чтобы ядро тестировалось без сети

        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=30
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: dict) -> list[dict]:
        for attempt in range(5):
            r = await self.client.get(BASE + path, params=params)
            if r.status_code == 429:  # лимит запросов
                await asyncio.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("PandaScore: слишком много 429")

    async def matches_between(self, start_iso: str, end_iso: str,
                              serie_id: int | None = None) -> list[dict]:
        """Матчи, запланированные в окне [start, end] (UTC ISO)."""
        out, page = [], 1
        while True:
            params = {
                "range[scheduled_at]": f"{start_iso},{end_iso}",
                "sort": "scheduled_at",
                "page[size]": PAGE,
                "page[number]": page,
            }
            if serie_id:
                params["filter[serie_id]"] = serie_id
            batch = await self._get("/matches", params)
            out += batch
            if len(batch) < PAGE:
                return out
            page += 1

    async def past_matches(self, pages: int = 1, serie_id: int | None = None) -> list[dict]:
        """Последние завершённые матчи (для истории и обновления результатов)."""
        out = []
        for page in range(1, pages + 1):
            params = {"sort": "-begin_at", "page[size]": PAGE, "page[number]": page}
            if serie_id:
                params["filter[serie_id]"] = serie_id
            batch = await self._get("/matches/past", params)
            out += batch
            if len(batch) < PAGE:
                break
        return out

    async def running_matches(self) -> list[dict]:
        return await self._get("/matches/running", {"page[size]": PAGE})
