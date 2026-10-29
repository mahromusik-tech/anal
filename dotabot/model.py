"""Модель v0: Elo на уровне карт + вероятность серии."""
START_RATING = 1500.0
MODEL_VERSION = "elo-v0"
LOW_DATA_GAMES = 15


def expected(r1: float, r2: float) -> float:
    return 1.0 / (1.0 + 10 ** ((r2 - r1) / 400.0))


def k_factor(games_played: int) -> float:
    if games_played < 10:
        return 40.0
    if games_played < 30:
        return 28.0
    return 20.0


def update(r1: float, n1: int, r2: float, n2: int, team1_won: bool) -> tuple[float, float]:
    e1 = expected(r1, r2)
    s1 = 1.0 if team1_won else 0.0
    return (r1 + k_factor(n1) * (s1 - e1),
            r2 + k_factor(n2) * ((1.0 - s1) - (1.0 - e1)))


def series_win_prob(p: float, need1: int, need2: int) -> float:
    """Вероятность победы команды 1 в серии, если ей нужно need1 карт, сопернику need2."""
    if need1 <= 0:
        return 1.0
    if need2 <= 0:
        return 0.0
    return (p * series_win_prob(p, need1 - 1, need2)
            + (1 - p) * series_win_prob(p, need1, need2 - 1))
