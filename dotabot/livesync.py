"""Самообучение: ловим идущие карты STRATZ, сопоставляем с PandaScore по названиям команд,
сохраняем пики, после карты дописываем результат в базу и обновляем вес пиков."""
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from . import service
from .draft import Stats, learn_weight
from .model import DEFAULT, expected

ISO = "%Y-%m-%dT%H:%M:%SZ"
GIVE_UP = 30  # попыток получить итог (по 1 в минуту)


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def same_team(a, b) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b and (a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a))))


def find_ps_match(conn, rname, dname):
    now = datetime.now(timezone.utc)
    lo, hi = (now - timedelta(hours=12)).strftime(ISO), (now + timedelta(hours=6)).strftime(ISO)
    for m in conn.execute(
            "SELECT m.*, t1.name n1, t2.name n2 FROM matches m JOIN teams t1 ON t1.id=m.team1_id "
            "JOIN teams t2 ON t2.id=m.team2_id WHERE m.status IN ('running','not_started') "
            "AND m.start_at>=? AND m.start_at<=?", (lo, hi)):
        if same_team(rname, m["n1"]) and same_team(dname, m["n2"]):
            return m, True
        if same_team(rname, m["n2"]) and same_team(dname, m["n1"]):
            return m, False
    return None, None


def series_position(conn, m, p) -> int:
    if p["rs"] is not None and p["ds"] is not None:
        return p["rs"] + p["ds"] + 1
    return conn.execute("SELECT COUNT(*) FROM games WHERE match_id=? AND status='finished'",
                        (m["id"],)).fetchone()[0] + 1


async def ensure_heroes(api, conn) -> None:
    if not conn.execute("SELECT 1 FROM heroes LIMIT 1").fetchone():
        service.store_heroes(conn, await api.heroes())


async def sync_live(api, conn, now: int | None = None) -> None:
    now = now or int(time.time())
    for p in await api.live():
        conn.execute(
            "INSERT INTO live_games(match_id, league_id, radiant, dire, last_seen, state, tries) "
            "VALUES(?,?,?,?,?,'live',0) ON CONFLICT(match_id) DO UPDATE SET last_seen=excluded.last_seen, "
            "league_id=excluded.league_id, radiant=excluded.radiant, dire=excluded.dire, tries=0",
            (p["match_id"], p["league_id"], p["rad"]["name"], p["dire"]["name"], now))
        if len(p["rad"]["heroes"]) < 5 or len(p["dire"]["heroes"]) < 5:
            continue
        m, r1 = find_ps_match(conn, p["rad"]["name"], p["dire"]["name"])
        if not m:
            continue
        pos = series_position(conn, m, p)
        rad_id, dire_id = (m["team1_id"], m["team2_id"]) if r1 else (m["team2_id"], m["team1_id"])
        service.store_live_picks(conn, m["id"], pos,
                                 [(rad_id, a, h) for a, h in p["rad"]["heroes"]]
                                 + [(dire_id, a, h) for a, h in p["dire"]["heroes"]])
        if conn.execute("SELECT p_elo FROM live_games WHERE match_id=?", (p["match_id"],)).fetchone()[0] is None:
            # замораживаем прогноз один раз, пока исход неизвестен
            t1, t2 = (p["rad"]["heroes"], p["dire"]["heroes"]) if r1 else (p["dire"]["heroes"], p["rad"]["heroes"])
            d = Stats(conn).delta(t1, t2)
            p_elo = expected(service.get_rating(conn, m["team1_id"])[0],
                             service.get_rating(conn, m["team2_id"])[0], DEFAULT.scale)
            conn.execute("UPDATE live_games SET ps_match_id=?, position=?, radiant_is_team1=?, p_elo=?, delta=? "
                         "WHERE match_id=?", (m["id"], pos, int(r1), p_elo, d["delta"], p["match_id"]))
    conn.commit()
    await _finish(api, conn, now)


async def _finish(api, conn, now: int) -> None:
    done_any = False
    for r in conn.execute("SELECT * FROM live_games WHERE state='live' AND last_seen<?", (now - 120,)).fetchall():
        try:
            d = await api.result(r["match_id"])
        except Exception as e:
            logging.warning("итог карты %s: %s", r["match_id"], e)
            d = None
        if d:
            service.store_hist(conn, r["match_id"], d["rows"])
            y = int(d["radiant_win"] == bool(r["radiant_is_team1"])) if r["p_elo"] is not None else None
            conn.execute("UPDATE live_games SET state='done', y=? WHERE match_id=?", (y, r["match_id"]))
            done_any = True
        else:
            conn.execute("UPDATE live_games SET tries=tries+1, state=CASE WHEN tries+1>=? THEN 'dead' "
                         "ELSE 'live' END WHERE match_id=?", (GIVE_UP, r["match_id"]))
    conn.commit()
    if done_any:
        learn_weight(conn)
