"""Pull data from Fantrax + NHL.com into the local SQLite store."""

import json
import sqlite3
import traceback
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from .clients import FantraxClient, NHLStatsClient
from .config import Settings
from .db import row, rows, transaction, upsert

LOWER_IS_BETTER = {"GAA", "GA", "L"}
TEAM_PSEUDO_POSITIONS = {"Tm", "TmS", "TmG"}
RESOURCES = ("league", "players", "rosters", "standings", "matchups", "stats", "schedule", "match")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_fantrax_ts(value: str) -> datetime:
    """Fantrax timestamps look like 2026-10-19T18:59:59.0-0400."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")


def season_id_for(season_year: int) -> int:
    return int("{}{}".format(season_year, season_year + 1))


def normalize_name(value: Optional[str]) -> str:
    if not value:
        return ""
    ascii_ = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return "".join(ch for ch in ascii_.lower() if ch.isalpha())


def split_fantrax_name(name: str):
    """Fantrax names are "Last, First"; returns (first, last, "First Last")."""
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
        return first, last, "{} {}".format(first, last).strip()
    parts = name.split()
    return " ".join(parts[:-1]), (parts[-1] if parts else name), name


def current_period(conn: sqlite3.Connection, league_id: str, kind: str, at: Optional[datetime] = None) -> Optional[int]:
    """Period number containing `at` (default now); clamps to first/last period outside the season."""
    at = at or datetime.now(timezone.utc)
    periods = rows(conn, "SELECT number, start_date, end_date FROM periods WHERE league_id=? AND kind=? ORDER BY number",
                   (league_id, kind))
    if not periods:
        return None
    for p in periods:
        if parse_fantrax_ts(p["start_date"]) <= at <= parse_fantrax_ts(p["end_date"]):
            return p["number"]
    if at < parse_fantrax_ts(periods[0]["start_date"]):
        return periods[0]["number"]
    return periods[-1]["number"]


# --------------------------------------------------------------------------- league


def sync_league(conn: sqlite3.Connection, fx: FantraxClient) -> Dict[str, Any]:
    info = fx.league_info()
    league_id = fx.league_id
    scoring = info.get("scoringSystem", {})

    categories = []
    order = 0
    for group in scoring.get("scoringCategorySettings", []):
        grp = "GOALIE" if "GOALIE" in group["group"]["code"] else "SKATER"
        for cfg in group["configs"]:
            cat = cfg["scoringCategory"]
            categories.append({
                "league_id": league_id,
                "short_name": cat["shortName"],
                "name": cat["name"],
                "code": cat["code"],
                "grp": grp,
                "weight": cfg.get("weight", 1.0),
                "lower_is_better": int(cat["shortName"] in LOWER_IS_BETTER),
                "sort_order": order,
            })
            order += 1

    periods = []
    for kind, key in (("scoring", "scoringPeriods"), ("roster", "rosterPeriods")):
        for p in info.get(key, []):
            periods.append({"league_id": league_id, "kind": kind, "number": p["number"],
                            "start_date": p["startDate"], "end_date": p["endDate"]})

    matchups = []
    short_names = {}
    for period in info.get("matchups", []):
        for seq, m in enumerate(period["matchupList"]):
            matchups.append({"league_id": league_id, "period": period["period"], "seq": seq,
                             "away_team_id": m["away"].get("id"), "home_team_id": m["home"].get("id")})
            for side in ("away", "home"):
                if m[side].get("id"):
                    short_names[m[side]["id"]] = m[side].get("shortName")

    teams = [{"id": t["id"], "league_id": league_id, "name": t["name"], "short_name": short_names.get(t["id"])}
             for t in info.get("teamInfo", {}).values()]

    pool = [{"league_id": league_id, "player_id": pid, "eligible_pos": p.get("eligiblePos"), "status": p.get("status")}
            for pid, p in info.get("playerInfo", {}).items()]

    with transaction(conn):
        upsert(conn, "league", [{
            "id": league_id,
            "name": info.get("leagueName"),
            "season_year": info.get("seasonYear"),
            "scoring_type": scoring.get("type"),
            "start_date": info.get("startDate"),
            "end_date": info.get("endDate"),
            "draft_type": info.get("draftType"),
            "roster_info": json.dumps(info.get("rosterInfo")),
            "playoffs": json.dumps(info.get("playoffs")),
            "draft_settings": json.dumps(info.get("draftSettings")),
            "synced_at": now_iso(),
        }], ["id"])
        for table in ("scoring_categories", "periods", "matchups", "league_players"):
            conn.execute("DELETE FROM {} WHERE league_id=?".format(table), (league_id,))
        upsert(conn, "scoring_categories", categories, ["league_id", "short_name"])
        upsert(conn, "teams", teams, ["id"])
        upsert(conn, "periods", periods, ["league_id", "kind", "number"])
        upsert(conn, "matchups", matchups, ["league_id", "period", "seq"])
        upsert(conn, "league_players", pool, ["league_id", "player_id"])

    return {"teams": len(teams), "categories": len(categories), "periods": len(periods),
            "matchups": len(matchups), "player_pool": len(pool)}


def sync_players(conn: sqlite3.Connection, fx: FantraxClient) -> Dict[str, Any]:
    ids = fx.player_ids("NHL")
    adp = {a["id"]: a.get("ADP") for a in fx.adp("NHL")}
    records = []
    for pid, p in ids.items():
        if p.get("position") in TEAM_PSEUDO_POSITIONS:
            continue
        first, last, full = split_fantrax_name(p.get("name", ""))
        team = p.get("team")
        records.append({
            "fantrax_id": pid,
            "name": full,
            "first_name": first,
            "last_name": last,
            "nhl_team": None if team in (None, "(N/A)") else team,
            "position": p.get("position"),
            "rotowire_id": p.get("rotowireId"),
            "sportradar_id": p.get("sportRadarId"),
            "adp": adp.get(pid),
        })
    with transaction(conn):
        upsert(conn, "players", records, ["fantrax_id"])
    return {"players": len(records), "with_adp": sum(1 for r in records if r["adp"] is not None)}


def sync_rosters(conn: sqlite3.Connection, fx: FantraxClient, period: Optional[int] = None) -> Dict[str, Any]:
    data = fx.team_rosters(period)
    period = data["period"]
    items = []
    for team_id, team in data.get("rosters", {}).items():
        for it in team.get("rosterItems", []):
            items.append({"league_id": fx.league_id, "period": period, "team_id": team_id,
                          "player_id": it["id"], "slot": it.get("position"), "status": it.get("status")})
    with transaction(conn):
        conn.execute("DELETE FROM roster_items WHERE league_id=? AND period=?", (fx.league_id, period))
        upsert(conn, "roster_items", items, ["league_id", "period", "team_id", "player_id"])
    return {"period": period, "roster_items": len(items)}


def sync_standings(conn: sqlite3.Connection, fx: FantraxClient) -> Dict[str, Any]:
    data = fx.standings()
    records = [{"league_id": fx.league_id, "team_id": s["teamId"], "rank": s.get("rank"), "points": s.get("points"),
                "games_back": s.get("gamesBack"), "win_pct": s.get("winPercentage")} for s in data]
    with transaction(conn):
        conn.execute("DELETE FROM standings WHERE league_id=?", (fx.league_id,))
        upsert(conn, "standings", records, ["league_id", "team_id"])
    return {"teams": len(records)}


def _store_matchup_scores(conn: sqlite3.Connection, league_id: str, data: Dict[str, Any]) -> int:
    period = data["period"]
    results, cats = [], []
    for m in data.get("matchups", []):
        for side, other in (("away", "home"), ("home", "away")):
            team = m[side]
            if not team.get("teamId") or not m[other].get("teamId"):
                continue  # bye
            results.append({
                "league_id": league_id, "period": period, "team_id": team["teamId"],
                "opponent_id": m[other]["teamId"], "score": team.get("score"),
                "category_wins": team.get("categoryWins"), "category_losses": team.get("categoryLosses"),
                "category_ties": team.get("categoryTies"), "games_played": team.get("gamesPlayed"),
            })
            for c in m.get("categories", []):
                cats.append({"league_id": league_id, "period": period, "team_id": team["teamId"],
                             "opponent_id": m[other]["teamId"], "category": c["shortName"], "value": c[side].get("value"),
                             "outcome": c[side].get("outcome")})
    conn.execute("DELETE FROM matchup_results WHERE league_id=? AND period=?", (league_id, period))
    conn.execute("DELETE FROM matchup_category_scores WHERE league_id=? AND period=?", (league_id, period))
    upsert(conn, "matchup_results", results, ["league_id", "period", "team_id", "opponent_id"])
    upsert(conn, "matchup_category_scores", cats, ["league_id", "period", "team_id", "opponent_id", "category"])
    return len(results)


def sync_matchups(conn: sqlite3.Connection, fx: FantraxClient, refetch_all: bool = False) -> Dict[str, Any]:
    """Fetch H2H matchup scores for every scoring period up to the current one.

    Completed periods don't change, so they're skipped once stored unless refetch_all.
    """
    cur = current_period(conn, fx.league_id, "scoring")
    if cur is None:
        raise RuntimeError("No scoring periods stored; sync 'league' first")
    have = {r["period"] for r in rows(conn, "SELECT DISTINCT period FROM matchup_results WHERE league_id=?",
                                      (fx.league_id,))}
    fetched = []
    for p in range(1, cur + 1):
        if not refetch_all and p in have and p < cur:
            continue
        data = fx.matchup_scores(p)
        with transaction(conn):
            _store_matchup_scores(conn, fx.league_id, data)
        fetched.append(p)
    return {"current_period": cur, "fetched_periods": fetched}


# --------------------------------------------------------------------------- stats


def _skater_record(s: Dict[str, Any], season_id: int, ts: str) -> Dict[str, Any]:
    return {
        "nhl_id": s["playerId"], "season_id": season_id, "full_name": s.get("skaterFullName"),
        "last_name": s.get("lastName"), "team_abbrevs": s.get("teamAbbrevs"), "position_code": s.get("positionCode"),
        "gp": s.get("gamesPlayed"), "goals": s.get("goals"), "assists": s.get("assists"), "points": s.get("points"),
        "plus_minus": s.get("plusMinus"), "pp_goals": s.get("ppGoals"), "pp_points": s.get("ppPoints"),
        "sh_points": s.get("shPoints"), "gw_goals": s.get("gameWinningGoals"), "shots": s.get("shots"),
        "hits": s.get("hits"), "blocks": s.get("blockedShots"), "pim": s.get("penaltyMinutes"),
        "toi_per_game": s.get("timeOnIcePerGame"), "synced_at": ts,
    }


def _goalie_record(g: Dict[str, Any], season_id: int, ts: str) -> Dict[str, Any]:
    return {
        "nhl_id": g["playerId"], "season_id": season_id, "full_name": g.get("goalieFullName"),
        "last_name": g.get("lastName"), "team_abbrevs": g.get("teamAbbrevs"), "gp": g.get("gamesPlayed"),
        "gs": g.get("gamesStarted"), "wins": g.get("wins"), "losses": g.get("losses"),
        "ot_losses": g.get("otLosses"), "goals_against": g.get("goalsAgainst"),
        "shots_against": g.get("shotsAgainst"), "saves": g.get("saves"), "shutouts": g.get("shutouts"),
        "toi_seconds": g.get("timeOnIce"), "gaa": g.get("goalsAgainstAverage"), "save_pct": g.get("savePct"),
        "synced_at": ts,
    }


def stat_seasons(conn: sqlite3.Connection, league_id: str) -> List[int]:
    """Current NHL season for the league plus the previous one (used as the prior)."""
    lg = row(conn, "SELECT season_year FROM league WHERE id=?", (league_id,))
    if not lg or not lg["season_year"]:
        raise RuntimeError("League not synced yet; sync 'league' first")
    year = lg["season_year"]
    return [season_id_for(year), season_id_for(year - 1)]


def sync_stats(conn: sqlite3.Connection, nhl: NHLStatsClient, seasons: Sequence[int]) -> Dict[str, Any]:
    out = {}
    ts = now_iso()
    for season in seasons:
        skaters = [_skater_record(s, season, ts) for s in nhl.skater_stats(season)]
        goalies = [_goalie_record(g, season, ts) for g in nhl.goalie_stats(season)]
        with transaction(conn):
            conn.execute("DELETE FROM skater_stats WHERE season_id=?", (season,))
            conn.execute("DELETE FROM goalie_stats WHERE season_id=?", (season,))
            upsert(conn, "skater_stats", skaters, ["nhl_id", "season_id"])
            upsert(conn, "goalie_stats", goalies, ["nhl_id", "season_id"])
        out[str(season)] = {"skaters": len(skaters), "goalies": len(goalies)}
    return out


def sync_schedule(conn: sqlite3.Connection, nhl: NHLStatsClient, league_id: str) -> Dict[str, Any]:
    """NHL games between the league's start and end dates (drives daily-lineup projections)."""
    lg = row(conn, "SELECT start_date, end_date FROM league WHERE id=?", (league_id,))
    if not lg or not lg["start_date"] or not lg["end_date"]:
        raise RuntimeError("League not synced yet; sync 'league' first")
    start, end = lg["start_date"], lg["end_date"]
    games, day = [], start
    for _ in range(80):  # ~26 weeks in a season; guards against a non-advancing cursor
        data = nhl.schedule_week(day)
        for d in data.get("gameWeek", []):
            if not (start <= d["date"] <= end):
                continue
            for g in d.get("games", []):
                games.append({"game_id": g["id"], "game_date": d["date"], "season_id": g.get("season"),
                              "game_type": g.get("gameType"), "away": g["awayTeam"]["abbrev"],
                              "home": g["homeTeam"]["abbrev"]})
        nxt = data.get("nextStartDate")
        if not nxt or nxt <= day or nxt > end:
            break
        day = nxt
    with transaction(conn):
        # Replace the window wholesale so postponed / moved games don't linger.
        conn.execute("DELETE FROM nhl_games WHERE game_date BETWEEN ? AND ?", (start, end))
        upsert(conn, "nhl_games", games, ["game_id"])
    return {"games": len(games), "regular_season": sum(1 for g in games if g["game_type"] == 2),
            "from": start, "to": end}


