"""Storage: schema and small query helpers.

The store is a SQLite file, or a Postgres database when DATABASE_URL is set (as on Vercel).
Queries are written once, in SQL both understand, with sqlite3's `?` placeholders.
"""

import re
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Union

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
    short_name      TEXT,
    salary_cap      REAL                -- from Fantrax rosters
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
    match_method    TEXT,
    birth_date      TEXT,               -- YYYY-MM-DD
    birth_date_source TEXT              -- 'nhl' (synced) or 'manual' (entered in the app; never overwritten)
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

-- Contracts read from each team's salary sheet. One row per sheet line.
CREATE TABLE IF NOT EXISTS contracts (
    league_id       TEXT NOT NULL,
    team_id         TEXT NOT NULL,      -- team whose sheet lists the contract
    sheet_name      TEXT NOT NULL,      -- name as written on the sheet
    player_id       TEXT,               -- matched Fantrax id (NULL if unmatched)
    match_method    TEXT,
    section         TEXT NOT NULL,      -- ROSTER / IR / MINORS / BUYOUT (from sheet section labels)
    salary          REAL,               -- current season
    years_remaining INTEGER,            -- seasons with a salary, counting the current one
    salaries        TEXT,               -- JSON list, current season first
    source          TEXT,
    synced_at       TEXT,
    PRIMARY KEY (league_id, team_id, sheet_name)
);
CREATE INDEX IF NOT EXISTS idx_contracts_player ON contracts(player_id);

-- Contract edits made in the app. They take precedence over the salary sheets and survive syncs.
CREATE TABLE IF NOT EXISTS contract_overrides (
    league_id       TEXT NOT NULL,
    player_id       TEXT NOT NULL,
    salaries        TEXT NOT NULL,      -- JSON list, current season first
    contract_type   TEXT NOT NULL DEFAULT 'STANDARD',   -- STANDARD / ELC / IN_SEASON_UFA
    note            TEXT,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (league_id, player_id)
);

-- Player and contract changes, written by changes.record after syncs and contract edits.
CREATE TABLE IF NOT EXISTS player_changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id       TEXT NOT NULL,
    player_id       TEXT NOT NULL,
    team_id         TEXT,               -- his team after the change, or the one he left
    kind            TEXT NOT NULL,      -- added / dropped / traded / salary / contract
    old_value       TEXT,               -- JSON: a team id, or {"salaries", "contract_type"}
    new_value       TEXT,               -- JSON
    source          TEXT NOT NULL,      -- Sync, Contracts sheet, or who edited the contract
    changed_at      TEXT NOT NULL
);

-- Draft picks that changed hands (see picks.py). A pick no move mentions is still its original team's.
CREATE TABLE IF NOT EXISTS pick_moves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id       TEXT NOT NULL,
    deal_id         TEXT NOT NULL,      -- picks moved together, in one trade or one RFA signing
    draft_year      INTEGER NOT NULL,
    round           INTEGER NOT NULL,
    original_team_id TEXT NOT NULL,     -- the team the pick was first given to
    from_team_id    TEXT NOT NULL,
    to_team_id      TEXT NOT NULL,
    kind            TEXT NOT NULL,      -- trade / rfa (compensation for signing another team's RFA)
    note            TEXT,
    source          TEXT NOT NULL,      -- who recorded it
    moved_at        TEXT NOT NULL
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

-- People who can sign in with Google. CFL_ADMIN_EMAILS are admins whether or not they're listed.
CREATE TABLE IF NOT EXISTS users (
    email           TEXT PRIMARY KEY,   -- Google account email, lowercased
    name            TEXT,
    team_id         TEXT,               -- the GM's team; NULL until the admin links one
    role            TEXT NOT NULL DEFAULT 'gm',         -- gm / admin
    status          TEXT NOT NULL DEFAULT 'pending',    -- pending (asked to join) / active
    created_at      TEXT NOT NULL,
    last_login_at   TEXT
);

-- Small app-level state (e.g. what was last published to each team's sheet).
CREATE TABLE IF NOT EXISTS app_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,      -- JSON
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    resource        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,      -- running / ok / error
    detail          TEXT
);
"""


# Columns added after a table first shipped: CREATE TABLE IF NOT EXISTS won't add them to an
# existing database, so connect() adds any that are missing.
ADDED_COLUMNS = {
    "teams": {"salary_cap": "REAL"},
    "players": {"birth_date": "TEXT", "birth_date_source": "TEXT"},
}

TABLES = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA)


# --------------------------------------------------------------------------- Postgres


def _postgres_ddl(sql: str) -> str:
    """SQLite DDL in Postgres terms: REAL is single precision there, and AUTOINCREMENT is SQLite's."""
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
    return sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")


@lru_cache(maxsize=None)
def _postgres_sql(sql: str) -> str:
    """sqlite3's ? placeholders as psycopg's %s (a literal % is doubled)."""
    return sql.replace("%", "%%").replace("?", "%s")


def _postgres_params(params: Sequence[Any]) -> tuple:
    # sqlite3 stores a bool as 0/1; Postgres won't put a boolean in an INTEGER column.
    return tuple(int(v) if isinstance(v, bool) else v for v in params)


class Row(tuple):
    """A Postgres result row, readable by position or by column name like sqlite3.Row."""

    _index: Dict[str, int] = {}

    def __getitem__(self, key):
        return tuple.__getitem__(self, self._index[key] if isinstance(key, str) else key)

    def keys(self) -> List[str]:
        return list(self._index)


def _row_type(cursor) -> type:
    """psycopg row factory: a Row subclass per result, mapping its column names to positions."""
    names = [c.name for c in cursor.description or ()]
    # Of two same-named columns the first wins, as in sqlite3.Row.
    return type("Row", (Row,), {"_index": {n: i for i, n in reversed(list(enumerate(names)))}})


class PostgresConnection:
    """A psycopg connection behind the part of the sqlite3 API this app uses."""

    def __init__(self, url: str):
        import psycopg  # only needed with a DATABASE_URL

        # prepare_threshold=None: no server-side prepared statements, which a pooled connection
        # string (PgBouncer, such as Neon's -pooler host) can't carry from one transaction to the next.
        self._conn = psycopg.connect(url, row_factory=_row_type, prepare_threshold=None)

    def execute(self, sql: str, params: Sequence[Any] = ()):
        return self._conn.execute(_postgres_sql(sql), _postgres_params(params))

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]):
        cur = self._conn.cursor()
        cur.executemany(_postgres_sql(sql), [_postgres_params(p) for p in seq_of_params])
        return cur

    def executescript(self, script: str) -> None:
        self._conn.execute(script)  # no parameters: sent as is, so it can hold several statements

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


