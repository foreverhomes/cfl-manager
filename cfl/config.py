"""Runtime settings, read from environment variables (and an optional .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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


def get_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    db_path = Path(os.environ.get("CFL_DB_PATH", ROOT / "data" / "league.db"))
    return Settings(
        league_id=os.environ.get("FANTRAX_LEAGUE_ID", "okl9vesymoq1qka7"),
        my_team_id=os.environ.get("FANTRAX_MY_TEAM_ID") or None,
        db_path=db_path,
        prior_season_weight=float(os.environ.get("CFL_PRIOR_SEASON_WEIGHT", "0.5")),
        shrink_games=float(os.environ.get("CFL_SHRINK_GAMES", "5")),
    )
