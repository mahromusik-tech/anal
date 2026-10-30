import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotabot import draft, service
from dotabot.db import connect, upsert_match
from dotabot.formatting import match_card
from dotabot.model import expected, series_win_prob
from dotabot.livesync import same_team, sync_live
from dotabot.pandascore import norm_ts, parse_match
from dotabot.stratz import parse_live, parse_result

TZ = ZoneInfo("Europe/Moscow")


def mk(mid, t1, t2, start, status, games, n=3, serie=1, patch="7.38"):
    return {"id": mid, "league": "L", "serie": "S", "serie_id": serie, "tournament": "T",
            "start_at": start, "status": status, "number_of_games": n, "patch": patch,
            "team1": {"id": t1, "name": f"T{t1}"}, "team2": {"id": t2, "name": f"T{t2}"},
            "winner_id": None, "forfeit": False,
            "games": [{"id": mid * 10 + i, "position": i, "status": st, "winner_id": w,
                       "played_at": start} for i, (st, w) in enumerate(games, 1)]}


def history(conn, n=20):
    for i in range(n):
        upsert_match(conn, mk(100 + i, 1, 2, f"2026-10-{1 + i // 2:02d}T1{i % 10}:00:00Z",
                              "finished", [("finished", 1), ("finished", 1)], n=2))
    service.rebuild_ratings(conn)


def picks_for(hs1, hs2):
    return [(1, 10 + h, h) for h in hs1] + [(2, 20 + h, h) for h in hs2]


def test_ratings_predictions_and_settle():
    conn = connect(":memory:")
    history(conn)
    r = {t: conn.execute("SELECT rating FROM ratings WHERE team_id=?", (t,)).fetchone()[0] for t in (1, 2)}
    assert r[1] > 1500 > r[2]

    upsert_match(conn, mk(1, 1, 2, "2026-10-29T15:00:00Z", "running",
                          [("finished", 1), ("not_started", None), ("not_started", None)]))
    s, e = service.day_bounds_utc(datetime(2026, 10, 29, 12, tzinfo=TZ))
    service.ensure_predictions(conn, s, e)
    assert [p[0] for p in conn.execute("SELECT position FROM predictions ORDER BY position")] == [2, 3]
    m = conn.execute("SELECT * FROM matches WHERE id=1").fetchone()
    assert "Серия" in match_card(conn, m, TZ)
    assert "пиков пока нет" in match_card(conn, m, TZ, detail=True)

    upsert_match(conn, mk(1, 1, 2, "2026-10-29T15:00:00Z", "running",
                          [("finished", 1), ("finished", 2), ("not_started", None)]))
    service.settle_predictions(conn)
    assert service.accuracy_report(conn)["n"] == 1


def test_math():
    assert abs(expected(1500, 1500) - 0.5) < 1e-9
    assert abs(series_win_prob(0.5, 2, 2) - 0.5) < 1e-9
    assert abs(series_win_prob(0.6, 2, 2) - 0.648) < 1e-9


def test_parse_match_stable_order_and_ts():
    raw = {"id": 5, "opponents": [{"opponent": {"id": 9, "name": "B"}}, {"opponent": {"id": 3, "name": "A"}}],
           "begin_at": "2026-10-29T15:00:00.000Z", "videogame_version": {"name": "7.38"}, "games": []}
    m = parse_match(raw)
    assert (m["team1"]["id"], m["team2"]["id"]) == (3, 9) and m["patch"] == "7.38"
    assert m["start_at"] == "2026-10-29T15:00:00Z"
    assert norm_ts("2026-10-29T18:00:00+03:00") == "2026-10-29T15:00:00Z"
    assert parse_match({"id": 1, "opponents": []}) is None


def hist(conn, mid, hs1, hs2, won1=1):
    rows = [dict(match_id=mid, side=0, hero_id=h, account_id=10 + h, won=won1, start=mid) for h in hs1]
    rows += [dict(match_id=mid, side=1, hero_id=h, account_id=20 + h, won=1 - won1, start=mid) for h in hs2]
    service.store_hist(conn, mid, rows)


def test_draft_prefers_winning_heroes():
    conn = connect(":memory:")
    history(conn, 4)
    hs1, hs2 = [1, 2, 3, 4, 5], [6, 7, 8, 9, 10]
    for i in range(15):
        hist(conn, 500 + i, hs1, hs2)
    upsert_match(conn, mk(300, 1, 2, "2026-10-29T15:00:00Z", "running", [("running", None)], n=1))
    m = conn.execute("SELECT * FROM matches WHERE id=300").fetchone()
    assert draft.game_draft(conn, m, 1) is None
    service.store_live_picks(conn, 300, 1, picks_for(hs1, hs2))
    d = draft.game_draft(conn, m, 1)
    assert d["delta"] > 0.3 and d["parts"]["counters"] > 0
    service.store_live_picks(conn, 300, 1, picks_for(hs2, hs1))
    assert draft.game_draft(conn, m, 1)["delta"] < -0.3
    assert "по пикам" in match_card(conn, m, TZ, detail=True)
    # честный бэктест: до первого матча данных нет -> delta ~ 0
    assert abs(draft.Stats(conn, before=500).delta([(11, 1)] * 1 + [(12, 2)] * 4, [(26, 6)] * 5)["delta"]) < 1e-9