Connection = Union[sqlite3.Connection, PostgresConnection]


def is_postgres(target: Any) -> bool:
    return isinstance(target, str) and target.startswith(("postgres://", "postgresql://"))


# Postgres databases this process has already brought up to date. Checking on every connection
# would add a round trip to every request.
_schema_ready: Set[str] = set()
SCHEMA_LOCK = 20262027  # pg_advisory_xact_lock key: two cold starts don't create the same tables at once


# --------------------------------------------------------------------------- connections


def _columns(conn: Connection, table: str) -> Set[str]:
    if isinstance(conn, PostgresConnection):
        return {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns "
                                           "WHERE table_schema = current_schema() AND table_name = ?", (table,))}
    return {r[1] for r in conn.execute("PRAGMA table_info({})".format(table))}


def _add_missing_columns(conn: Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        have = _columns(conn, table)
        for column, sql_type in columns.items():
            if column not in have:
                if isinstance(conn, PostgresConnection):
                    sql_type = _postgres_ddl(sql_type)
                conn.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table, column, sql_type))


def connect(target: Union[Path, str]) -> Connection:
    """Open the store, a SQLite file or a postgres:// URL, creating any missing tables and columns."""
    if is_postgres(target):
        conn = PostgresConnection(target)
        if target not in _schema_ready:
            conn.execute("SELECT pg_advisory_xact_lock(?)", (SCHEMA_LOCK,))
            conn.executescript(_postgres_ddl(SCHEMA))
            _add_missing_columns(conn)
            conn.commit()
            _schema_ready.add(target)
        return conn
    if "://" in str(target):
        raise ValueError("DATABASE_URL must be a postgres:// or postgresql:// URL")
    db_path = Path(target)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    conn.commit()
    return conn


def copy_database(src: Connection, dst: Connection) -> Dict[str, int]:
    """Replace every table's rows in dst with src's, in one transaction. Returns the rows copied per table."""
    counts = {}
    with transaction(dst):
        for table in TABLES:
            cols = sorted(_columns(src, table) & _columns(dst, table))
            data = src.execute("SELECT {} FROM {}".format(", ".join(cols), table)).fetchall()
            dst.execute("DELETE FROM {}".format(table))
            dst.executemany("INSERT INTO {} ({}) VALUES ({})".format(table, ", ".join(cols), ", ".join("?" for _ in cols)),
                            [tuple(r) for r in data])
            counts[table] = len(data)
        if isinstance(dst, PostgresConnection):
            # Ids came across as they were; new rows are numbered after them.
            for table in ("sync_log", "player_changes", "pick_moves"):
                dst.execute("SELECT setval(pg_get_serial_sequence('{0}', 'id'), COALESCE(MAX(id), 0) + 1, false) "
                            "FROM {0}".format(table))
    return counts


@contextmanager
def transaction(conn: Connection) -> Iterator[Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def rows(conn: Connection, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(conn: Connection, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def upsert(conn: Connection, table: str, records: Iterable[Dict[str, Any]], keys: Sequence[str]) -> int:
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
