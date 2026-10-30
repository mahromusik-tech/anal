"""Синхронизация данных, рейтинги, прогнозы, пики, метрики."""
import asyncio
import math
from datetime import datetime, timedelta, timezone

from . import db as dbm
from .model import DEFAULT, MODEL_VERSION, START_RATING, elo_walk, expected
from .pandascore import PandaScore, parse_match

ISO = "%Y-%m-%dT%H:%M:%SZ"
LOCK = asyncio.Lock()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO)


def parse_ts(s):
    try:
        return datetime.strptime(s, ISO).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def day_bounds_utc(now_local: datetime, days: int = 1) -> tuple[str, str]:
    """Границы [00:00 локального дня, +days суток) в UTC ISO."""
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    f = lambda d: d.astimezone(timezone.utc).strftime(ISO)
    return f(start), f(end)


def get_filter(conn, chat_id: int) -> int | None:
    r = conn.execute("SELECT serie_id FROM chat_filter WHERE chat_id=?", (chat_id,)).fetchone()
    return r[0] if r else None


def set_filter(conn, chat_id: int, serie_id: int | None) -> None:
    conn.execute("INSERT INTO chat_filter(chat_id, serie_id) VALUES(?,?) "
                 "ON CONFLICT(chat_id) DO UPDATE SET serie_id=excluded.serie_id", (chat_id, serie_id))
    conn.commit()


def store(conn, raw_matches: list[dict]) -> int:
    n = 0
    for raw in raw_matches:
        m = parse_match(raw)
        if m and m["start_at"]:
            dbm.upsert_match(conn, m, commit=False)
            n += 1
    conn.commit()
    return n


def load_rows(conn) -> list[tuple]:
    rows = conn.execute(
        """SELECT g.winner_id, g.played_at, m.team1_id, m.team2_id
           FROM games g JOIN matches m ON m.id = g.match_id
           WHERE g.status = 'finished' AND g.winner_id IS NOT NULL AND m.forfeit = 0
           ORDER BY g.played_at, g.id""").fetchall()
    return [(r["team1_id"], r["team2_id"], r["winner_id"] == r["team1_id"], parse_ts(r["played_at"]))
            for r in rows if r["winner_id"] in (r["team1_id"], r["team2_id"])]


def rebuild_ratings(conn) -> None:
    rating, played, _ = elo_walk(load_rows(conn), DEFAULT, until=datetime.now(timezone.utc))
    conn.execute("DELETE FROM ratings")
    conn.executemany("INSERT INTO ratings(team_id, rating, games_played) VALUES(?,?,?)",
                     [(t, rating[t], played[t]) for t in rating])
    conn.commit()


def get_rating(conn, team_id: int) -> tuple[float, int]:
    r = conn.execute("SELECT rating, games_played FROM ratings WHERE team_id=?", (team_id,)).fetchone()
    return (r["rating"], r["games_played"]) if r else (START_RATING, 0)


def ensure_predictions(conn, start_iso: str, end_iso: str) -> None:
    """Прогнозы (без пиков) на карты матчей окна. Начавшаяся/сыгранная карта замораживается."""
    q = ("SELECT * FROM matches WHERE start_at >= ? AND start_at < ? "
         "AND status IN ('not_started','running')")
    for m in conn.execute(q, (start_iso, end_iso)).fetchall():
        p = expected(get_rating(conn, m["team1_id"])[0], get_rating(conn, m["team2_id"])[0], DEFAULT.scale)
        for pos in range(1, (m["number_of_games"] or 1) + 1):
            g = conn.execute("SELECT status FROM games WHERE match_id=? AND position=?",
                             (m["id"], pos)).fetchone()
            if (g["status"] if g else "not_started") != "not_started":
                continue
            conn.execute(
                """INSERT INTO predictions(match_id, position, model_version, team1_id, team2_id,
                       p_team1, created_at) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(match_id, position, model_version) DO UPDATE SET
                       team1_id=excluded.team1_id, team2_id=excluded.team2_id,
                       p_team1=excluded.p_team1, created_at=excluded.created_at
                   WHERE result_winner_id IS NULL""",
                (m["id"], pos, MODEL_VERSION, m["team1_id"], m["team2_id"], p, utc_now_iso()))
    conn.commit()


def settle_predictions(conn) -> None:
    conn.execute(
        """UPDATE predictions SET result_winner_id = (
               SELECT g.winner_id FROM games g
               WHERE g.match_id = predictions.match_id AND g.position = predictions.position
                 AND g.status = 'finished')
           WHERE result_winner_id IS NULL""")
    conn.commit()


def accuracy_report(conn) -> dict | None:
    rows = conn.execute(
        """SELECT p_team1, team1_id, result_winner_id FROM predictions
           WHERE result_winner_id IS NOT NULL AND model_version = ?""", (MODEL_VERSION,)).fetchall()
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


