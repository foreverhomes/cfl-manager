"""Change log: what changed between two snapshots of every player's team and contract."""

import json
from typing import Any, Dict, List

from . import analysis as A
from .db import Connection, row, transaction, upsert
from .sync import now_iso

Snapshot = Dict[str, Dict[str, Any]]   # {player_id: {"team_id", "salaries", "contract_type"}}
NOTHING = {"team_id": None, "salaries": [], "contract_type": "STANDARD"}


def _contract(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {"salaries": entry["salaries"], "contract_type": entry["contract_type"]}


def diff(old: Snapshot, new: Snapshot) -> List[Dict[str, Any]]:
    out = []
    for pid in sorted(old.keys() | new.keys()):
        was, now = old.get(pid, NOTHING), new.get(pid, NOTHING)
        team = now["team_id"] or was["team_id"]   # the team after, or the one he left
        if was["team_id"] != now["team_id"]:
            kind = "added" if was["team_id"] is None else "dropped" if now["team_id"] is None else "traded"
            out.append({"player_id": pid, "team_id": team, "kind": kind,
                        "old_value": was["team_id"], "new_value": now["team_id"]})
        if _contract(was) != _contract(now):
            # [:1] compares this season's salary, and treats no contract as no salary.
            kind = "salary" if was["salaries"][:1] != now["salaries"][:1] else "contract"
            out.append({"player_id": pid, "team_id": team, "kind": kind,
                        "old_value": _contract(was), "new_value": _contract(now)})
    return out


def snapshot(ctx: A.Context) -> Snapshot:
    """Every player on a team or with a contract."""
    return {p.fantrax_id: {"team_id": p.team_id, "salaries": p.contract_salaries, "contract_type": p.contract_type}
            for p in ctx.players.values() if p.team_id or p.contract_salaries}


def record(conn: Connection, ctx: A.Context, source: str) -> Dict[str, Any]:
    """Log what changed since the last snapshot, then save this one. The first run only saves a baseline."""
    key = "changes:{}".format(ctx.league_id)
    saved = row(conn, "SELECT value FROM app_state WHERE key=?", (key,))
    new = snapshot(ctx)
    found = diff(json.loads(saved["value"]), new) if saved else []
    ts = now_iso()
    with transaction(conn):
        conn.executemany("INSERT INTO player_changes (league_id, player_id, team_id, kind, old_value, new_value, "
                         "source, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         [(ctx.league_id, c["player_id"], c["team_id"], c["kind"], json.dumps(c["old_value"]),
                           json.dumps(c["new_value"]), source, ts) for c in found])
        upsert(conn, "app_state", [{"key": key, "value": json.dumps(new), "updated_at": ts}], ["key"])
    return {"baseline": saved is None, "changes": len(found)}
