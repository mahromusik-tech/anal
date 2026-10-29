"""Синхронизация данных, пересчёт рейтингов, прогнозы, метрики."""
import math
from datetime import datetime, timedelta, timezone

from . import db as dbm
from .model import MODEL_VERSION, START_RATING, expected, update
from .pandascore import PandaScore, parse_match

ISO = "%Y-%m-%dT%H:%M:%SZ"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO)


def day_bounds_utc(now_local: datetime) -> tuple[str, str]:
    """Границы локальных суток [00:00, 24:00) в UTC ISO."""
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    f = lambda d: d.astimezone(timezone.utc).strftime(ISO)
    return f(start), f(end)


def store(conn, raw_matches: list[dict]) -> int:
    n = 0
    for raw in raw_matches:
        m = parse_match(raw)
        if m and m["start_at"]:
            dbm.upsert_match(conn, m)
            n += 1
    return n


def rebuild_ratings(conn) -> None:
    """Полный пересчёт Elo по всем сыгранным картам в хронологическом порядке."""
    rows = conn.execute(
        """SELECT g.winner_id, m.team1_id, m.team2_id
           FROM games g JOIN matches m ON m.id = g.match_id
           WHERE g.status = 'finished' AND g.winner_id IS NOT NULL AND m.forfeit = 0
           ORDER BY g.played_at, g.id"""
    ).fetchall()
    rating: dict[int, float] = {}
    played: dict[int, int] = {}
    for r in rows:
        t1, t2 = r["team1_id"], r["team2_id"]
        if r["winner_id"] not in (t1, t2):
            continue
        r1, r2 = rating.get(t1, START_RATING), rating.get(t2, START_RATING)
        n1, n2 = played.get(t1, 0), played.get(t2, 0)
        rating[t1], rating[t2] = update(r1, n1, r2, n2, r["winner_id"] == t1)
        played[t1], played[t2] = n1 + 1, n2 + 1
    conn.execute("DELETE FROM ratings")
    conn.executemany(
        "INSERT INTO ratings(team_id, rating, games_played) VALUES(?,?,?)",
        [(t, rating[t], played[t]) for t in rating],
    )
    conn.commit()


def get_rating(conn, team_id: int) -> tuple[float, int]:
    r = conn.execute("SELECT rating, games_played FROM ratings WHERE team_id=?",
                     (team_id,)).fetchone()
    return (r["rating"], r["games_played"]) if r else (START_RATING, 0)


def ensure_predictions(conn, start_iso: str, end_iso: str, serie_id: int | None) -> None:
    """Считает/обновляет прогнозы на карты матчей окна. Карта, которая уже началась, не трогается."""
    q = ("SELECT * FROM matches WHERE start_at >= ? AND start_at < ? "
         "AND status IN ('not_started','running')")
    args: list = [start_iso, end_iso]
    if serie_id:
        q += " AND serie_id = ?"
        args.append(serie_id)
    for m in conn.execute(q, args).fetchall():
        p = expected(get_rating(conn, m["team1_id"])[0], get_rating(conn, m["team2_id"])[0])
        for pos in range(1, (m["number_of_games"] or 1) + 1):
            g = conn.execute("SELECT status FROM games WHERE match_id=? AND position=?",
                             (m["id"], pos)).fetchone()
            status = g["status"] if g else "not_started"
            if status != "not_started":
                continue  # идёт/сыграна/не будет сыграна: прогноз замораживаем
            conn.execute(
                """INSERT INTO predictions(match_id, position, model_version, team1_id, team2_id,
                       p_team1, created_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(match_id, position, model_version) DO UPDATE SET
                       p_team1=excluded.p_team1, created_at=excluded.created_at
                   WHERE result_winner_id IS NULL""",
                (m["id"], pos, MODEL_VERSION, m["team1_id"], m["team2_id"], p, utc_now_iso()),
            )
    conn.commit()


def settle_predictions(conn) -> None:
    conn.execute(
        """UPDATE predictions SET result_winner_id = (
               SELECT g.winner_id FROM games g
               WHERE g.match_id = predictions.match_id AND g.position = predictions.position
                 AND g.status = 'finished')
           WHERE result_winner_id IS NULL"""
    )
    conn.commit()


def accuracy_report(conn) -> dict | None:
    rows = conn.execute(
        """SELECT p_team1, team1_id, result_winner_id FROM predictions
           WHERE result_winner_id IS NOT NULL AND model_version = ?""", (MODEL_VERSION,)
    ).fetchall()
    if not rows:
        return None
    n = len(rows)
    hit = ll = brier = 0.0
    for r in rows:
        y = 1.0 if r["result_winner_id"] == r["team1_id"] else 0.0
        p = min(max(r["p_team1"], 1e-6), 1 - 1e-6)
        hit += (p >= 0.5) == (y == 1.0)
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        brier += (p - y) ** 2
    return {"n": n, "accuracy": hit / n, "logloss": ll / n, "brier": brier / n}


async def refresh(ps: PandaScore, conn, now_local: datetime, serie_id: int | None) -> None:
    start, end = day_bounds_utc(now_local)
    store(conn, await ps.matches_between(start, end, serie_id))
    store(conn, await ps.past_matches(pages=1))
    rebuild_ratings(conn)
    ensure_predictions(conn, start, end, serie_id)
    settle_predictions(conn)


async def backfill(ps: PandaScore, conn, pages: int) -> int:
    n = store(conn, await ps.past_matches(pages=pages))
    rebuild_ratings(conn)
    return n
