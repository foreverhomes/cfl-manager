"""Runtime settings, read from environment variables (and an optional .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Union
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    league_id: str
    my_team_id: Optional[str]
    db_path: Path
    # How much a game from last season counts relative to a game this season
    # when blending per-game rates.
    prior_season_weight: float
    # Pseudo-games of league-average production added to every player, so tiny
    # samples (call-ups, rookies) don't produce extreme rates.
    shrink_games: float
    # JSON file mapping teams to their salary sheets (see salary_sheets.example.json).
    salary_sheets_path: Path
    # Apps Script web app that writes an app-owned team's sheet (see cfl/sheet_publisher.gs).
    sheet_publish_url: Optional[str] = None
    sheet_publish_token: Optional[str] = None
    # A Postgres database (postgres://...) to use instead of the SQLite file, as on Vercel.
    database_url: Optional[str] = None
    # salary_sheets.json's contents, for a deployment that has no such file.
    salary_sheets_json: Optional[str] = None
    # Sign in with Google (see README, "Logins"). Without a client id there are no logins and
    # every request acts as the admin, which is refused on Vercel.
    google_client_id: Optional[str] = None
    session_secret: Optional[str] = None        # signs the login cookie
    admin_emails: Tuple[str, ...] = ()          # always admins, so the admin can't be locked out
    on_vercel: bool = False                     # Vercel sets VERCEL=1 in every deployment

    @property
    def database(self) -> Union[Path, str]:
        """What db.connect() opens: the Postgres URL if one is set, else the SQLite file."""
        return self.database_url or self.db_path

    @property
    def database_label(self) -> str:
        """The database for logs and /health, without its password."""
        if not self.database_url:
            return str(self.db_path)
        url = urlsplit(self.database_url)
        return "postgres://{}{}".format(url.hostname or "", url.path)


def get_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    db_path = Path(os.environ.get("CFL_DB_PATH", ROOT / "data" / "league.db"))
    return Settings(
        league_id=os.environ.get("FANTRAX_LEAGUE_ID", "okl9vesymoq1qka7"),
        my_team_id=os.environ.get("FANTRAX_MY_TEAM_ID") or None,
        db_path=db_path,
        prior_season_weight=float(os.environ.get("CFL_PRIOR_SEASON_WEIGHT", "0.5")),
        shrink_games=float(os.environ.get("CFL_SHRINK_GAMES", "5")),
        salary_sheets_path=Path(os.environ.get("CFL_SALARY_SHEETS", ROOT / "salary_sheets.json")),
        sheet_publish_url=os.environ.get("CFL_SHEET_PUBLISH_URL") or None,
        sheet_publish_token=os.environ.get("CFL_SHEET_PUBLISH_TOKEN") or None,
        database_url=os.environ.get("DATABASE_URL") or None,
        salary_sheets_json=os.environ.get("CFL_SALARY_SHEETS_JSON") or None,
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID") or None,
        session_secret=os.environ.get("CFL_SESSION_SECRET") or None,
        admin_emails=tuple(e.strip().lower() for e in os.environ.get("CFL_ADMIN_EMAILS", "").split(",") if e.strip()),
        on_vercel=bool(os.environ.get("VERCEL")),
    )
