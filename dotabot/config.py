import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    pandascore_token: str
    tz: ZoneInfo
    db_path: str
    owner_id: int | None
    stratz_token: str | None


def load_settings() -> Settings:
    load_dotenv()
    owner = os.getenv("OWNER_ID", "").strip()
    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        pandascore_token=os.environ["PANDASCORE_TOKEN"],
        tz=ZoneInfo(os.getenv("BOT_TZ", "Europe/Moscow")),
        db_path=os.getenv("DB_PATH", "dota.db"),
        owner_id=int(owner) if owner else None,
        stratz_token=os.getenv("STRATZ_TOKEN", "").strip() or None,
    )
