"""Contract edits made in the app: local overrides layered over the team salary sheets."""

import json
from typing import Any, Dict, List, Optional, Sequence

from . import analysis as A
from .db import Connection, row, rows, transaction, upsert
from .sync import MAX_CONTRACT_YEARS, now_iso

CONTRACT_TYPES = {
    "STANDARD": "Standard",
    "ELC": "Entry-level (ELC)",
    "IN_SEASON_UFA": "In-season UFA",
}
# League rules: ELCs are a set $3 for at most 3 seasons; in-season UFA pickups get 1-year deals
# that don't use contract-bank years.
ELC_SALARY = 3.0
ELC_MAX_YEARS = 3


def validate(salaries: Sequence[Optional[float]], contract_type: str) -> List[float]:
    if contract_type not in CONTRACT_TYPES:
        raise ValueError("contract_type must be one of: {}".format(", ".join(CONTRACT_TYPES)))
    if not 1 <= len(salaries) <= MAX_CONTRACT_YEARS:
        raise ValueError("A contract runs 1 to {} seasons".format(MAX_CONTRACT_YEARS))
    if any(s is None or s < 0 for s in salaries):
        raise ValueError("Each season needs a salary of $0 or more, with no gaps")
    if contract_type == "ELC" and (len(salaries) > ELC_MAX_YEARS or any(s != ELC_SALARY for s in salaries)):
        raise ValueError("An ELC is ${:g} a season for at most {} seasons".format(ELC_SALARY, ELC_MAX_YEARS))
    if contract_type == "IN_SEASON_UFA" and len(salaries) != 1:
        raise ValueError("In-season UFA pickups get 1-year contracts")
    return [float(s) for s in salaries]


def save_override(conn: Connection, league_id: str, player_id: str, salaries: Sequence[Optional[float]],
                  contract_type: str = "STANDARD", note: Optional[str] = None) -> None:
    clean = validate(salaries, contract_type)
    with transaction(conn):
        upsert(conn, "contract_overrides", [{
            "league_id": league_id, "player_id": player_id, "salaries": json.dumps(clean),
            "contract_type": contract_type, "note": (note or "").strip() or None, "updated_at": now_iso(),
        }], ["league_id", "player_id"])


def delete_override(conn: Connection, league_id: str, player_id: str) -> bool:
    with transaction(conn):
        cur = conn.execute("DELETE FROM contract_overrides WHERE league_id=? AND player_id=?", (league_id, player_id))
    return cur.rowcount > 0


def contract_detail(conn: Connection, ctx: A.Context, player_id: str) -> Dict[str, Any]:
    """Effective contract plus what the sheet says and any app edit, for the player editor."""
    p = ctx.players[player_id]
    sheet_rows = rows(conn, "SELECT c.*, t.name AS team_name FROM contracts c JOIN teams t ON t.id = c.team_id "
                            "WHERE c.league_id=? AND c.player_id=?", (ctx.league_id, player_id))
    sheet_rows.sort(key=lambda c: c["team_id"] != p.team_id)   # his current team's sheet first
    sheet = sheet_rows[0] if sheet_rows else None
    override = row(conn, "SELECT * FROM contract_overrides WHERE league_id=? AND player_id=?",
                   (ctx.league_id, player_id))
    return {
        "player": A.player_summary(p, ctx),
        "effective": None if p.salary is None else {
            "salaries": p.contract_salaries, "salary": p.salary, "years": p.contract_years,
            "contract_type": p.contract_type, "source": p.contract_source,
        },
        "sheet": None if sheet is None else {
            "salaries": json.loads(sheet["salaries"] or "[]"), "section": sheet["section"],
            "sheet_name": sheet["sheet_name"], "team_id": sheet["team_id"], "team_name": sheet["team_name"],
            "match_method": sheet["match_method"],
        },
        "override": None if override is None else {
            "salaries": json.loads(override["salaries"]), "contract_type": override["contract_type"],
            "note": override["note"], "updated_at": override["updated_at"],
        },
        "app_owned": p.team_id in ctx.app_owned,
        "birth_date_source": (row(conn, "SELECT birth_date_source FROM players WHERE fantrax_id=?", (player_id,))
                              or {}).get("birth_date_source"),
        "seasons": [A.season_label(ctx, i) for i in range(MAX_CONTRACT_YEARS)],
        "contract_types": CONTRACT_TYPES,
        "rules": {"max_years": MAX_CONTRACT_YEARS, "elc_salary": ELC_SALARY, "elc_max_years": ELC_MAX_YEARS},
    }
