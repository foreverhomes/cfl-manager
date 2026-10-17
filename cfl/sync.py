"""Pull data from Fantrax + NHL.com into the store."""

import csv
import difflib
import io
import json
import re
import traceback
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .clients import FantraxClient, NHLStatsClient, fetch_sheet_csv
from .config import Settings
from .db import Connection, row, rows, transaction, upsert

LOWER_IS_BETTER = {"GAA", "GA", "L"}
TEAM_PSEUDO_POSITIONS = {"Tm", "TmS", "TmG"}
RESOURCES = ("league", "players", "rosters", "standings", "matchups", "stats", "schedule", "match", "bios", "salaries")


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


def current_period(conn: Connection, league_id: str, kind: str, at: Optional[datetime] = None) -> Optional[int]:
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


def sync_league(conn: Connection, fx: FantraxClient) -> Dict[str, Any]:
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


def sync_players(conn: Connection, fx: FantraxClient) -> Dict[str, Any]:
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


def sync_rosters(conn: Connection, fx: FantraxClient, period: Optional[int] = None) -> Dict[str, Any]:
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
        conn.executemany("UPDATE teams SET salary_cap=? WHERE id=?",
                         [(team.get("salaryCap"), team_id) for team_id, team in data.get("rosters", {}).items()])
    return {"period": period, "roster_items": len(items)}


def sync_standings(conn: Connection, fx: FantraxClient) -> Dict[str, Any]:
    data = fx.standings()
    records = [{"league_id": fx.league_id, "team_id": s["teamId"], "rank": s.get("rank"), "points": s.get("points"),
                "games_back": s.get("gamesBack"), "win_pct": s.get("winPercentage")} for s in data]
    with transaction(conn):
        conn.execute("DELETE FROM standings WHERE league_id=?", (fx.league_id,))
        upsert(conn, "standings", records, ["league_id", "team_id"])
    return {"teams": len(records)}


def _store_matchup_scores(conn: Connection, league_id: str, data: Dict[str, Any]) -> int:
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


def sync_matchups(conn: Connection, fx: FantraxClient, refetch_all: bool = False) -> Dict[str, Any]:
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


def stat_seasons(conn: Connection, league_id: str) -> List[int]:
    """Current NHL season for the league plus the previous one (used as the prior)."""
    lg = row(conn, "SELECT season_year FROM league WHERE id=?", (league_id,))
    if not lg or not lg["season_year"]:
        raise RuntimeError("League not synced yet; sync 'league' first")
    year = lg["season_year"]
    return [season_id_for(year), season_id_for(year - 1)]


def sync_stats(conn: Connection, nhl: NHLStatsClient, seasons: Sequence[int]) -> Dict[str, Any]:
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


def sync_schedule(conn: Connection, nhl: NHLStatsClient, league_id: str) -> Dict[str, Any]:
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


def match_players(conn: Connection, league_id: str) -> Dict[str, Any]:
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


# --------------------------------------------------------------------------- birth dates


def _split_search_name(name: str) -> Tuple[str, str]:
    """("Haoxi", "Wang") from NHL.com's "Haoxi (Simon) Wang"."""
    parts = re.sub(r"\([^)]*\)", " ", name or "").split()
    return (parts[0] if parts else ""), (parts[-1] if parts else "")


def _search_nhl_id(nhl: NHLStatsClient, name: str, position: Optional[str], team: Optional[str],
                   first: Optional[str] = None, last: Optional[str] = None) -> Optional[int]:
    """NHL id for a player by name, using position and team to break ties. Falls back to a
    last-name search with a close first name, for transliterations (Yegor/Egor, Artyom/Artem)."""
    key = normalize_name(name)
    hits = [h for h in nhl.search_players(name) if normalize_name(h.get("name")) == key]
    if not hits and last:
        want_first = normalize_name(first)
        for h in nhl.search_players(last):
            h_first, h_last = _split_search_name(h.get("name"))
            if normalize_name(h_last) == normalize_name(last) and \
                    difflib.SequenceMatcher(None, normalize_name(h_first), want_first).ratio() >= 0.6:
                hits.append(h)
    group = _pos_group(position)
    same_group = [h for h in hits if _pos_group({"L": "LW", "R": "RW"}.get(h.get("positionCode"), h.get("positionCode"))) == group]
    hits = same_group or hits
    if len(hits) > 1 and team:
        on_team = [h for h in hits if h.get("teamAbbrev") == team]
        hits = on_team or hits
    if len(hits) > 1:
        hits = [h for h in hits if h.get("active")] or hits
    return int(hits[0]["playerId"]) if len(hits) == 1 else None


