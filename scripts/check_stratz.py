"""Диагностика STRATZ: python -m scripts.check_stratz
Проверяет токен и каждый запрос бота, выгружает нужную часть схемы в stratz_schema.txt (пришлите файл)."""
import asyncio
import re

from dotabot.config import load_settings
from dotabot.stratz import QUERIES, Stratz

TEST_VARS = {"live": None, "match": {"id": 9068056822}, "league": {"id": 1, "skip": 0},
             "leagues": {"take": 5}, "heroes": None}
SCHEMA_Q = ("query { __schema { queryType { name } types { name kind fields { name args { name type { ...T } } "
            "type { ...T } } } } } fragment T on __Type { name kind ofType { name kind ofType { name kind "
            "ofType { name kind } } } }")
WANT = re.compile(r"(Live|League|PickBan|Draft)|^(MatchType|MatchPlayerType)$", re.I)


def tr(t) -> str:
    if not t:
        return "?"
    if t["kind"] == "NON_NULL":
        return tr(t["ofType"]) + "!"
    if t["kind"] == "LIST":
        return "[" + tr(t["ofType"]) + "]"
    return t["name"]


async def main() -> None:
    s = load_settings()
    if not s.stratz_token:
        raise SystemExit("Нет STRATZ_TOKEN в .env")
    api = Stratz(s.stratz_token)
    try:
        for name, q in QUERIES.items():
            try:
                data = await api.q(q, TEST_VARS[name])
                print(f"[OK]   {name}: {str(data)[:140]}")
            except Exception as e:
                print(f"[FAIL] {name}: {e}")
        try:
            sc = (await api.q(SCHEMA_Q))["__schema"]
            qn = sc["queryType"]["name"]
            with open("stratz_schema.txt", "w", encoding="utf-8") as f:
                for t in sorted(sc["types"], key=lambda x: x["name"]):
                    if t["name"] == qn or (WANT.search(t["name"]) and t["fields"]):
                        f.write(f"\ntype {t['name']}\n")
                        for fl in t["fields"]:
                            args = ", ".join(f"{a['name']}: {tr(a['type'])}" for a in fl["args"] or [])
                            f.write(f"  {fl['name']}({args}): {tr(fl['type'])}\n")
            print("Схема записана в stratz_schema.txt")
        except Exception as e:
            print("[FAIL] схема:", e)
    finally:
        await api.close()


asyncio.run(main())
