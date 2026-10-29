from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .model import LOW_DATA_GAMES, MODEL_VERSION, series_win_prob
from .service import get_rating

ISO = "%Y-%m-%dT%H:%M:%SZ"


def _pct(p: float) -> str:
    return f"{round(p * 100)}%"


def match_card(conn, m, tz: ZoneInfo) -> str:
    t = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM teams WHERE id IN (?,?)", (m["team1_id"], m["team2_id"]))}
    a, b = escape(t[m["team1_id"]]), escape(t[m["team2_id"]])
    start = datetime.strptime(m["start_at"], ISO).replace(
        tzinfo=ZoneInfo("UTC")).astimezone(tz)
    n = m["number_of_games"] or 1
    head = f"🕒 <b>{start:%H:%M}</b> · <b>{a}</b> vs <b>{b}</b> · Bo{n}"
    sub = escape(" · ".join(x for x in (m["league"], m["serie"]) if x))
    lines = [head] + ([sub] if sub else [])

    games = {g["position"]: g for g in conn.execute(
        "SELECT * FROM games WHERE match_id=?", (m["id"],))}
    preds = {p["position"]: p for p in conn.execute(
        "SELECT * FROM predictions WHERE match_id=? AND model_version=?",
        (m["id"], MODEL_VERSION))}

    w1 = w2 = 0
    for pos in range(1, n + 1):
        g, p = games.get(pos), preds.get(pos)
        if g and g["status"] == "finished" and g["winner_id"]:
            won1 = g["winner_id"] == m["team1_id"]
            w1, w2 = w1 + won1, w2 + (not won1)
            tag = f"✅ {a if won1 else b}"
            if p:
                pp = p["p_team1"] if won1 else 1 - p["p_team1"]
                tag += f" (прогноз был {_pct(pp)})"
            lines.append(f"Карта {pos}: {tag}")
        elif g and g["status"] == "not_played":
            continue
        elif p:
            live = " 🔴 идёт" if g and g["status"] == "running" else ""
            lines.append(f"Карта {pos}: {a} {_pct(p['p_team1'])} — {b} {_pct(1 - p['p_team1'])}{live}")
        elif g and g["status"] == "running":
            lines.append(f"Карта {pos}: 🔴 идёт (прогноза до старта нет)")

    if n >= 3 and m["status"] != "finished":
        pr = next((p for p in preds.values()), None)
        if pr:
            need = n // 2 + 1
            s = series_win_prob(pr["p_team1"], need - w1, need - w2)
            lines.append(f"Серия: {a} {_pct(s)} — {b} {_pct(1 - s)}")

    n1, n2 = get_rating(conn, m["team1_id"])[1], get_rating(conn, m["team2_id"])[1]
    if min(n1, n2) < LOW_DATA_GAMES:
        lines.append("⚠️ мало данных по одной из команд, прогноз грубый")
    return "\n".join(lines)