def test_patch_window():
    conn = connect(":memory:")
    for i in range(30):  # старый патч: герой 1 слабый; новый: сильный
        hist(conn, 600 + i, [1], [6], won1=0 if i < 15 else 1)
    old = draft.Stats(conn).hero(1)
    service.add_patch(conn, "7.40", 615)
    assert draft.Stats(conn).hero(1) > old


def live_raw(mid=777):
    pl = [{"steamAccountId": 10 + h if h <= 5 else 20 + h, "heroId": h, "isRadiant": h <= 5} for h in range(1, 11)]
    return {"matchId": mid, "leagueId": 1, "radiantTeam": {"name": "Alpha"}, "direTeam": {"name": "BETA team"},
            "players": pl}


class FakeApi:
    def __init__(self, live, result):
        self._live, self._result = live, result

    async def live(self):
        return self._live

    async def result(self, mid):
        return self._result


def test_live_sync_learns_from_result():
    conn = connect(":memory:")
    history(conn, 4)
    now = datetime.now(timezone.utc)
    upsert_match(conn, mk(400, 1, 2, now.strftime("%Y-%m-%dT%H:%M:%SZ"), "running", [("running", None)], n=1))
    conn.execute("UPDATE teams SET name='Alpha' WHERE id=1")
    conn.execute("UPDATE teams SET name='Beta' WHERE id=2")
    conn.commit()
    t = int(now.timestamp())
    asyncio.run(sync_live(FakeApi([parse_live(live_raw())], None), conn, now=t))
    assert conn.execute("SELECT COUNT(*) FROM live_picks WHERE match_id=400 AND position=1").fetchone()[0] == 10
    g = conn.execute("SELECT * FROM live_games WHERE match_id=777").fetchone()
    assert g["p_elo"] is not None and g["radiant_is_team1"] == 1 and g["state"] == "live"
    raw = {"id": 777, "didRadiantWin": True, "startDateTime": t, "players": live_raw()["players"]}
    asyncio.run(sync_live(FakeApi([], parse_result(raw)), conn, now=t + 300))
    g = conn.execute("SELECT * FROM live_games WHERE match_id=777").fetchone()
    assert g["state"] == "done" and g["y"] == 1
    assert conn.execute("SELECT COUNT(*) FROM hist_picks WHERE match_id=777").fetchone()[0] == 10
    assert draft.draft_report(conn)["n"] == 1


def test_parsers_and_names():
    assert same_team("Team Spirit", "Spirit") and same_team("PARIVISION", "parivision") and not same_team("Ab", "Abc")
    assert parse_live(live_raw())["rad"]["heroes"][0] == (11, 1) and len(parse_live(live_raw())["dire"]["heroes"]) == 5
    assert parse_result({"id": 1, "didRadiantWin": True, "players": []}) is None
    assert parse_result({"id": 1, "didRadiantWin": None, "players": live_raw()["players"]}) is None
    assert parse_live({"matchId": 5, "players": [{"steamAccountId": 0, "heroId": 3, "isRadiant": True}]})["rad"]["heroes"] == [(None, 3)]


def test_backfill_picks_and_bootstrap_skip():
    conn = connect(":memory:")
    raw = lambda mid: parse_result({"id": mid, "didRadiantWin": True, "startDateTime": mid, "players": live_raw()["players"]})

    class Api:
        async def heroes(self):
            return [{"id": 1, "displayName": "Axe"}]

        async def leagues(self, n):
            return [5]

        async def live(self):
            return []

        async def league_matches(self, lid, skip):
            return (2, [raw(1), raw(2)]) if skip == 0 else (0, [])

    new, seen, n = asyncio.run(service.backfill_picks(Api(), conn, log=lambda *_: None))
    assert (new, seen, n) == (2, 2, 1)
    assert conn.execute("SELECT COUNT(*) FROM hist_picks").fetchone()[0] == 20
    assert conn.execute("SELECT name FROM heroes WHERE id=1").fetchone()[0] == "Axe"
    assert asyncio.run(service.backfill_picks(Api(), conn, log=lambda *_: None))[0] == 0  # повтор ничего не дублирует
