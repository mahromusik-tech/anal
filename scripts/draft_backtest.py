"""Честный бэктест слоя по пикам на накопленной истории: python -m scripts.draft_backtest [карт=1500]
Для каждой карты статистика считается только по более ранним картам. Показывает, какие компоненты
(герои, игроки, связки, контры) реально помогают."""
import math
import sys
from collections import defaultdict

from dotabot.config import load_settings
from dotabot.db import connect
from dotabot.draft import Stats, sigmoid


def ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def best(recs, key):
    return min((sum(ll(sigmoid(w * key(r)), r[1]) for r in recs) / len(recs), w) for w in [i * 0.25 for i in range(9)])


def main() -> None:
    last = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    conn = connect(load_settings().db_path)
    ms = defaultdict(lambda: {0: [], 1: [], "t": 0, "won0": 0})
    for r in conn.execute("SELECT match_id, side, hero_id, account_id, won, start_time FROM hist_picks "
                          "WHERE start_time IS NOT NULL ORDER BY start_time, match_id"):
        m = ms[r["match_id"]]
        m[r["side"]].append((r["account_id"], r["hero_id"]))
        m["t"] = r["start_time"]
        if r["side"] == 0:
            m["won0"] = r["won"]
    items = [m for m in ms.values() if len(m[0]) == 5 and len(m[1]) == 5]
    items = items[int(len(items) * 0.3):][-last:]
    print(f"Карт для проверки: {len(items)}")
    if len(items) < 200:
        print("Мало данных: python -m scripts.backfill_stratz, потом повторите.")
        return
    recs = []
    for m in items:
        d = Stats(conn, before=m["t"]).delta(m[0], m[1])
        recs.append((d, m["won0"]))
    print("Монетка: logloss 0.693")
    print("Всё вместе: logloss %.4f, вес %.2f, угадано %.1f%%" % (
        *best(recs, lambda r: r[0]["delta"]),
        100 * sum((r[0]["delta"] > 0) == (r[1] == 1) for r in recs) / len(recs)))
    for part in ("heroes", "players", "combos", "counters"):
        l, w = best(recs, lambda r, p=part: r[0]["parts"][p])
        print(f"  только {part:9s}: logloss {l:.4f}, вес {w:.2f}")


main()
