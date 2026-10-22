"""Draft picks: every team gets one pick a round in each upcoming draft, and picks change hands
by trade or as compensation for signing another team's RFA.

Only the moves are stored. A pick belongs to its original team until it's moved, then to whoever
its latest move sent it to, so a new draft year needs no setup: its picks appear once it's in range.
"""

import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import rules as R
from .db import Connection, rows, transaction
from .sync import now_iso

MOVE_KINDS = {"trade": "Trade", "rfa": "RFA compensation"}

PickKey = Tuple[int, int, str]   # (draft_year, round, original_team_id)


def draft_years(season_year: int, count: int) -> List[int]:
    """The drafts picks exist for: the one before next season, and the ones after it."""
    return [season_year + 1 + i for i in range(count)]


def _key(p: Dict[str, Any]) -> PickKey:
    return int(p["draft_year"]), int(p["round"]), p["original_team_id"]


def latest_moves(conn: Connection, league_id: str) -> Dict[PickKey, Dict[str, Any]]:
    """Each moved pick's most recent move. A pick that isn't here is still with its original team."""
    out: Dict[PickKey, Dict[str, Any]] = {}
    for m in rows(conn, "SELECT * FROM pick_moves WHERE league_id=? ORDER BY id", (league_id,)):
        out[_key(m)] = m
    return out


def all_picks(conn: Connection, league_id: str, team_ids: Sequence[str], season_year: int) -> List[Dict[str, Any]]:
    """Every pick in the upcoming drafts, with its owner and the move that put it there (None if it never moved).
    Each draft has the rounds of its own league year's rules."""
    moved, rules = latest_moves(conn, league_id), R.load(conn, league_id, season_year)
    out = []
    for year in draft_years(season_year, rules["draft.drafts_shown"]):
        for rnd in range(1, R.load(conn, league_id, year)["draft.rounds"] + 1):
            for team in team_ids:
                last = moved.get((year, rnd, team))
                out.append({"draft_year": year, "round": rnd, "original_team_id": team,
                            "owner_team_id": last["to_team_id"] if last else team, "last_move": last})
    return out


def move(conn: Connection, league_id: str, team_ids: Sequence[str], season_year: int, kind: str,
         sends: Sequence[Tuple[str, str, Sequence[Dict[str, Any]]]], note: Optional[str], source: str) -> str:
    """Record one deal: each (from_team, to_team, picks) sends those picks, which from_team must own.
    Returns the deal id, which undo() takes."""
    if kind not in MOVE_KINDS:
        raise ValueError("kind must be one of: {}".format(", ".join(MOVE_KINDS)))
    owners = {_key(p): p["owner_team_id"] for p in all_picks(conn, league_id, team_ids, season_year)}
    records, seen = [], set()
    for from_team, to_team, picks in sends:
        if from_team not in team_ids or to_team not in team_ids:
            raise ValueError("Unknown team")
        if from_team == to_team:
            raise ValueError("A team can't send picks to itself")
        for p in picks:
            key = _key(p)
            if key not in owners:
                raise ValueError("There's no {} round {} pick from that team".format(key[0], key[1]))
            if owners[key] != from_team or key in seen:
                raise ValueError("That team doesn't own the {} round {} pick it's sending".format(key[0], key[1]))
            seen.add(key)
            records.append((key, from_team, to_team))
    if not records:
        raise ValueError("Pick at least one draft pick to send")
    if kind == "rfa" and len({(f, t) for _, f, t in records}) > 1:
        raise ValueError("RFA compensation goes one way: from the team that signed him to the team that lost him")
    deal, ts, note = uuid.uuid4().hex[:12], now_iso(), (note or "").strip() or None
    with transaction(conn):
        conn.executemany(
            "INSERT INTO pick_moves (league_id, deal_id, draft_year, round, original_team_id, from_team_id, "
            "to_team_id, kind, note, source, moved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(league_id, deal, year, rnd, orig, f, t, kind, note, source, ts) for (year, rnd, orig), f, t in records])
    return deal


def deal_moves(conn: Connection, league_id: str, deal_id: str) -> List[Dict[str, Any]]:
    return rows(conn, "SELECT * FROM pick_moves WHERE league_id=? AND deal_id=? ORDER BY id", (league_id, deal_id))


def undo(conn: Connection, league_id: str, deal_id: str) -> int:
    """Delete a deal's moves, so its picks go back where they were. Refused once any of them has moved again."""
    mine = deal_moves(conn, league_id, deal_id)
    latest = latest_moves(conn, league_id)
    if any(latest[_key(m)]["id"] != m["id"] for m in mine):
        raise ValueError("Some of these picks have moved again since. Undo the later move first.")
    with transaction(conn):
        conn.execute("DELETE FROM pick_moves WHERE league_id=? AND deal_id=?", (league_id, deal_id))
    return len(mine)
