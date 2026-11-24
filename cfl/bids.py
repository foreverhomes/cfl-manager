"""In-season UFA bids: the highest bid for 24 hours wins the player. See docs/in-season-plan.md.

Each bid is a row in ufa_bids that moves through BIDS, a state machine (fsm.py). A player has at most
one pending bid, the high bid, and a higher one moves it to outbid. When its hours pass it wins. The
winning GM adds the player in Fantrax, and the next sync gives him a 1-year contract at the bid
(contracts.mark_pickups). Deadlines are checked when someone reads or bids (settle), so a bid wins at
its deadline whenever someone looks.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import analysis as A
from .db import Connection, row, rows, transaction
from .fsm import Blocked, Conflict, Machine, Transition
from .sync import parse_fantrax_ts

WON_NOTE = "Won a UFA bid"
FROZEN = "Rosters are frozen for the playoffs. Only the admin can allow a bid, to replace an injured player"


def _withdrawable(conn: Connection, bid: Dict[str, Any], rules: Dict[str, Any]) -> List[str]:
    return [] if rules["ufa.withdraw"] else ["Bids can't be withdrawn"]


BIDS = Machine("ufa_bid", "ufa_bids", ["league_id", "id"], {
    "outbid": Transition(frozenset({"pending"}), "outbid"),
    "win": Transition(frozenset({"pending"}), "won"),
    "withdraw": Transition(frozenset({"pending"}), "withdrawn", _withdrawable),
    "cancel": Transition(frozenset({"pending"}), "cancelled"),
    "sign": Transition(frozenset({"won"}), "signed"),
    "void": Transition(frozenset({"pending", "won"}), "voided"),
})


def key(bid: Dict[str, Any]) -> Dict[str, Any]:
    return {"league_id": bid["league_id"], "id": bid["id"]}


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat(timespec="seconds")


def _money(value: float) -> str:
    return "${:g}".format(value)


def settle(conn: Connection, league_id: str, now: Optional[datetime] = None) -> None:
    """Every pending bid whose deadline has passed wins."""
    for bid in rows(conn, "SELECT league_id, id FROM ufa_bids WHERE league_id=? AND state='pending' AND deadline <= ?",
                    (league_id, _stamp(now or datetime.now(timezone.utc)))):
        try:
            BIDS.fire(conn, key(bid), "win", "deadline")
        except Conflict:
            pass                                    # someone else settled it first


def high_bid(conn: Connection, league_id: str, player_id: str) -> Optional[Dict[str, Any]]:
    return row(conn, "SELECT * FROM ufa_bids WHERE league_id=? AND player_id=? AND state='pending'",
               (league_id, player_id))


def _closed(conn: Connection, ctx: A.Context, now: datetime) -> Optional[str]:
    """Why bids are closed now, if they are: they're open in the regular season."""
    league, day = ctx.league, now.date().isoformat()
    if league["start_date"] and day < league["start_date"]:
        return "Bids open when the season starts"
    if league["end_date"] and day > league["end_date"]:
        return "The season is over"
    first = (json.loads(league["playoffs"] or "null") or {}).get("firstPlayoffPeriod")
    start = first and row(conn, "SELECT start_date FROM periods WHERE league_id=? AND kind='scoring' AND number=?",
                          (ctx.league_id, first))
    return FROZEN if start and now >= parse_fantrax_ts(start["start_date"]) else None


def cap_space(conn: Connection, ctx: A.Context, team_id: str) -> Optional[float]:
    """This season's cap space, less the team's open bids, so it can't bid past its room across several
    players. None when the team has no contracts on file."""
    cap = A.cap_summary(ctx, team_id)
    if cap is None:
        return None
    held = row(conn, "SELECT COALESCE(SUM(salary), 0) AS s FROM ufa_bids WHERE league_id=? AND team_id=? "
                     "AND state IN ('pending', 'won')", (ctx.league_id, team_id))["s"]
    return cap["space"] - held


