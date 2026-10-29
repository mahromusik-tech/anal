import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    league TEXT, serie TEXT, serie_id INTEGER, tournament TEXT,
    start_at TEXT,              -- UTC, формат YYYY-MM-DDTHH:MM:SSZ
    status TEXT,
    number_of_games INTEGER,
    team1_id INTEGER, team2_id INTEGER,
    winner_id INTEGER,
    forfeit INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_matches_start ON matches(start_at);
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    status TEXT,
    winner_id INTEGER,
    played_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_games_match ON games(match_id);
CREATE TABLE IF NOT EXISTS ratings (
    team_id INTEGER PRIMARY KEY,
    rating REAL NOT NULL,
    games_played INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    team1_id INTEGER NOT NULL,
    team2_id INTEGER NOT NULL,
    p_team1 REAL NOT NULL,
    created_at TEXT NOT NULL,
    result_winner_id INTEGER,
    UNIQUE(match_id, position, model_version)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_match(conn: sqlite3.Connection, m: dict) -> None:
    for t in (m["team1"], m["team2"]):
        conn.execute(
            "INSERT INTO teams(id, name) VALUES(?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
            (t["id"], t["name"]),
        )
    conn.execute(
        """INSERT INTO matches(id, league, serie, serie_id, tournament, start_at, status,
               number_of_games, team1_id, team2_id, winner_id, forfeit)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               league=excluded.league, serie=excluded.serie, serie_id=excluded.serie_id,
               tournament=excluded.tournament, start_at=excluded.start_at,
               status=excluded.status, number_of_games=excluded.number_of_games,
               team1_id=excluded.team1_id, team2_id=excluded.team2_id,
               winner_id=excluded.winner_id, forfeit=excluded.forfeit""",
        (m["id"], m["league"], m["serie"], m["serie_id"], m["tournament"], m["start_at"],
         m["status"], m["number_of_games"], m["team1"]["id"], m["team2"]["id"],
         m["winner_id"], int(m["forfeit"])),
    )
    for g in m["games"]:
        conn.execute(
            """INSERT INTO games(id, match_id, position, status, winner_id, played_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   position=excluded.position, status=excluded.status,
                   winner_id=excluded.winner_id, played_at=excluded.played_at""",
            (g["id"], m["id"], g["position"], g["status"], g["winner_id"],
             g["played_at"] or m["start_at"]),
        )
    conn.commit()
