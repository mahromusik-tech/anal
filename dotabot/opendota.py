"""Клиент OpenDota: история про-матчей с пиками (бесплатно, без ключа ~60 запр/мин, ~2000/сутки)."""
import asyncio

BASE = "https://api.opendota.com/api"


class OpenDota:
    def __init__(self, api_key: str | None = None):
        import httpx

        self.key = api_key
        self.client = httpx.AsyncClient(timeout=30)

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: dict | None = None):
        params = dict(params or {})
        if self.key:
            params["api_key"] = self.key
        for attempt in range(5):
            r = await self.client.get(BASE + path, params=params)
            if r.status_code == 429:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("OpenDota: слишком много 429")

    async def pro_matches(self, less_than: int | None = None) -> list[dict]:
        return await self._get("/proMatches", {"less_than_match_id": less_than} if less_than else None)

    async def match(self, match_id: int) -> dict:
        return await self._get(f"/matches/{match_id}")

    async def heroes(self) -> list[dict]:
        return await self._get("/heroes")

    async def patches(self) -> list[dict]:
        return await self._get("/constants/patch")


def parse_od_match(m: dict, patches: dict) -> list[dict]:
    """Строки hist_picks. Пусто, если матч неполный (нет 10 игроков или победителя)."""
    players = m.get("players") or []
    if len(players) < 10 or m.get("radiant_win") is None:
        return []
    patch = patches.get(m.get("patch")) or str(m.get("patch"))
    out = []
    for p in players:
        if not p.get("hero_id"):
            continue
        radiant = p["isRadiant"] if "isRadiant" in p else p.get("player_slot", 0) < 128
        side = 0 if radiant else 1
        out.append({"match_id": m["match_id"], "side": side, "hero_id": p["hero_id"],
                    "account_id": p.get("account_id"),
                    "won": int(bool(m["radiant_win"]) == (side == 0)),
                    "patch": patch, "start": m.get("start_time")})
    return out