def check(conn: Connection, ctx: A.Context, team_id: str, player_id: str, salary: float,
          high: Optional[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    """What a bid would do, without saving it: the lowest salary that beats `high`, the team's cap space
    after the bid, and what stops it now (empty: nothing does)."""
    p = ctx.players.get(player_id)
    if p is None:
        raise LookupError("No player {}".format(player_id))
    team, space = ctx.teams[team_id], cap_space(conn, ctx, team_id)
    floor = high["salary"] + ctx.rules["ufa.min_raise"] if high else ctx.rules["ufa.min_bid"]
    reasons = [r for r in [_closed(conn, ctx, now)] if r]
    if p.team_id:
        reasons.append("{} is on {}".format(p.name, ctx.teams.get(p.team_id, p.team_id)))
    if high and high["team_id"] == team_id:
        reasons.append("{} has the high bid on {} already".format(team, p.name))
    elif salary < floor:
        reasons.append("A bid on {} must be at least {}".format(p.name, _money(floor)))
    if space is None:
        reasons.append("{} has no contracts on file, so the app can't check its cap space".format(team))
    elif salary > space:
        reasons.append("{} has {} of cap space left for a {} bid".format(team, _money(space), _money(salary)))
    return {"min_salary": floor, "cap_space": None if space is None else space - salary, "reasons": reasons}


def preview(conn: Connection, ctx: A.Context, team_id: str, player_id: str, salary: float,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    settle(conn, ctx.league_id, now)
    return check(conn, ctx, team_id, player_id, salary, high_bid(conn, ctx.league_id, player_id), now)


def place(conn: Connection, ctx: A.Context, team_id: str, player_id: str, salary: float, actor: str,
          force_reason: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Make the team's bid the player's high bid, and return it. The pending bid it beats is outbid.
    Raises Blocked with the reasons it can't be, unless forced with a reason (the caller lets only the
    admin force), and Conflict when another bid came first."""
    now = now or datetime.now(timezone.utc)
    settle(conn, ctx.league_id, now)
    high = high_bid(conn, ctx.league_id, player_id)
    reasons = check(conn, ctx, team_id, player_id, salary, high, now)["reasons"]
    forced = (force_reason or "").strip() or None
    if reasons and not forced:
        raise Blocked(reasons)
    bid = {"id": uuid.uuid4().hex[:12], "league_id": ctx.league_id, "season_year": ctx.league["season_year"],
           "player_id": player_id, "team_id": team_id, "salary": float(salary), "state": "pending",
           "deadline": _stamp(now + timedelta(hours=ctx.rules["ufa.bid_hours"])), "created_by": actor,
           "created_at": _stamp(now)}

    def insert(conn: Connection, beaten: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cur = conn.execute("INSERT INTO ufa_bids ({}) VALUES ({}) ON CONFLICT (league_id, player_id) "
                           "WHERE state = 'pending' DO NOTHING".format(", ".join(bid), ", ".join("?" for _ in bid)),
                           list(bid.values()))
        if cur.rowcount != 1:
            raise Conflict("a pending bid came first")
        BIDS.log(conn, key(bid), "bid", None, "pending", actor, forced if reasons else None,
                 {"overrode": reasons} if reasons else None)
        return {"by": bid["id"]}

    try:
        if high:
            BIDS.fire(conn, key(high), "outbid", actor, effects=insert)
        else:
            with transaction(conn):
                insert(conn)
    except Conflict:
        raise Conflict("Another bid on {} came first. Reload and try again.".format(ctx.players[player_id].name))
    return bid


def withdraw(conn: Connection, ctx: A.Context, bid: Dict[str, Any], actor: str, now: Optional[datetime] = None) -> str:
    settle(conn, ctx.league_id, now)
    return BIDS.fire(conn, key(bid), "withdraw", actor, ctx=ctx.rules)


def void(conn: Connection, ctx: A.Context, bid: Dict[str, Any], actor: str, reason: str,
         now: Optional[datetime] = None) -> str:
    settle(conn, ctx.league_id, now)
    return BIDS.fire(conn, key(bid), "void", actor, effects=lambda conn, bid: {"reason": reason})


def cancel_taken(conn: Connection, ctx: A.Context) -> List[str]:
    """Cancel each pending bid on a player a sync now finds on a CFL team, and return their names."""
    out = []
    for bid in rows(conn, "SELECT * FROM ufa_bids WHERE league_id=? AND state='pending'", (ctx.league_id,)):
        p = ctx.players.get(bid["player_id"])
        if p and p.team_id:
            try:
                BIDS.fire(conn, key(bid), "cancel", "sync", effects=lambda conn, bid, team=p.team_id: {"on": team})
            except Conflict:
                continue
            out.append(p.name)
    return sorted(out)
