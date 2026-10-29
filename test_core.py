"""Офлайн-проверка ядра без сети: python -m tests.test_core"""
from datetime import datetime
from zoneinfo import ZoneInfo

from dotabot import service
from dotabot.db import connect, upsert_match
from dotabot.formatting import match_card
from dotabot.model import expected, series_win_prob


def mk(mid, t1, t2, start, status, games, n=3, serie=1):
    return {"id": mid, "league": "L", "serie": "S", "serie_id": serie, "tournament": "T",
            "start_at": start, "status": status, "number_of_games": n,
            "team1": {"id": t1, "name": f"T{t1}"}, "team2": {"id": t2, "name": f"T{t2}"},
            "winner_id": None, "forfeit": False,
            "games": [{"id": mid * 10 + i, "position": i, "status": st, "winner_id": w,
                       "played_at": start} for i, (st, w) in enumerate(games, 1)]}


def main():
    conn = connect(":memory:")
    # история: команда 1 стабильно бьёт команду 2
    for i in range(20):
        upsert_match(conn, mk(100 + i, 1, 2, f"2026-10-{1 + i // 2:02d}T1{i % 10}:00:00Z",
                              "finished", [("finished", 1), ("finished", 1)], n=2))
    service.rebuild_ratings(conn)
    r1 = conn.execute("SELECT rating FROM ratings WHERE team_id=1").fetchone()[0]
    r2 = conn.execute("SELECT rating FROM ratings WHERE team_id=2").fetchone()[0]
    assert r1 > 1500 > r2, (r1, r2)

    # сегодняшний матч Bo3: первая карта сыграна, вторая не началась
    upsert_match(conn, mk(1, 1, 2, "2026-10-29T15:00:00Z", "running",
                          [("finished", 1), ("not_started", None), ("not_started", None)]))
    tz = ZoneInfo("Europe/Moscow")
    s, e = service.day_bounds_utc(datetime(2026, 10, 29, 12, 0, tzinfo=tz))
    service.ensure_predictions(conn, s, e, 1)
    preds = conn.execute("SELECT position FROM predictions ORDER BY position").fetchall()
    assert [p[0] for p in preds] == [2, 3], preds  # карта 1 уже сыграна, прогноз не создаётся

    print(match_card(conn, conn.execute("SELECT * FROM matches WHERE id=1").fetchone(), tz))

    # карта 2 сыграна -> прогноз закрывается
    upsert_match(conn, mk(1, 1, 2, "2026-10-29T15:00:00Z", "running",
                          [("finished", 1), ("finished", 2), ("not_started", None)]))
    service.settle_predictions(conn)
    rep = service.accuracy_report(conn)
    assert rep and rep["n"] == 1, rep
    print(rep)

    assert abs(expected(1500, 1500) - 0.5) < 1e-9
    assert abs(series_win_prob(0.5, 2, 2) - 0.5) < 1e-9
    print("OK")


main()