def store_hist(conn, match_id: int, rows: list[dict]) -> None:
    """Результат сыгранной карты Valve: пики, игроки, победа (основа для статистики героев)."""
    conn.executemany(
        "INSERT OR IGNORE INTO hist_picks(match_id, side, hero_id, account_id, won, patch, start_time) "
        "VALUES(:match_id,:side,:hero_id,:account_id,:won,NULL,:start)", rows)
    conn.execute("INSERT OR IGNORE INTO match_done(match_id) VALUES(?)", (match_id,))
    conn.commit()


def store_live_picks(conn, match_id: int, position: int, rows: list[tuple]) -> None:
    """rows: (team_id PandaScore, account_id, hero_id) идущей карты."""
    conn.execute("DELETE FROM live_picks WHERE match_id=? AND position=?", (match_id, position))
    conn.executemany("INSERT OR IGNORE INTO live_picks(match_id, position, team_id, player_id, hero_id) "
                     "VALUES(?,?,?,?,?)", [(match_id, position, *r) for r in rows])
    conn.commit()


def add_patch(conn, name: str, start_ts: int) -> None:
    conn.execute("INSERT OR REPLACE INTO patches(name, start_ts) VALUES(?,?)", (name, start_ts))
    conn.commit()


def store_heroes(conn, heroes: list[dict]) -> None:
    conn.executemany("INSERT OR REPLACE INTO heroes(id, name) VALUES(?,?)",
                     [(h["id"], h.get("displayName") or h.get("localized_name") or h.get("name")) for h in heroes])
    conn.commit()


async def refresh(ps: PandaScore, conn, now_local: datetime) -> None:
    async with LOCK:
        start, end = day_bounds_utc(now_local, days=2)  # сегодня и завтра
        lo = (parse_ts(start) - timedelta(days=1)).strftime(ISO)  # матчи, начатые вчера и идущие через полночь
        store(conn, await ps.matches_between(lo, end))
        store(conn, await ps.running_matches())
        store(conn, await ps.past_matches(pages=1))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime(ISO)
        stale = [r[0] for r in conn.execute(
            "SELECT id FROM matches WHERE status IN ('not_started','running') AND start_at < ? "
            "ORDER BY start_at DESC LIMIT 50", (cutoff,))]  # перенесённые/отменённые/зависшие
        store(conn, await ps.matches_by_ids(stale))
        rebuild_ratings(conn)
        ensure_predictions(conn, start, end)
        settle_predictions(conn)


async def refresh_match(ps: PandaScore, conn, match_id: int) -> None:
    async with LOCK:
        store(conn, await ps.matches_by_ids([match_id]))
        rebuild_ratings(conn)
        settle_predictions(conn)


async def backfill(ps: PandaScore, conn, pages: int) -> int:
    n = store(conn, await ps.past_matches(pages=pages))
    rebuild_ratings(conn)
    return n


async def backfill_picks(api, conn, n_leagues: int = 50, pages: int = 3, leagues: list[int] | None = None,
                         log=print) -> tuple[int, int, int]:
    """История карт с пиками из STRATZ (до 100 карт лиги за запрос). Возвращает (новых, с итогом, лиг)."""
    store_heroes(conn, await api.heroes())
    if not leagues:
        leagues = []
        try:
            leagues += await api.leagues(n_leagues)
        except Exception as e:
            log(f"Список лиг: {e}")
        try:
            leagues += [x["league_id"] for x in await api.live() if x["league_id"]]
        except Exception as e:
            log(f"Живые матчи: {e}")
        leagues += [r[0] for r in conn.execute("SELECT DISTINCT league_id FROM live_games WHERE league_id IS NOT NULL")]
        leagues = list(dict.fromkeys(leagues))
    have = {r[0] for r in conn.execute("SELECT match_id FROM match_done")}
    new = seen = 0
    for lid in leagues:
        for page in range(pages):
            try:
                got, results = await api.league_matches(lid, page * 100)
            except Exception as e:
                log(f"лига {lid}: {e}")
                break
            for r in results:
                seen += 1
                if r["match_id"] not in have:
                    store_hist(conn, r["match_id"], r["rows"])
                    have.add(r["match_id"])
                    new += 1
            await asyncio.sleep(0.4)  # лимит STRATZ: 20 запр/с, 250/мин
            if got < 100:
                break
        log(f"  лига {lid}: всего новых карт {new}")
    return new, seen, len(leagues)


async def bootstrap(ps, api, conn, log=print) -> None:
    """Первый запуск на пустой базе (например, новый сервер): сама качает историю результатов и пиков."""
    if conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] < 500:
        log("Первый запуск: загружаю историю матчей PandaScore")
        await backfill(ps, conn, 30)
    if api and conn.execute("SELECT COUNT(DISTINCT match_id) FROM hist_picks").fetchone()[0] < 1000:
        log("Первый запуск: загружаю историю карт с пиками из STRATZ")
        new, _, n = await backfill_picks(api, conn, log=log)
        log(f"История пиков: {new} новых карт из {n} лиг")
