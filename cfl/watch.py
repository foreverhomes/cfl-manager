"""Team watcher: what changed since the last check, and what needs a roster move.

Each run snapshots your roster (lineup status, NHL games played) and category ranks into a
small JSON file, then compares against the previous snapshot. Without an injury feed, health is
inferred from games played: a player in your IR slot who played is healthy again, and an active
or bench player whose NHL team kept playing without him is probably hurt or scratched.
"""

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import analysis as A

HISTORY = 30          # snapshots of category ranks kept for trends
MISSED_ALERT = 2      # consecutive team games missed before flagging a skater
MISSED_ALERT_G = 5    # goalies sit regularly, so wait longer


def team_games_between(conn: sqlite3.Connection, after: str, through: str) -> Dict[str, int]:
    """Regular-season games per NHL team with after < game_date <= through."""
    counts: Dict[str, int] = {}
    for g in conn.execute("SELECT away, home FROM nhl_games WHERE game_type=2 AND game_date > ? AND game_date <= ?",
                          (after, through)):
        for t in g:
            counts[t] = counts.get(t, 0) + 1
    return counts


def stats_through(conn: sqlite3.Connection, today: date) -> str:
    """Last game day the stored NHL stats fully cover: the day before the stats sync ran."""
    r = conn.execute("SELECT MAX(synced_at) FROM skater_stats").fetchone()
    synced = date.fromisoformat(r[0][:10]) if r and r[0] else today
    return date.fromordinal(min(synced, today).toordinal() - 1).isoformat()


def snapshot(conn: sqlite3.Connection, ctx: A.Context, team_id: str, today: date) -> Dict:
    prof = A.team_profile(conn, ctx, team_id)
    roster = {p.fantrax_id: {"name": p.name, "nhl_team": p.nhl_team, "status": p.roster_status, "gp": p.gp_cur,
                    "group": p.group}
              for p in ctx.roster(team_id)}
    ranks = {c["category"]: {"proj": c["projected_rank"], "actual": c["actual_rank"], "record": c["actual_record"],
                             "need": c["need_weight"]}
             for c in prof["categories"]}
    return {"date": today.isoformat(), "stats_through": stats_through(conn, today), "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "team_id": team_id, "team_name": prof["team_name"], "roster": roster, "ranks": ranks,
            "overall_rank": prof["projected_overall_rank"], "expected_wins": prof["expected_category_wins"],
            "strengths": prof["strengths"], "weaknesses": prof["weaknesses"]}


def compare(prev: Optional[Dict], cur: Dict, team_games: Dict[str, int]) -> Dict:
    """Alerts and changes between two snapshots. Carries `missed` streaks forward into `cur`."""
    alerts: List[Dict] = []
    changes: List[str] = []
    prev_roster = (prev or {}).get("roster", {})
    for fid, p in cur["roster"].items():
        before = prev_roster.get(fid)
        played = before is not None and (p["gp"] or 0) > (before["gp"] or 0)
        games = team_games.get(p["nhl_team"] or "", 0) if before is not None else 0
        missed = 0 if played else (before or {}).get("missed", 0) + games
        p["missed"] = missed
        if before is None:
            if prev is not None:
                changes.append("Added {} ({})".format(p["name"], p["status"]))
        elif before["status"] != p["status"]:
            changes.append("{}: {} -> {}".format(p["name"], before["status"], p["status"]))
        if p["status"] == "INJURED_RESERVE" and played:
            alerts.append({"kind": "activate", "player": p["name"],
                           "text": "{} played while in your IR slot. Activate him.".format(p["name"])})
        elif p["status"] in ("ACTIVE", "RESERVE") and games and missed >= (
                MISSED_ALERT_G if p.get("group") == "G" else MISSED_ALERT):
            alerts.append({"kind": "missing", "player": p["name"],
                           "text": "{} ({}) has missed {} straight team games. Check for an injury; "
                                   "IR him if eligible.".format(p["name"], p["nhl_team"], missed)})
    for fid, p in prev_roster.items():
        if fid not in cur["roster"]:
            changes.append("Dropped or traded {}".format(p["name"]))
    trends = []
    for cat, r in cur["ranks"].items():
        b = (prev or {}).get("ranks", {}).get(cat)
        if b and b["proj"] and r["proj"] and b["proj"] != r["proj"]:
            trends.append({"category": cat, "from": b["proj"], "to": r["proj"]})
    return {"alerts": alerts, "changes": changes, "rank_moves": trends}


def run(conn: sqlite3.Connection, ctx: A.Context, team_id: str, state_path: Path,
        today: Optional[date] = None) -> Dict:
    today = today or date.today()
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    prev = state.get("last")
    cur = snapshot(conn, ctx, team_id, today)
    # Team games the stats now cover that the previous snapshot's stats didn't.
    games = team_games_between(conn, prev["stats_through"], cur["stats_through"]) if prev else {}
    result = compare(prev, cur, games)
    history = (state.get("history") or [])[-(HISTORY - 1):] + [
        {"date": cur["date"], "overall_rank": cur["overall_rank"], "expected_wins": cur["expected_wins"],
         "ranks": {c: {"proj": r["proj"], "actual": r["actual"]} for c, r in cur["ranks"].items()}}]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last": cur, "history": history}, indent=1))
    return {"team_name": cur["team_name"], "date": cur["date"], "first_run": prev is None,
            "since": prev["date"] if prev else None, **result,
            "overall_rank": cur["overall_rank"], "teams": len(ctx.teams), "expected_wins": cur["expected_wins"],
            "strengths": cur["strengths"], "weaknesses": cur["weaknesses"],
            "ir": [p["name"] for p in cur["roster"].values() if p["status"] == "INJURED_RESERVE"],
            "categories": cur["ranks"], "history": history}


def render(r: Dict) -> str:
    lines = ["**{}** on {}: projected #{} of {}, {:.1f} expected category wins a week.".format(
        r["team_name"], r["date"], r["overall_rank"], r["teams"], r["expected_wins"] or 0)]
    if r["first_run"]:
        lines.append("First check: baseline saved. Changes and IR alerts start with the next run.")
    if r["alerts"]:
        lines.append("\n**Needs a move**")
        lines += ["- " + a["text"] for a in r["alerts"]]
    if r["changes"]:
        lines.append("\n**Roster changes since {}**".format(r["since"]))
        lines += ["- " + c for c in r["changes"]]
    if r["ir"]:
        lines.append("\nIn your IR slot: " + ", ".join(r["ir"]))
    lines.append("\nStrengths: {}. Weaknesses: {}.".format(", ".join(r["strengths"]), ", ".join(r["weaknesses"])))
    if r["rank_moves"]:
        lines.append("Projected rank moves: " + ", ".join(
            "{} #{} -> #{}".format(m["category"], m["from"], m["to"]) for m in r["rank_moves"]))
    lines.append("\n| cat | proj rank | actual rank | W-L-T | need |\n|---|---|---|---|---|")
    for cat, c in r["categories"].items():
        lines.append("| {} | {} | {} | {} | {:.2f} |".format(cat, c["proj"] or "-", c["actual"] or "-",
                                                         c["record"] or "-", c["need"] or 0))
    return "\n".join(lines)