# --------------------------------------------------------------------------- id matching


def _pos_group(pos: Optional[str]) -> str:
    if pos == "G":
        return "G"
    if pos == "D":
        return "D"
    return "F"


def match_players(conn: sqlite3.Connection, league_id: str) -> Dict[str, Any]:
    """Link Fantrax players to NHL.com player ids by name, then team / position to break ties."""
    nhl: Dict[int, Dict[str, Any]] = {}
    for table, is_goalie in (("skater_stats", False), ("goalie_stats", True)):
        pos_col = "'G'" if is_goalie else "position_code"
        for r in rows(conn, "SELECT nhl_id, full_name, last_name, team_abbrevs, {} AS pos, season_id FROM {} "
                            "ORDER BY season_id".format(pos_col, table)):
            p = nhl.setdefault(r["nhl_id"], {"nhl_id": r["nhl_id"], "teams": set()})
            p.update(full=r["full_name"], last=r["last_name"], group="G" if is_goalie else _pos_group(r["pos"]))
            p["teams"].update((r["team_abbrevs"] or "").split(","))

    by_full: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_last: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in nhl.values():
        by_full[normalize_name(p["full"])].append(p)
        by_last[normalize_name(p["last"])].append(p)

    in_pool = {r["player_id"] for r in rows(conn, "SELECT player_id FROM league_players WHERE league_id=?",
                                            (league_id,))}
    fantrax = rows(conn, "SELECT fantrax_id, name, first_name, last_name, nhl_team, position FROM players")

    def pick(cands: List[Dict[str, Any]], fx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        is_goalie = fx["position"] == "G"
        cands = [c for c in cands if (c["group"] == "G") == is_goalie]
        if len(cands) == 1:
            return cands[0]
        for pred in (lambda c: fx["nhl_team"] in c["teams"], lambda c: c["group"] == _pos_group(fx["position"])):
            narrowed = [c for c in cands if pred(c)]
            if len(narrowed) == 1:
                return narrowed[0]
            if narrowed:
                cands = narrowed
        return None

    matches: Dict[str, Any] = {}
    for fx in fantrax:
        hit = pick(by_full.get(normalize_name(fx["name"]), []), fx)
        method = "name"
        if hit is None and fx["nhl_team"]:
            # Nicknames / alternate spellings (Matt/Matthew, Dmitry/Dmitri): same last name,
            # NHL team, position group and first initial.
            initial = normalize_name(fx["first_name"])[:1]
            cands = [c for c in by_last.get(normalize_name(fx["last_name"]), [])
                     if fx["nhl_team"] in c["teams"] and c["group"] == _pos_group(fx["position"])
                     and normalize_name(c["full"])[:1] == initial]
            hit = pick(cands, fx)
            method = "last+team"
        if hit is not None:
            matches[fx["fantrax_id"]] = (hit, method, fx)

    # Several Fantrax entries can resolve to one NHL player; keep the most plausible one.
    claimed: Dict[int, List[str]] = defaultdict(list)
    for fid, (hit, _, _) in matches.items():
        claimed[hit["nhl_id"]].append(fid)
    for nhl_id, fids in claimed.items():
        if len(fids) < 2:
            continue
        fids.sort(key=lambda f: (f in in_pool, matches[f][2]["nhl_team"] in matches[f][0]["teams"],
                                 matches[f][1] == "name"), reverse=True)
        for loser in fids[1:]:
            del matches[loser]

    with transaction(conn):
        conn.execute("UPDATE players SET nhl_id=NULL, match_method=NULL")
        conn.executemany("UPDATE players SET nhl_id=?, match_method=? WHERE fantrax_id=?",
                         [(hit["nhl_id"], method, fid) for fid, (hit, method, _) in matches.items()])

    cur_season = stat_seasons(conn, league_id)[0]
    unmatched_active = rows(conn, """
        SELECT s.nhl_id, s.full_name, s.team_abbrevs, s.gp FROM (
            SELECT nhl_id, full_name, team_abbrevs, gp FROM skater_stats WHERE season_id=? AND gp > 0
            UNION ALL
            SELECT nhl_id, full_name, team_abbrevs, gp FROM goalie_stats WHERE season_id=? AND gp > 0
        ) s LEFT JOIN players p ON p.nhl_id = s.nhl_id
        WHERE p.fantrax_id IS NULL ORDER BY s.gp DESC""", (cur_season, cur_season))
    rostered_unmatched = row(conn, """
        SELECT COUNT(*) AS n FROM roster_items ri JOIN players p ON p.fantrax_id = ri.player_id
        WHERE ri.league_id=? AND ri.period=(SELECT MAX(period) FROM roster_items WHERE league_id=?)
          AND ri.status != 'MINORS' AND p.nhl_id IS NULL""", (league_id, league_id))
    return {
        "matched": len(matches),
        "nhl_players_with_current_gp_unmatched": len(unmatched_active),
        "unmatched_sample": [u["full_name"] for u in unmatched_active[:15]],
        "rostered_non_minors_without_nhl_stats": rostered_unmatched["n"] if rostered_unmatched else 0,
    }


# --------------------------------------------------------------------------- orchestration


def _logged(conn: sqlite3.Connection, resource: str, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    cur = conn.execute("INSERT INTO sync_log (resource, started_at, status) VALUES (?, ?, 'running')",
                       (resource, now_iso()))
    log_id = cur.lastrowid
    conn.commit()
    try:
        result = fn()
        status, detail = "ok", result
    except Exception as exc:  # noqa: BLE001 - recorded in sync_log; later steps still run
        status, detail = "error", {"error": repr(exc), "trace": traceback.format_exc(limit=3)}
        result = None
    conn.execute("UPDATE sync_log SET finished_at=?, status=?, detail=? WHERE id=?",
                 (now_iso(), status, json.dumps(detail), log_id))
    conn.commit()
    return {"resource": resource, "status": status, "detail": detail}


def sync_all(conn: sqlite3.Connection, settings: Settings, resources: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    wanted = list(resources or RESOURCES)
    unknown = set(wanted) - set(RESOURCES)
    if unknown:
        raise ValueError("Unknown resources: {} (valid: {})".format(sorted(unknown), ", ".join(RESOURCES)))
    fx = FantraxClient(settings.league_id)
    nhl = NHLStatsClient()
    steps = {
        "league": lambda: sync_league(conn, fx),
        "players": lambda: sync_players(conn, fx),
        "rosters": lambda: sync_rosters(conn, fx),
        "standings": lambda: sync_standings(conn, fx),
        "matchups": lambda: sync_matchups(conn, fx),
        "stats": lambda: sync_stats(conn, nhl, stat_seasons(conn, settings.league_id)),
        "schedule": lambda: sync_schedule(conn, nhl, settings.league_id),
        "match": lambda: match_players(conn, settings.league_id),
    }
    # Always run in dependency order regardless of the order requested.
    return [_logged(conn, name, steps[name]) for name in RESOURCES if name in wanted]
