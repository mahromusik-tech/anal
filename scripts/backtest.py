"""Walk-forward бэктест Elo: python -m scripts.backtest
Сетка параметров, базовые линии, калибровочная таблица. Первые 30% карт — прогрев, не оцениваются."""
import itertools
import math

from dotabot import service
from dotabot.config import load_settings
from dotabot.db import connect
from dotabot.model import Params, elo_walk


def metrics(preds):
    n = len(preds)
    ll = br = hit = 0.0
    for p, y in preds:
        p = min(max(p, 1e-6), 1 - 1e-6)
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        br += (p - y) ** 2
        hit += (p >= 0.5) == (y == 1.0)
    return ll / n, br / n, hit / n


def main() -> None:
    conn = connect(load_settings().db_path)
    rows = service.load_rows(conn)
    cut = int(len(rows) * 0.3)
    print(f"Карт: {len(rows)}, оцениваются: {len(rows) - cut}")
    if len(rows) - cut < 200:
        print("Мало данных: сделайте python -m scripts.backfill 40")
        return
    res = []
    for k, d, sc in itertools.product((0.7, 1.0, 1.4), (0.0, 0.002, 0.005), (300.0, 400.0, 500.0)):
        _, _, preds = elo_walk(rows, Params(k, d, sc))
        res.append((metrics(preds[cut:]), Params(k, d, sc), preds[cut:]))
    res.sort(key=lambda x: x[0][0])
    print("Монетка: logloss 0.693 | brier 0.250")
    print("Топ-5 (logloss, brier, accuracy):")
    for (ll, br, acc), p, _ in res[:5]:
        print(f"  {ll:.4f} {br:.4f} {acc:.1%}  {p}")
    best = res[0]
    print("\nКалибровка лучшего (p команды 1 -> факт):")
    bins = [[0, 0.0, 0.0] for _ in range(10)]
    for p, y in best[2]:
        b = min(int(p * 10), 9)
        bins[b][0] += 1
        bins[b][1] += p
        bins[b][2] += y
    for i, (n, sp, sy) in enumerate(bins):
        if n:
            print(f"  {i / 10:.1f}-{(i + 1) / 10:.1f}: n={n:5d}  прогноз {sp / n:.2f}  факт {sy / n:.2f}")
    print("\nПеренесите лучшие значения в Params (dotabot/model.py), поднимите MODEL_VERSION.")


main()