def sync_bios(conn: Connection, nhl: NHLStatsClient, league_id: str, lookup_limit: int = 400) -> Dict[str, Any]:
    """Birth dates: one bios report per season, then a per-player lookup for rostered players still
    missing one (prospects without NHL games). Dates entered in the app ('manual') are never replaced."""
    found: Dict[int, str] = {}
    for season in stat_seasons(conn, league_id):
        for kind in ("skater", "goalie"):
            for r in nhl.bios(kind, season):
                if r.get("birthDate"):
                    found[r["playerId"]] = r["birthDate"]
    with transaction(conn):
        conn.executemany("UPDATE players SET birth_date=?, birth_date_source='nhl' WHERE nhl_id=? "
                         "AND COALESCE(birth_date_source, '') != 'manual'", [(d, i) for i, d in found.items()])
    period = (row(conn, "SELECT MAX(period) AS p FROM roster_items WHERE league_id=?", (league_id,)) or {}).get("p")
    missing = rows(conn, "SELECT DISTINCT p.fantrax_id, p.name, p.first_name, p.last_name, p.nhl_id, p.position, "
                         "p.nhl_team FROM players p "
                         "JOIN roster_items ri ON ri.player_id = p.fantrax_id "
                         "WHERE ri.league_id=? AND ri.period=? AND p.birth_date IS NULL", (league_id, period))
    looked_up, still_missing = 0, []
    for m in missing[:lookup_limit]:
        nhl_id = m["nhl_id"] or _search_nhl_id(nhl, m["name"], m["position"], m["nhl_team"],
                                               m["first_name"], m["last_name"])
        birth = nhl.player_landing(nhl_id).get("birthDate") if nhl_id else None
        if birth:
            with transaction(conn):
                conn.execute("UPDATE players SET birth_date=?, birth_date_source='nhl' WHERE fantrax_id=?",
                             (birth, m["fantrax_id"]))
            looked_up += 1
        else:
            still_missing.append(m["name"])
    return {"from_bios": len(found), "looked_up": looked_up, "still_missing": still_missing,
            "deferred": max(0, len(missing) - lookup_limit)}


# --------------------------------------------------------------------------- salaries

# A whole cell naming a season: 2026/2027, 2026-27, 26/27, 26-27.
SEASON_CELL = re.compile(r"^\s*(\d{2}|\d{4})\s*[/-]\s*(\d{2}|\d{4})\s*$")
NAME_HEADERS = {"player", "players", "name", "skaters"}
PRICE_HEADERS = ("salary", "contract price", "bid price", "price")
LENGTH_HEADERS = ("years left", "contract length", "years")
MAX_CONTRACT_YEARS = 5   # league rule
# Rows whose name cell is one of these start a section (compared lowercase, trailing ':' stripped).
SECTION_LABELS = {
    "buyouts": "BUYOUT", "buyout": "BUYOUT", "cap punishment": "BUYOUT",
    "injured reserve": "IR", "injury reserve": "IR", "ir": "IR", "ir guys": "IR",
    "minors": "MINORS", "minor league": "MINORS", "minors/ir": "MINORS",
    "prospects": "PROSPECTS", "ir cap relief": "SKIP",
    "forwards": "ROSTER", "defence": "ROSTER", "defense": "ROSTER", "goalies": "ROSTER",
}
# Summary rows (totals, cap space, ...) to ignore wherever they appear.
SUMMARY_PREFIXES = ("total", "salary spend", "cap space", "cap room", "payroll", "players signed")


def _money(cell: str) -> Optional[float]:
    text = (cell or "").replace("$", "").replace(",", "").strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None


def _season_start(cell: str) -> Optional[int]:
    """Start year of a season header cell (2026 for "2026/2027", "26-27"), else None."""
    m = SEASON_CELL.match(cell or "")
    if not m:
        return None
    first, second = m.groups()
    year = int(first) if len(first) == 4 else 2000 + int(first)
    return year if (year + 1) % 100 == int(second[-2:]) else None


def column_index(ref: Any) -> int:
    """0-based column from a spreadsheet letter ("A", "AE") or an int."""
    if isinstance(ref, int):
        return ref
    n = 0
    for ch in str(ref).strip().upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _find_column(header: List[str], names: Sequence[str]) -> Optional[int]:
    lowered = [(c or "").strip().lower() for c in header]
    for name in names:   # in priority order
        for j, cell in enumerate(lowered):
            if cell == name or cell.startswith(name):
                return j
    return None


