from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .draft import combine, game_draft, get_weight, sigmoid
from .model import LOW_DATA_GAMES, MODEL_VERSION, expected, series_win_prob
from .service import get_rating

ISO = "%Y-%m-%dT%H:%M:%SZ"


def _pct(p: float) -> str:
    return f"{round(p * 100)}%"


def _tpl(a, b, p) -> str:
    return f"{a} {_pct(p)} — {b} {_pct(1 - p)}"


def _names(conn, m) -> dict:
    return {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM teams WHERE id IN (?,?)", (m["team1_id"], m["team2_id"]))}


def _start(m, tz):
    return datetime.strptime(m["start_at"], ISO).replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)


def match_title(conn, m, tz) -> str:
    t = _names(conn, m)
    return f"{_start(m, tz):%H:%M} {t[m['team1_id']]} vs {t[m['team2_id']]}"


def match_card(conn, m, tz: ZoneInfo, detail: bool = False) -> str:
    t = _names(conn, m)
    a, b = escape(t[m["team1_id"]]), escape(t[m["team2_id"]])
    n = m["number_of_games"] or 1
    flag = {"canceled": " ❌ отменён", "postponed": " ⏸ отложен", "running": " 🔴 LIVE"}.get(m["status"], "")
    lines = [f"🕒 <b>{_start(m, tz):%d.%m %H:%M}</b> · <b>{a}</b> vs <b>{b}</b> · Bo{n}{flag}"]
    sub = escape(" · ".join(x for x in (m["league"], m["serie"]) if x))
    if sub:
        lines.append(sub)
    if m["status"] == "canceled":
        return "\n".join(lines)

    games = {g["position"]: g for g in conn.execute("SELECT * FROM games WHERE match_id=?", (m["id"],))}
    preds = {p["position"]: p for p in conn.execute(
        "SELECT * FROM predictions WHERE match_id=? AND model_version=?", (m["id"], MODEL_VERSION))}
    (r1, n1), (r2, n2) = get_rating(conn, m["team1_id"]), get_rating(conn, m["team2_id"])
    p0 = expected(r1, r2)
    dw = get_weight(conn)

    w1 = w2 = 0
    p_cur = None  # итоговая вероятность первой несыгранной карты (с пиками, если есть)
    for pos in range(1, n + 1):
        g, p = games.get(pos), preds.get(pos)
        st = g["status"] if g else "not_started"
        if st == "finished":
            if not g["winner_id"]:
                continue
            won1 = g["winner_id"] == m["team1_id"]
            w1, w2 = w1 + won1, w2 + (not won1)
            tag = f"✅ {a if won1 else b}"
            if p:
                tag += f" (прогноз был {_pct(p['p_team1'] if won1 else 1 - p['p_team1'])})"
            lines.append(f"Карта {pos}: {tag}")
            continue
        if st == "not_played" or (m["status"] == "finished" and not g):
            continue
        pre = p["p_team1"] if p else p0
        d = game_draft(conn, m, pos)
        fin = combine(pre, d["delta"], dw) if d else pre
        if p_cur is None:
            p_cur = fin
        tag = " 🔴 идёт" if st == "running" else ""
        if not detail:
            lines.append(f"Карта {pos}: {_tpl(a, b, pre)}{tag}")
            continue
        lines.append(f"<b>Карта {pos}</b>{tag}")
        lines.append(f"• до пиков: {_tpl(a, b, pre)}")
        if d:
            pr = d["parts"]
            lines += [f"• по пикам (только герои): {_tpl(a, b, sigmoid(d['delta']))}",
                      f"• итого: <b>{_tpl(a, b, fin)}</b>",
                      f"• {a}: {escape(', '.join(d['names1']))}",
                      f"• {b}: {escape(', '.join(d['names2']))}",
                      f"• вклад (логиты): герои {pr['heroes']:+.2f}, игроки {pr['players']:+.2f}, "
                      f"связки {pr['combos']:+.2f}, контры {pr['counters']:+.2f}"]
        else:
            lines.append("• пиков пока нет")

    if n >= 3 and m["status"] not in ("finished", "canceled"):
        need = n // 2 + 1
        if p_cur is None:
            s = series_win_prob(p0, need - w1, need - w2)
        else:  # текущая карта — с пиками, остальные — без
            s = (p_cur * series_win_prob(p0, need - w1 - 1, need - w2)
                 + (1 - p_cur) * series_win_prob(p0, need - w1, need - w2 - 1))
        score = f" (счёт {w1}:{w2})" if w1 + w2 else ""
        lines.append(f"Серия: {_tpl(a, b, s)}{score}")

    if min(n1, n2) < LOW_DATA_GAMES:
        lines.append("⚠️ мало данных по одной из команд, прогноз грубый")
    return "\n".join(lines)
