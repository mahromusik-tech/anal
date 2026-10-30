"""Модель elo-v1: Elo на уровне карт, затухание при простое, вероятность серии."""
from dataclasses import dataclass

START_RATING = 1500.0
MODEL_VERSION = "elo-v2"
LOW_DATA_GAMES = 15


@dataclass(frozen=True)
class Params:
    k_scale: float = 1.4
    decay_per_day: float = 0.002  # доля возврата к START_RATING за день простоя (подбирать бэктестом)
    scale: float = 400.0


DEFAULT = Params()


def expected(r1: float, r2: float, scale: float = 400.0) -> float:
    return 1.0 / (1.0 + 10 ** ((r2 - r1) / scale))


def k_factor(games_played: int) -> float:
    if games_played < 10:
        return 40.0
    if games_played < 30:
        return 28.0
    return 20.0


def update(r1, n1, r2, n2, team1_won: bool, p: Params = DEFAULT) -> tuple[float, float]:
    e1 = expected(r1, r2, p.scale)
    s1 = 1.0 if team1_won else 0.0
    return (r1 + p.k_scale * k_factor(n1) * (s1 - e1),
            r2 + p.k_scale * k_factor(n2) * ((1.0 - s1) - (1.0 - e1)))


def elo_walk(rows, params: Params = DEFAULT, until=None):
    """rows: (team1, team2, team1_won, ts|None) по хронологии.
    Возвращает (rating, played, preds), preds = [(p_team1 ДО карты, исход)] для бэктеста."""
    rating: dict = {}
    played: dict = {}
    seen: dict = {}
    preds = []

    def decay(t, ts):
        last = seen.get(t)
        if params.decay_per_day and ts and last and ts > last:
            days = (ts - last).total_seconds() / 86400
            rating[t] = START_RATING + (rating[t] - START_RATING) * (1 - params.decay_per_day) ** days

    for t1, t2, won1, ts in rows:
        for t in (t1, t2):
            rating.setdefault(t, START_RATING)
            played.setdefault(t, 0)
            decay(t, ts)
        r1, r2 = rating[t1], rating[t2]
        preds.append((expected(r1, r2, params.scale), 1.0 if won1 else 0.0))
        rating[t1], rating[t2] = update(r1, played[t1], r2, played[t2], won1, params)
        played[t1] += 1
        played[t2] += 1
        if ts:
            seen[t1] = seen[t2] = ts
    if until:
        for t in rating:
            decay(t, until)
    return rating, played, preds


def series_win_prob(p: float, need1: int, need2: int) -> float:
    """Вероятность победы команды 1 в серии, если ей нужно need1 карт, сопернику need2."""
    if need1 <= 0:
        return 1.0
    if need2 <= 0:
        return 0.0
    return (p * series_win_prob(p, need1 - 1, need2)
            + (1 - p) * series_win_prob(p, need1, need2 - 1))