def parse_salary_sheet(rows: List[List[str]], season_year: int, salary_column: Any = None,
                       years_column: Any = None) -> Dict[str, Any]:
    """Contracts from one GM's salary sheet. Every GM keeps a different layout, so:

    - The header row is the first whose first cell is "Player" / "Name" / "Skaters".
    - "seasons" layout: a header cell names the current season (2026/2027, 26-27, ...). Salaries
      are read from that column rightward, through unlabeled columns too, until a blank or a
      marker like UFA/RFA (max 5 seasons).
    - "salary_years" layout: no season columns, or explicit `salary_column` / `years_column`
      hints (letters like "AE" or 0-based ints): the salary repeats for the years left.
    - Rows named BUYOUTS / Injured Reserve / Minors / PROSPECTS start sections (prospects are
      unsigned and skipped); totals and other summary rows, and rows with no salary, are skipped.
    - "Last, First" names are flipped; repeated names keep their first row.
    Returns {"layout", "contracts", "duplicates"}.
    """
    header_at = next((i for i, r in enumerate(rows)
                      if r and r[0].strip().lower().rstrip(":") in NAME_HEADERS), None)
    if header_at is None:
        raise ValueError("No header row starting with 'Player'")
    header = rows[header_at]
    hinted = salary_column is not None
    season_col = None if hinted else next((j for j, c in enumerate(header) if _season_start(c) == season_year), None)
    price_col = column_index(salary_column) if hinted else _find_column(header, PRICE_HEADERS)
    years_col = column_index(years_column) if years_column is not None else _find_column(header, LENGTH_HEADERS)
    if season_col is None and price_col is None:
        raise ValueError("No {}/{:02d} season column and no salary column; add salary_column / years_column "
                         "hints for this sheet".format(season_year, (season_year + 1) % 100))
    layout = "seasons" if season_col is not None else "salary_years"

    def cell(r: List[str], j: Optional[int]) -> str:
        return r[j] if j is not None and j < len(r) else ""

    def salaries_for(r: List[str]) -> List[float]:
        if layout == "seasons":
            out: List[float] = []
            for j in range(season_col, season_col + MAX_CONTRACT_YEARS):
                amount = _money(cell(r, j))
                if amount is None:
                    break
                out.append(amount)
            return out
        salary = _money(cell(r, price_col))
        if salary is None:
            return []
        years = int(_money(cell(r, years_col)) or 1)
        return [salary] * max(1, min(years, MAX_CONTRACT_YEARS))

    section, seen, duplicates, contracts = "ROSTER", set(), [], []
    for r in rows[header_at + 1:]:
        raw = (r[0] if r else "").strip()
        if not raw:
            continue
        label = raw.lower().rstrip(":").strip()
        if label in SECTION_LABELS:
            section = SECTION_LABELS[label]
            continue
        if label.startswith(SUMMARY_PREFIXES) or section in ("PROSPECTS", "SKIP"):
            continue
        salaries = salaries_for(r)
        if not salaries:
            continue
        name = split_fantrax_name(raw)[2] if "," in raw else raw
        if name.lower() in seen:
            duplicates.append(name)
            continue
        seen.add(name.lower())
        contracts.append({"sheet_name": name, "section": section, "salary": salaries[0],
                          "years_remaining": len(salaries), "salaries": salaries})
    return {"layout": layout, "contracts": contracts, "duplicates": duplicates}


def match_sheet_names(names: Sequence[str], roster: Sequence[Tuple[str, str]],
                      pool: Sequence[Tuple[str, str]]) -> Dict[str, Tuple[str, str]]:
    """Map sheet names to Fantrax ids. roster/pool are (fantrax_id, name) pairs.

    The team's own roster is tried first, exactly and then fuzzily (sheets are hand-typed:
    "Slavkofsky" for Slafkovsky). Fuzzy matches must be clearly better than the runner-up.
    Outside the roster only exact, unique name matches count.
    """
    roster_keys = [(fid, normalize_name(n)) for fid, n in roster]
    pool_by_key: Dict[str, List[str]] = defaultdict(list)
    for fid, n in pool:
        pool_by_key[normalize_name(n)].append(fid)
    out: Dict[str, Tuple[str, str]] = {}
    for name in names:
        key = normalize_name(name)
        exact = [fid for fid, k in roster_keys if k == key]
        if len(exact) == 1:
            out[name] = (exact[0], "roster")
            continue
        scored = sorted(((difflib.SequenceMatcher(None, key, k).ratio(), fid) for fid, k in roster_keys),
                        reverse=True)
        if scored and scored[0][0] >= 0.8 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
            out[name] = (scored[0][1], "roster (fuzzy {:.2f})".format(scored[0][0]))
            continue
        if len(pool_by_key.get(key, [])) == 1:
            out[name] = (pool_by_key[key][0], "league")
    return out


