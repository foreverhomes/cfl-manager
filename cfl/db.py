"""SQLite storage: schema and small query helpers."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS league (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    season_year     INTEGER,
    scoring_type    TEXT,
    start_date      TEXT,
    end_date        TEXT,
    draft_type      TEXT,
    roster_info     TEXT,   -- JSON: position constraints and roster limits
    playoffs        TEXT,   -- JSON
    draft_settings  TEXT,   -- JSON
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS scoring_categories (
    league_id       TEXT NOT NULL,
    short_name      TEXT NOT NULL,
    name            TEXT NOT NULL,
    code            TEXT,
    grp             TEXT NOT NULL,      -- SKATER or GOALIE
    weight          REAL NOT NULL DEFAULT 1.0,
    lower_is_better INTEGER NOT NULL DEFAULT 0,
    sort_order      INTEGER NOT NULL,
    PRIMARY KEY (league_id, short_name)
);

CREATE TABLE IF NOT EXISTS teams (
    id              TEXT PRIMARY KEY,
    league_id       TEXT NOT NULL,
    name            TEXT NOT NULL,
    short_name      TEXT
);

CREATE TABLE IF NOT EXISTS periods (
    league_id       TEXT NOT NULL,
    kind            TEXT NOT NULL,      -- 'scoring' (H2H week) or 'roster' (lineup day)
    number          INTEGER NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    PRIMARY KEY (league_id, kind, number)
);

-- Schedule. With an odd team count some teams play two matchups in a period,
-- and a NULL team id on one side means a bye.
CREATE TABLE IF NOT EXISTS matchups (
    league_id       TEXT NOT NULL,
    period          INTEGER NOT NULL,
    seq             INTEGER NOT NULL,   -- position within the period's matchup list
    away_team_id    TEXT,
    home_team_id    TEXT,
    PRIMARY KEY (league_id, period, seq)
);

-- One row per team per matchup (a team can have two matchups in a period).
CREATE TABLE IF NOT EXISTS matchup_results (
    league_id       TEXT NOT NULL,
    period          INTEGER NOT NULL,
    team_id         TEXT NOT NULL,
    opponent_id     TEXT NOT NULL,
    score           REAL,
    category_wins   REAL,
    category_losses REAL,
    category_ties   REAL,
    games_played    INTEGER,
    PRIMARY KEY (league_id, period, team_id, opponent_id)
);

-- One row per team per matchup per category. A team's value for a category is the
-- same in every matchup it has that period; only the outcome differs.
CREATE TABLE IF NOT EXISTS matchup_category_scores (
    league_id       TEXT NOT NULL,
    period          INTEGER NOT NULL,
    team_id         TEXT NOT NULL,
    opponent_id     TEXT NOT NULL,
    category        TEXT NOT NULL,
    value           REAL,
    outcome         TEXT,               -- WIN / LOSS / TIE
    PRIMARY KEY (league_id, period, team_id, opponent_id, category)
);

CREATE TABLE IF NOT EXISTS standings (
    league_id       TEXT NOT NULL,
    team_id         TEXT NOT NULL,
    rank            INTEGER,
    points          TEXT,               -- W-L-T as reported by Fantrax
    games_back      REAL,
    win_pct         REAL,
    PRIMARY KEY (league_id, team_id)
);

-- Global Fantrax player universe (sport-wide, not league specific).
CREATE TABLE IF NOT EXISTS players (
    fantrax_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,      -- "First Last"
    first_name      TEXT,
    last_name       TEXT,
    nhl_team        TEXT,
    position        TEXT,               -- Fantrax primary position: C/LW/RW/D/G
    rotowire_id     INTEGER,
    sportradar_id   TEXT,
    adp             REAL,
    nhl_id          INTEGER,            -- matched NHL.com player id
    match_method    TEXT
);
CREATE INDEX IF NOT EXISTS idx_players_nhl ON players(nhl_id);

-- League-specific view of the player pool.
CREATE TABLE IF NOT EXISTS league_players (
    league_id       TEXT NOT NULL,
    player_id       TEXT NOT NULL,
    eligible_pos    TEXT,               -- e.g. "LW,C,F"
    status          TEXT,               -- FA or T (on a team)
    PRIMARY KEY (league_id, player_id)
);

CREATE TABLE IF NOT EXISTS roster_items (
    league_id       TEXT NOT NULL,
    period          INTEGER NOT NULL,   -- roster (lineup) period
    team_id         TEXT NOT NULL,
    player_id       TEXT NOT NULL,
    slot            TEXT,               -- lineup slot position
    status          TEXT,               -- ACTIVE / RESERVE / MINORS / INJURED_RESERVE
    PRIMARY KEY (league_id, period, team_id, player_id)
);

CREATE TABLE IF NOT EXISTS skater_stats (
    nhl_id          INTEGER NOT NULL,
    season_id       INTEGER NOT NULL,   -- e.g. 20262027
    full_name       TEXT,
    last_name       TEXT,
    team_abbrevs    TEXT,
    position_code   TEXT,               -- C / L / R / D
    gp              INTEGER,
    goals           INTEGER,
    assists         INTEGER,
    points          INTEGER,
    plus_minus      INTEGER,
    pp_goals        INTEGER,
    pp_points       INTEGER,
    sh_points       INTEGER,
    gw_goals        INTEGER,
    shots           INTEGER,
    hits            INTEGER,
    blocks          INTEGER,
    pim             INTEGER,
    toi_per_game    REAL,               -- seconds
    synced_at       TEXT,
    PRIMARY KEY (nhl_id, season_id)
);

CREATE TABLE IF NOT EXISTS goalie_stats (
    nhl_id          INTEGER NOT NULL,
    season_id       INTEGER NOT NULL,
    full_name       TEXT,
    last_name       TEXT,
    team_abbrevs    TEXT,
    gp              INTEGER,
    gs              INTEGER,
    wins            INTEGER,
    losses          INTEGER,
    ot_losses       INTEGER,
    goals_against   INTEGER,
    shots_against   INTEGER,
    saves           INTEGER,
    shutouts        INTEGER,
    toi_seconds     INTEGER,
    gaa             REAL,
    save_pct        REAL,
    synced_at       TEXT,
    PRIMARY KEY (nhl_id, season_id)
);

CREATE TABLE IF NOT EXISTS nhl_games (
    game_id         INTEGER PRIMARY KEY,
    game_date       TEXT NOT NULL,      -- local game day, YYYY-MM-DD
    season_id       INTEGER,
    game_type       INTEGER,            -- 1 preseason, 2 regular season, 3 playoffs
    away            TEXT NOT NULL,
    home            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nhl_games_date ON nhl_games(game_date);

CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    resource        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,      -- running / ok / error
    detail          TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def rows(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def upsert(conn: sqlite3.Connection, table: str, records: Iterable[Dict[str, Any]], keys: Sequence[str]) -> int:
    """INSERT ... ON CONFLICT(keys) DO UPDATE for a batch of dicts sharing the same columns."""
    records = list(records)
    if not records:
        return 0
    cols = list(records[0].keys())
    updates = [c for c in cols if c not in keys]
    sql = "INSERT INTO {t} ({c}) VALUES ({p}) ON CONFLICT ({k}) DO {u}".format(
        t=table,
        c=", ".join(cols),
        p=", ".join("?" for _ in cols),
        k=", ".join(keys),
        u="UPDATE SET " + ", ".join("{0}=excluded.{0}".format(c) for c in updates) if updates else "NOTHING",
    )
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in records])
    return len(records)
