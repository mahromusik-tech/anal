from datetime import datetime

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from . import service
from .config import Settings
from .formatting import match_card
from .model import MODEL_VERSION

CHUNK = 3800


def build_router(conn, ps, s: Settings) -> Router:
    r = Router()

    async def send_long(msg: Message, text: str) -> None:
        while text:
            cut = text.rfind("\n\n", 0, CHUNK) if len(text) > CHUNK else len(text)
            cut = cut if cut > 0 else min(len(text), CHUNK)
            await msg.answer(text[:cut])
            text = text[cut:].lstrip()

    @r.message(CommandStart())
    async def start(msg: Message):
        await msg.answer(
            "Привет! Я считаю шансы на каждую карту матчей Dota 2 на сегодня.\n\n"
            "/today — матчи сегодня и прогнозы по картам\n"
            "/accuracy — как мои прогнозы совпали с результатами\n\n"
            "Это статистическая аналитика, а не совет, на что ставить."
        )

    @r.message(Command("today"))
    async def today(msg: Message):
        start_iso, end_iso = service.day_bounds_utc(datetime.now(s.tz))
        q = "SELECT * FROM matches WHERE start_at >= ? AND start_at < ?"
        args: list = [start_iso, end_iso]
        if s.serie_id:
            q += " AND serie_id = ?"
            args.append(s.serie_id)
        rows = conn.execute(q + " ORDER BY start_at", args).fetchall()
        if not rows:
            await msg.answer("На сегодня матчей нет.")
            return
        await send_long(msg, "\n\n".join(match_card(conn, m, s.tz) for m in rows))

    @r.message(Command("accuracy"))
    async def accuracy(msg: Message):
        rep = service.accuracy_report(conn)
        if not rep:
            await msg.answer("Пока нет карт, по которым можно сверить прогноз.")
            return
        warn = "\n⚠️ Выборка маленькая, цифры сильно шумят." if rep["n"] < 100 else ""
        await msg.answer(
            f"Модель {MODEL_VERSION}, карт: {rep['n']}\n"
            f"Угадано победителей: {rep['accuracy']:.1%}\n"
            f"Log loss: {rep['logloss']:.3f} (монетка = 0.693)\n"
            f"Brier: {rep['brier']:.3f} (монетка = 0.250)" + warn
        )

    @r.message(Command("refresh"))
    async def refresh(msg: Message):
        if s.owner_id and msg.from_user.id == s.owner_id:
            await service.refresh(ps, conn, datetime.now(s.tz), s.serie_id)
            await msg.answer("Данные обновлены.")

    return r
