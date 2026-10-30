import logging
import re
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import draft, service
from .config import Settings
from .formatting import match_card, match_title
from .model import MODEL_VERSION

CHUNK = 3800


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_router(conn, ps, s: Settings) -> Router:
    r = Router()

    async def send_long(msg: Message, text: str, kb=None) -> None:
        while text:
            cut = text.rfind("\n\n", 0, CHUNK) if len(text) > CHUNK else len(text)
            cut = cut if cut > 0 else min(len(text), CHUNK)
            chunk, text = text[:cut], text[cut:].lstrip()
            await msg.answer(chunk, reply_markup=None if text else kb)

    def match_buttons(rows):
        return _kb([[InlineKeyboardButton(text=match_title(conn, m, s.tz), callback_data=f"m:{m['id']}")]
                    for m in rows[:30]])

    async def show_day(msg: Message, offset: int):
        a, b = service.day_bounds_utc(datetime.now(s.tz) + timedelta(days=offset))
        q = "SELECT * FROM matches WHERE start_at >= ? AND start_at < ? AND status != 'canceled'"
        args: list = [a, b]
        sid = service.get_filter(conn, msg.chat.id)
        if sid:
            q += " AND serie_id = ?"
            args.append(sid)
        rows = conn.execute(q + " ORDER BY start_at", args).fetchall()
        if not rows:
            await msg.answer("Матчей нет." + (" Включён фильтр турнира: /tournaments" if sid else ""))
            return
        await send_long(msg, "\n\n".join(match_card(conn, m, s.tz) for m in rows), match_buttons(rows))

    async def render(mid: int):
        try:
            await service.refresh_match(ps, conn, mid)
        except Exception:
            logging.exception("refresh_match")
        m = conn.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
        if not m:
            return None, None
        return (match_card(conn, m, s.tz, detail=True),
                _kb([[InlineKeyboardButton(text="🔄 Обновить", callback_data=f"u:{mid}")]]))

    @r.message(CommandStart())
    async def start(msg: Message):
        await msg.answer(
            "Привет! Считаю шансы на каждую карту матчей Dota 2.\n\n"
            "/today, /tomorrow — матчи (кнопкой открывается детальный прогноз)\n"
            "/match pari vs yandex — найти матч по командам\n"
            "/tournaments — фильтр по турниру\n"
            "/accuracy — как прогнозы совпали с результатами\n"
            "Владелец: /patch, /status, /refresh\n\n"
            "Это статистическая аналитика, а не совет, на что ставить.")

    @r.message(Command("today"))
    async def today(msg: Message):
        await show_day(msg, 0)

    @r.message(Command("tomorrow"))
    async def tomorrow(msg: Message):
        await show_day(msg, 1)

    @r.message(Command("tournaments"))
    async def tournaments(msg: Message):
        a, b = service.day_bounds_utc(datetime.now(s.tz), days=2)
        rows = conn.execute(
            "SELECT DISTINCT serie_id, league, serie FROM matches WHERE start_at >= ? AND start_at < ? "
            "AND serie_id IS NOT NULL AND status != 'canceled' ORDER BY league", (a, b)).fetchall()
        btns = [[InlineKeyboardButton(text="Все турниры", callback_data="f:0")]]
        btns += [[InlineKeyboardButton(text=" ".join(x for x in (x["league"], x["serie"]) if x)[:50],
                                       callback_data=f"f:{x['serie_id']}")] for x in rows[:40]]
        await msg.answer("Выберите турнир:", reply_markup=_kb(btns))

    @r.callback_query(F.data.startswith("f:"))
    async def set_filter(cb: CallbackQuery):
        sid = int(cb.data[2:])
        service.set_filter(conn, cb.message.chat.id, sid or None)
        await cb.message.answer("Фильтр сброшен." if not sid else "Фильтр установлен. Смотрите /today")
        await cb.answer()

    @r.message(Command("match"))
    async def match(msg: Message, command: CommandObject):
        q = (command.args or "").strip().lower()
        if not q:
            await msg.answer("Пример: /match pari vs yandex")
            return
        parts = [p.strip() for p in re.split(r"\s+(?:vs\.?|против|-)\s+", q) if p.strip()][:2]
        a, b = service.day_bounds_utc(datetime.now(s.tz) - timedelta(days=1), days=4)
        sql = ("SELECT m.* FROM matches m JOIN teams t1 ON t1.id=m.team1_id JOIN teams t2 ON t2.id=m.team2_id "
               "WHERE m.start_at >= ? AND m.start_at < ? AND m.status != 'canceled'")
        args: list = [a, b]
        for p in parts:
            sql += " AND (lower(t1.name) LIKE ? OR lower(t2.name) LIKE ?)"
            args += [f"%{p}%"] * 2
        rows = conn.execute(sql + " ORDER BY m.start_at LIMIT 8", args).fetchall()
        if not rows:
            await msg.answer("Не нашёл такой матч (ищу от вчера до +2 дней).")
        elif len(rows) == 1:
            text, kb = await render(rows[0]["id"])
            await msg.answer(text, reply_markup=kb)
        else:
            await msg.answer("Нашёл несколько:", reply_markup=match_buttons(rows))

    @r.callback_query(F.data.startswith("m:"))
    async def open_match(cb: CallbackQuery):
        text, kb = await render(int(cb.data[2:]))
        if text:
            await cb.message.answer(text, reply_markup=kb)
        await cb.answer("" if text else "Матч не найден")

    @r.callback_query(F.data.startswith("u:"))
    async def update_match(cb: CallbackQuery):
        text, kb = await render(int(cb.data[2:]))
        try:
            if text:
                await cb.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            pass  # текст не изменился
        await cb.answer()

    @r.message(Command("accuracy"))
    async def accuracy(msg: Message):
        out = []
        rep = service.accuracy_report(conn)
        if rep:
            warn = "\n⚠️ Выборка маленькая, цифры шумят." if rep["n"] < 100 else ""
            out.append(f"Без пиков ({MODEL_VERSION}), карт: {rep['n']}\n"
                       f"Угадано победителей: {rep['accuracy']:.1%}\n"
                       f"Log loss: {rep['logloss']:.3f} (монетка = 0.693)\n"
                       f"Brier: {rep['brier']:.3f} (монетка = 0.250)" + warn)
        dr = draft.draft_report(conn)
        if dr:
            warn = "\n⚠️ Выборка маленькая, вес пиков ещё не обучен." if dr["n"] < 100 else ""
            out.append(f"С пиками, карт: {dr['n']} (вес пиков {dr['w']:.2f})\n"
                       f"Только Elo: угадано {dr['acc_elo']:.1%}, logloss {dr['ll_elo']:.3f}\n"
                       f"Elo + пики: угадано {dr['acc_mix']:.1%}, logloss {dr['ll_mix']:.3f}" + warn)
        await msg.answer("\n\n".join(out) or "Пока нет карт, по которым можно сверить прогноз.")

    def is_owner(msg: Message) -> bool:
        return bool(s.owner_id and msg.from_user.id == s.owner_id)

    @r.message(Command("patch"))
    async def patch(msg: Message, command: CommandObject):
        if not is_owner(msg):
            await msg.answer("Нет доступа.")
            return
        args = (command.args or "").split()
        if not args:
            rows = conn.execute("SELECT name, start_ts FROM patches ORDER BY start_ts DESC LIMIT 5").fetchall()
            await msg.answer("Патчи: " + (", ".join(f"{x['name']} ({datetime.fromtimestamp(x['start_ts'], s.tz):%d.%m.%Y})"
                                                    for x in rows) or "не заданы") + "\nПример: /patch 7.40 2026-10-15")
            return
        when = (datetime.strptime(args[1], "%Y-%m-%d").replace(tzinfo=s.tz) if len(args) > 1
                else datetime.now(s.tz))
        service.add_patch(conn, args[0], int(when.timestamp()))
        await msg.answer(f"Патч {args[0]} с {when:%d.%m.%Y}. Статистика героев теперь считается и по нему.")

    @r.message(Command("status"))
    async def status(msg: Message):
        if not is_owner(msg):
            await msg.answer("Нет доступа.")
            return
        q = lambda sql: conn.execute(sql).fetchone()[0]
        states = dict(conn.execute("SELECT state, COUNT(*) FROM live_games GROUP BY state").fetchall())
        await msg.answer(f"Карт Valve в базе: {q('SELECT COUNT(DISTINCT match_id) FROM hist_picks')}\n"
                         f"Живые/готовые/потерянные: {states.get('live', 0)}/{states.get('done', 0)}/{states.get('dead', 0)}\n"
                         f"Вес пиков: {draft.get_weight(conn):.2f}")

    @r.message(Command("refresh"))
    async def refresh(msg: Message):
        if not is_owner(msg):
            await msg.answer("Нет доступа.")
            return
        await service.refresh(ps, conn, datetime.now(s.tz))
        await msg.answer("Данные обновлены.")

    return r
