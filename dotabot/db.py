import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
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
    id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, position INTEGER NOT NULL,
    status TEXT, winner_id INTEGER, played_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_games_match ON games(match_id);
CREATE TABLE IF NOT EXISTS ratings (
    team_id INTEGER PRIMARY KEY, rating REAL NOT NULL, games_played INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL, position INTEGER NOT NULL, model_version TEXT NOT NULL,
    team1_id INTEGER NOT NULL, team2_id INTEGER NOT NULL,
    p_team1 REAL NOT NULL, created_at TEXT NOT NULL, result_winner_id INTEGER,
    UNIQUE(match_id, position, model_version)
);
"""

V2 = """
CREATE TABLE IF NOT EXISTS picks (
    game_id INTEGER NOT NULL, team_id INTEGER NOT NULL, player_id INTEGER,
    hero_id INTEGER NOT NULL, hero TEXT,
    PRIMARY KEY(game_id, team_id, hero_id)
);
CREATE INDEX IF NOT EXISTS idx_picks_hero ON picks(hero_id);
CREATE INDEX IF NOT EXISTS idx_picks_player ON picks(player_id, hero_id);
CREATE TABLE IF NOT EXISTS picks_fetched (game_id INTEGER PRIMARY KEY, ok INTEGER);
CREATE TABLE IF NOT EXISTS chat_filter (chat_id INTEGER PRIMARY KEY, serie_id INTEGER);
"""


V3 = """
CREATE TABLE IF NOT EXISTS hist_picks (
    match_id INTEGER NOT NULL, side INTEGER NOT NULL, hero_id INTEGER NOT NULL,
    account_id INTEGER, won INTEGER NOT NULL, patch TEXT, start_time INTEGER,
    PRIMARY KEY(match_id, side, hero_id)
);
CREATE INDEX IF NOT EXISTS idx_hp_hero ON hist_picks(hero_id, patch);
CREATE INDEX IF NOT EXISTS idx_hp_player ON hist_picks(account_id, hero_id);
CREATE INDEX IF NOT EXISTS idx_hp_time ON hist_picks(start_time);
CREATE TABLE IF NOT EXISTS od_done (match_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS heroes (id INTEGER PRIMARY KEY, name TEXT);
"""


V4 = """
CREATE TABLE IF NOT EXISTS match_done (match_id INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO match_done SELECT match_id FROM od_done;
CREATE TABLE IF NOT EXISTS live_picks (
    match_id INTEGER NOT NULL, position INTEGER NOT NULL, team_id INTEGER NOT NULL,
    player_id INTEGER, hero_id INTEGER NOT NULL,
    PRIMARY KEY(match_id, position, team_id, hero_id)
);
CREATE TABLE IF NOT EXISTS live_games (
    match_id INTEGER PRIMARY KEY, league_id INTEGER, radiant TEXT, dire TEXT,
    ps_match_id INTEGER, position INTEGER, radiant_is_team1 INTEGER,
    p_elo REAL, delta REAL, y INTEGER, last_seen INTEGER, state TEXT, tries INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS patches (name TEXT PRIMARY KEY, start_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def _migrate(conn) -> None:
    if conn.execute("PRAGMA user_version").fetchone()[0] < 2:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
        if "patch" not in cols:
            conn.execute("ALTER TABLE matches ADD COLUMN patch TEXT")
        conn.executescript(V2)
        conn.execute("PRAGMA user_version=2")
        conn.commit()
    if conn.execute("PRAGMA user_version").fetchone()[0] < 3:
        conn.executescript(V3)
        conn.execute("PRAGMA user_version=3")
        conn.commit()
    if conn.execute("PRAGMA user_version").fetchone()[0] < 4:
        conn.executescript(V4)
        conn.execute("PRAGMA user_version=4")
        conn.commit()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def upsert_match(conn, m: dict, commit: bool = True) -> None:
    for t in (m["team1"], m["team2"]):
        conn.execute("INSERT INTO teams(id, name) VALUES(?, ?) "
                     "ON CONFLICT(id) DO UPDATE SET name=excluded.name", (t["id"], t["name"]))
    conn.execute(
        """INSERT INTO matches(id, league, serie, serie_id, tournament, start_at, status,
               number_of_games, team1_id, team2_id, winner_id, forfeit, patch)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               league=excluded.league, serie=excluded.serie, serie_id=excluded.serie_id,
               tournament=excluded.tournament, start_at=excluded.start_at,
               status=excluded.status, number_of_games=excluded.number_of_games,
               team1_id=excluded.team1_id, team2_id=excluded.team2_id,
               winner_id=excluded.winner_id, forfeit=excluded.forfeit, patch=excluded.patch""",
        (m["id"], m["league"], m["serie"], m["serie_id"], m["tournament"], m["start_at"],
         m["status"], m["number_of_games"], m["team1"]["id"], m["team2"]["id"],
         m["winner_id"], int(m["forfeit"]), m.get("patch")))
    for g in m["games"]:
        conn.execute(
            """INSERT INTO games(id, match_id, position, status, winner_id, played_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET position=excluded.position, status=excluded.status,
                   winner_id=excluded.winner_id, played_at=excluded.played_at""",
            (g["id"], m["id"], g["position"], g["status"], g["winner_id"],
             g["played_at"] or m["start_at"]))
    if commit:
        conn.commit()
