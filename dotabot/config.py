import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    pandascore_token: str
    serie_id: int | None
    tz: ZoneInfo
    db_path: str
    owner_id: int | None


def load_settings() -> Settings:
    load_dotenv()
    serie = os.getenv("TOURNAMENT_SERIE_ID", "").strip()
    owner = os.getenv("OWNER_ID", "").strip()
    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        pandascore_token=os.environ["PANDASCORE_TOKEN"],
        serie_id=int(serie) if serie else None,
        tz=ZoneInfo(os.getenv("TZ", "Europe/Moscow")),
        db_path=os.getenv("DB_PATH", "dota.db"),
        owner_id=int(owner) if owner else None,
    )