def load_salary_sources(settings: Settings) -> Dict[str, Dict[str, Any]]:
    """salary_sheets.json: {team: "url"} or {team: {"url": ..., "salary_column": "AE", "years_column": "AF"}}.

    `"owner": "app"` marks a team whose contracts live in this app: its sheet is never read again
    (it is published to instead; see publish.py).
    """
    path = settings.salary_sheets_path
    if settings.salary_sheets_json:
        data = json.loads(settings.salary_sheets_json)
    elif path.exists():
        data = json.loads(path.read_text())
    else:
        return {}
    return {str(k): (v if isinstance(v, dict) else {"url": v}) for k, v in data.items() if not str(k).startswith("_")}


def _resolve_team(conn: Connection, league_id: str, ref: str, my_team_id: Optional[str]) -> str:
    if ref == "me":
        if not my_team_id:   # the site has no FANTRAX_MY_TEAM_ID: name the team instead
            raise ValueError("salary sheets: 'me' needs FANTRAX_MY_TEAM_ID; use the team's id or name as the key")
        return my_team_id
    teams = rows(conn, "SELECT id, name FROM teams WHERE league_id=?", (league_id,))
    if any(t["id"] == ref for t in teams):
        return ref
    matches = [t["id"] for t in teams if ref.lower() in t["name"].lower()]
    if len(matches) != 1:
        raise ValueError("salary_sheets.json: '{}' doesn't match exactly one team".format(ref))
    return matches[0]


def app_owned_teams(conn: Connection, settings: Settings) -> Dict[str, Dict[str, Any]]:
    """{team_id: sheet spec} for teams whose contracts are managed in this app."""
    out = {}
    for ref, spec in load_salary_sources(settings).items():
        if spec.get("owner") != "app":
            continue
        try:
            out[_resolve_team(conn, settings.league_id, ref, settings.my_team_id)] = spec
        except ValueError:
            continue
    return out


def league_season(conn: Connection, league_id: str) -> Optional[int]:
    """The league's current season (2026 for 2026/27), as of the last league sync."""
    return (row(conn, "SELECT season_year FROM league WHERE id=?", (league_id,)) or {}).get("season_year")


def current_salaries(record: Dict[str, Any], season_year: Optional[int]) -> List[float]:
    """An app contract record's salaries from season_year on. Its list starts at the season it was
    saved in, so the seasons since then have passed; empty once the contract has run out."""
    salaries = json.loads(record["salaries"])
    if season_year and record.get("season_year"):
        salaries = salaries[max(season_year - record["season_year"], 0):]
    return salaries


def adopt_sheet_contracts(conn: Connection, league_id: str, team_id: str) -> Dict[str, Any]:
    """Move a team's sheet contracts into the app's own contract records (contract_overrides), once.

    Players who already have an app record keep it, unless it has run out. Buyout rows stay in
    `contracts`, since they have no player to attach to. Returns what was adopted.
    """
    season = league_season(conn, league_id)
    sheet_rows = rows(conn, "SELECT * FROM contracts WHERE league_id=? AND team_id=? AND section != 'BUYOUT'",
                      (league_id, team_id))
    have = {r["player_id"] for r in rows(conn, "SELECT * FROM contract_overrides WHERE league_id=?", (league_id,))
            if current_salaries(r, season)}
    adopt = [r for r in sheet_rows if r["player_id"] and r["player_id"] not in have]
    ts = now_iso()
    with transaction(conn):
        upsert(conn, "contract_overrides", [{
            "league_id": league_id, "player_id": r["player_id"], "salaries": r["salaries"],
            "contract_type": "STANDARD", "note": None, "updated_at": ts, "season_year": season}
            for r in adopt], ["league_id", "player_id"])
        conn.execute("DELETE FROM contracts WHERE league_id=? AND team_id=? AND section != 'BUYOUT'", (league_id, team_id))
    return {"adopted": len(adopt), "already_in_app": sum(1 for r in sheet_rows if r["player_id"] in have),
            "unmatched_dropped": [r["sheet_name"] for r in sheet_rows if not r["player_id"]]}


