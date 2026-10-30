"""Прогноз по пикам из собственной базы hist_picks (карты Valve, копятся сами).
Герои (с окном «текущий патч»), игрок на герое, связки героев, контрпики. Всё в лог-шансах
с сжатием к ожидаемому значению: мало данных -> вклад ~0. Вес пиков в итоге учится по живым прогнозам."""
import math
import time
from itertools import combinations

K_HERO, K_PLAYER, K_PAIR, K_COUNTER = 20, 8, 25, 40
W_COUNTER = 0.5
MAX_DELTA = 1.5
DEFAULT_W = 0.5  # консервативно: часть эффекта игроков уже сидит в Elo


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def get_weight(conn) -> float:
    r = conn.execute("SELECT v FROM kv WHERE k='draft_w'").fetchone()
    return float(r[0]) if r else DEFAULT_W


def combine(p_pre: float, delta: float, w: float = DEFAULT_W) -> float:
    return sigmoid(logit(p_pre) + w * delta)


def _adj(n, w, prior, k):
    return logit((w + k * prior) / (n + k)) - logit(prior)


class Stats:
    """before — только матчи раньше этого unix-времени (для честного бэктеста)."""

    def __init__(self, conn, before=None):
        self.c, self.t = conn, before
        self.ps = conn.execute("SELECT MAX(start_ts) FROM patches WHERE start_ts<=?",
                               (before or int(time.time()),)).fetchone()[0]

    def q(self, where, args=(), join="", jargs=()):
        params = [*jargs, *args]
        if self.t:
            where += " AND a.start_time<?"
            params.append(self.t)
        r = self.c.execute(f"SELECT COUNT(*), COALESCE(SUM(a.won),0) FROM hist_picks a {join} WHERE {where}",
                           params).fetchone()
        return r[0], r[1]

    def hero(self, h):
        n, w = self.q("a.hero_id=?", (h,))
        base = (w + K_HERO * 0.5) / (n + K_HERO)
        if self.ps:
            n2, w2 = self.q("a.hero_id=? AND a.start_time>=?", (h, self.ps))
            return (w2 + K_HERO * base) / (n2 + K_HERO)
        return base

    def player(self, pl, h, prior):
        if not pl:
            return 0.0
        n, w = self.q("a.account_id=? AND a.hero_id=?", (pl, h))
        return _adj(n, w, prior, K_PLAYER)

    def rel(self, h1, h2, same):
        op = "=" if same else "!="
        return self.q("a.hero_id=?", (h1,), f"JOIN hist_picks b ON b.match_id=a.match_id AND "
                      f"b.side{op}a.side AND b.hero_id=?", (h2,))

    def delta(self, t1, t2):
        """t1, t2: списки (account_id, hero_id). delta > 0 -> пики выгоднее команде 1."""
        base = {h: self.hero(h) for _, h in t1 + t2}

        def side(team):
            heroes = sum(logit(base[h]) for _, h in team)
            players = sum(self.player(pl, h, base[h]) for pl, h in team)
            combos = 0.0
            for a, b in combinations(sorted(h for _, h in team), 2):
                n, w = self.rel(a, b, True)
                combos += _adj(n, w, sigmoid((logit(base[a]) + logit(base[b])) / 2), K_PAIR)
            return heroes, players, combos

        s1, s2 = side(t1), side(t2)
        counters = 0.0
        for _, a in t1:
            for _, b in t2:
                n, w = self.rel(a, b, False)
                counters += _adj(n, w, sigmoid(logit(base[a]) - logit(base[b])), K_COUNTER)
        parts = {"heroes": s1[0] - s2[0], "players": s1[1] - s2[1],
                 "combos": s1[2] - s2[2], "counters": W_COUNTER * counters}
        return {"delta": max(-MAX_DELTA, min(MAX_DELTA, sum(parts.values()))), "parts": parts}


def draft_delta(conn, t1, t2, before=None):
    return Stats(conn, before).delta(t1, t2)


def game_draft(conn, m, position):
    """Пики идущей карты (live_picks). None, если по 5 героев у обеих команд ещё нет."""
    def rows(t):
        return conn.execute(
            "SELECT lp.player_id, lp.hero_id, COALESCE(h.name, CAST(lp.hero_id AS TEXT)) AS name "
            "FROM live_picks lp LEFT JOIN heroes h ON h.id=lp.hero_id "
            "WHERE lp.match_id=? AND lp.position=? AND lp.team_id=?", (m["id"], position, t)).fetchall()
    r1, r2 = rows(m["team1_id"]), rows(m["team2_id"])
    if len(r1) < 5 or len(r2) < 5:
        return None
    d = draft_delta(conn, [(r["player_id"], r["hero_id"]) for r in r1],
                    [(r["player_id"], r["hero_id"]) for r in r2])
    d["names1"], d["names2"] = [r["name"] for r in r1], [r["name"] for r in r2]
    return d


def _ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _done(conn):
    return conn.execute("SELECT p_elo, delta, y FROM live_games WHERE state='done' "
                        "AND y IS NOT NULL AND p_elo IS NOT NULL").fetchall()


def learn_weight(conn, min_n: int = 100):
    """Подбирает вес пиков по сыгранным живым прогнозам (минимум logloss). Меньше min_n — не трогает."""
    rows = _done(conn)
    if len(rows) < min_n:
        return None
    ll, w = min((sum(_ll(combine(r[0], r[1], w), r[2]) for r in rows), w) for w in [i * 0.25 for i in range(7)])
    conn.execute("INSERT OR REPLACE INTO kv(k, v) VALUES('draft_w', ?)", (str(w),))
    conn.commit()
    return w


def draft_report(conn):
    rows = _done(conn)
    if not rows:
        return None
    n, w = len(rows), get_weight(conn)
    mix = [combine(r[0], r[1], w) for r in rows]
    return {"n": n, "w": w,
            "ll_elo": sum(_ll(r[0], r[2]) for r in rows) / n, "ll_mix": sum(_ll(p, r[2]) for p, r in zip(mix, rows)) / n,
            "acc_elo": sum((r[0] >= .5) == (r[2] == 1) for r in rows) / n,
            "acc_mix": sum((p >= .5) == (r[2] == 1) for p, r in zip(mix, rows)) / n}