def sync_salaries(conn: Connection, settings: Settings) -> Dict[str, Any]:
    """Read each configured team salary sheet into `contracts`. Teams owned by the app are skipped
    (their sheet contracts are adopted the first time)."""
    sources = load_salary_sources(settings)
    if not sources:
        return {"skipped": "no salary sheets configured ({})".format(settings.salary_sheets_path.name)}
    league_id = settings.league_id
    lg = row(conn, "SELECT season_year FROM league WHERE id=?", (league_id,))
    if not lg:
        raise RuntimeError("League not synced yet; sync 'league' first")
    period = (row(conn, "SELECT MAX(period) AS p FROM roster_items WHERE league_id=?", (league_id,)) or {}).get("p")
    pool = [(r["fantrax_id"], r["name"]) for r in rows(conn, "SELECT p.fantrax_id, p.name FROM players p "
            "JOIN league_players lp ON lp.player_id = p.fantrax_id WHERE lp.league_id=?", (league_id,))]
    out = {}
    for ref, spec in sources.items():
        team_id = _resolve_team(conn, league_id, ref, settings.my_team_id)
        team = row(conn, "SELECT name FROM teams WHERE id=?", (team_id,))
        if spec.get("owner") == "app":
            out[team["name"] if team else team_id] = dict(
                {"owner": "app", "sheet_read": False}, **adopt_sheet_contracts(conn, league_id, team_id))
            continue
        source = spec["url"]
        parsed = parse_salary_sheet(list(csv.reader(io.StringIO(fetch_sheet_csv(source)))), lg["season_year"],
                                    salary_column=spec.get("salary_column"), years_column=spec.get("years_column"))
        contracts = parsed["contracts"]
        roster = rows(conn, "SELECT p.fantrax_id, p.name, ri.status FROM roster_items ri "
                            "JOIN players p ON p.fantrax_id = ri.player_id "
                            "WHERE ri.league_id=? AND ri.period=? AND ri.team_id=?", (league_id, period, team_id))
        players = [c for c in contracts if c["section"] != "BUYOUT"]
        matched = match_sheet_names([c["sheet_name"] for c in players],
                                    [(r["fantrax_id"], r["name"]) for r in roster], pool)
        ts = now_iso()
        records = []
        for c in contracts:
            fid, method = matched.get(c["sheet_name"], (None, None)) if c["section"] != "BUYOUT" else (None, None)
            records.append({"league_id": league_id, "team_id": team_id, "sheet_name": c["sheet_name"],
                            "player_id": fid, "match_method": method, "section": c["section"],
                            "salary": c["salary"], "years_remaining": c["years_remaining"],
                            "salaries": json.dumps(c["salaries"]), "source": source, "synced_at": ts})
        with transaction(conn):
            conn.execute("DELETE FROM contracts WHERE league_id=? AND team_id=?", (league_id, team_id))
            upsert(conn, "contracts", records, ["league_id", "team_id", "sheet_name"])
        on_sheet = {r["player_id"] for r in records if r["player_id"]}
        out[team["name"] if team else team_id] = {
            "layout": parsed["layout"],
            "contracts": len(records),
            "committed_now": sum(r["salary"] or 0 for r in records if r["section"] in ("ROSTER", "BUYOUT")),
            "duplicates_skipped": parsed["duplicates"],
            "matched": len(on_sheet),
            "fuzzy_matches": {c: m[1] for c, m in matched.items() if "fuzzy" in m[1]},
            "unmatched": [r["sheet_name"] for r in records if r["section"] != "BUYOUT" and not r["player_id"]],
            "rostered_without_salary": sorted(r["name"] for r in roster
                                              if r["status"] != "MINORS" and r["fantrax_id"] not in on_sheet),
        }
    return out


# --------------------------------------------------------------------------- orchestration


def _logged(conn: Connection, resource: str, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    log_id = conn.execute("INSERT INTO sync_log (resource, started_at, status) VALUES (?, ?, 'running') RETURNING id",
                          (resource, now_iso())).fetchall()[0][0]
    conn.commit()
    try:
        result = fn()
        status, detail = "ok", result
    except Exception as exc:  # noqa: BLE001 - recorded in sync_log; later steps still run
        status, detail = "error", {"error": repr(exc), "trace": traceback.format_exc(limit=3)}
        result = None
        conn.rollback()  # Postgres refuses further statements in a transaction that failed
    conn.execute("UPDATE sync_log SET finished_at=?, status=?, detail=? WHERE id=?",
                 (now_iso(), status, json.dumps(detail), log_id))
    conn.commit()
    return {"resource": resource, "status": status, "detail": detail}


def sync_all(conn: Connection, settings: Settings, resources: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
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
        "bios": lambda: sync_bios(conn, nhl, settings.league_id),
        "salaries": lambda: sync_salaries(conn, settings),
    }
    # Always run in dependency order regardless of the order requested.
    results = [_logged(conn, name, steps[name]) for name in RESOURCES if name in wanted]
    from . import analysis, changes   # both import this module, so they can't be imported at the top
    results.append(_logged(conn, "changes",
                           lambda: changes.record(conn, analysis.load_context(conn, settings), "Sync")))
    return results
