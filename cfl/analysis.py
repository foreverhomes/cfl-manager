"""Category analysis for an H2H-categories league.

Pipeline:
  1. Project every player's per-game production by blending this season with last
     season (weighted), shrunk slightly toward the league average for tiny samples.
  2. Convert each scoring category into a z-score against the fantasy-relevant pool
     (roughly the top N players, N = league-wide active roster slots).
  3. Build each fantasy team's projected weekly category totals from its best lineup,
     and rank teams per category.
  4. Turn a team's category standing into "need" weights and rank free agents /
     other teams' players by need-weighted value.
"""

import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .config import Settings
from .db import row, rows
from .sync import stat_seasons

# Fantrax category short name -> skater_stats column.
SKATER_STATS = {
    "G": "goals", "A": "assists", "Pt": "points", "+/-": "plus_minus", "PPP": "pp_points",
    "PPG": "pp_goals", "SHP": "sh_points", "GWG": "gw_goals", "SOG": "shots", "Hit": "hits",
    "Blk": "blocks", "PIM": "pim",
}
SKATER_COLUMNS = sorted(set(SKATER_STATS.values()))
# Goalie counting categories -> goalie_stats column; GAA and SV% are derived ratios.
GOALIE_COUNTING = {"W": "wins", "SHO": "shutouts", "L": "losses", "GA": "goals_against", "SV": "saves"}
GOALIE_RATIOS = {"GAA", "SV%"}
GOALIE_COLUMNS = ["wins", "losses", "shutouts", "goals_against", "saves", "shots_against", "toi_seconds"]

PRIOR_SEASON_GAMES = 82
GAMES_PER_WEEK = 3.5          # used only to express team projections as weekly totals
MIN_POOL_GAMES = 10           # games (this + last season) to count toward z-score baselines
NON_LINEUP_STATUSES = {"MINORS", "INJURED_RESERVE"}


class NotSynced(RuntimeError):
    pass


@dataclass
class Category:
    short_name: str
    name: str
    grp: str                  # SKATER / GOALIE
    weight: float
    lower_is_better: bool


@dataclass
class Player:
    fantrax_id: str
    name: str
    nhl_team: Optional[str]
    position: Optional[str]
    eligible: List[str]
    group: str                # F / D / G
    nhl_id: Optional[int]
    gp_cur: int = 0
    gp_prior: int = 0
    team_id: Optional[str] = None
    roster_status: Optional[str] = None
    adp: Optional[float] = None
    # Per game for skaters; per *team* game for goalies (already scaled by start share).
    rates: Dict[str, float] = field(default_factory=dict)
    start_share: Optional[float] = None
    display: Dict[str, float] = field(default_factory=dict)   # category value shown to humans
    impact: Dict[str, float] = field(default_factory=dict)    # oriented so higher = better
    z: Dict[str, float] = field(default_factory=dict)

    @property
    def has_stats(self) -> bool:
        return self.gp_cur + self.gp_prior > 0

    def value(self, weights: Dict[str, float]) -> float:
        return sum(self.z.get(c, 0.0) * w for c, w in weights.items())


@dataclass
class Context:
    league_id: str
    league: Dict
    categories: List[Category]
    teams: Dict[str, str]
    roster_period: Optional[int]
    players: Dict[str, Player]
    slots: Dict[str, int]
    cur_season: int
    prior_season: int
    unsupported_categories: List[str]

    def cats(self, grp: str) -> List[Category]:
        return [c for c in self.categories if c.grp == grp and c.short_name not in self.unsupported_categories]

    def league_weights(self, grp: str) -> Dict[str, float]:
        return {c.short_name: c.weight for c in self.cats(grp)}

    def roster(self, team_id: str, statuses: Optional[Iterable[str]] = None) -> List[Player]:
        wanted = set(statuses) if statuses else None
        return [p for p in self.players.values()
                if p.team_id == team_id and (wanted is None or p.roster_status in wanted)]


# --------------------------------------------------------------------------- projection


def _blend(cur: float, cur_gp: float, prior: float, prior_gp: float, w: float, k: float, pop_rate: float) -> float:
    return (cur + w * prior + k * pop_rate) / (cur_gp + w * prior_gp + k)


def _group_for(position: Optional[str]) -> str:
    if position == "G":
        return "G"
    if position == "D":
        return "D"
    return "F"


def _project_skaters(conn: sqlite3.Connection, ctx: Context, settings: Settings) -> None:
    w, k = settings.prior_season_weight, settings.shrink_games
    stats = defaultdict(dict)
    for r in rows(conn, "SELECT * FROM skater_stats WHERE season_id IN (?, ?)", (ctx.cur_season, ctx.prior_season)):
        stats[r["nhl_id"]][r["season_id"]] = r

    skaters = [p for p in ctx.players.values() if p.group != "G" and p.nhl_id in stats]
    # League-average per-game rates by position group: the shrinkage target.
    pop: Dict[str, Dict[str, float]] = {}
    for grp in ("F", "D"):
        num = defaultdict(float)
        den = 0.0
        for p in skaters:
            if p.group != grp:
                continue
            for season, weight in ((ctx.cur_season, 1.0), (ctx.prior_season, w)):
                s = stats[p.nhl_id].get(season)
                if s and s["gp"]:
                    den += weight * s["gp"]
                    for col in SKATER_COLUMNS:
                        num[col] += weight * (s[col] or 0)
        pop[grp] = {col: (num[col] / den if den else 0.0) for col in SKATER_COLUMNS}

    for p in skaters:
        cur = stats[p.nhl_id].get(ctx.cur_season) or {}
        prior = stats[p.nhl_id].get(ctx.prior_season) or {}
        p.gp_cur, p.gp_prior = cur.get("gp") or 0, prior.get("gp") or 0
        for col in SKATER_COLUMNS:
            p.rates[col] = _blend(cur.get(col) or 0, p.gp_cur, prior.get(col) or 0, p.gp_prior, w, k,
                                  pop[p.group][col])
        for c in ctx.cats("SKATER"):
            v = p.rates[SKATER_STATS[c.short_name]]
            p.display[c.short_name] = v
            p.impact[c.short_name] = -v if c.lower_is_better else v


def _project_goalies(conn: sqlite3.Connection, ctx: Context, settings: Settings) -> None:
    w, k = settings.prior_season_weight, settings.shrink_games
    stats = defaultdict(dict)
    for r in rows(conn, "SELECT * FROM goalie_stats WHERE season_id IN (?, ?)", (ctx.cur_season, ctx.prior_season)):
        stats[r["nhl_id"]][r["season_id"]] = r
    # Team games so far this season ~ the most games any skater has played.
    team_games_cur = (row(conn, "SELECT MAX(gp) AS g FROM skater_stats WHERE season_id=?", (ctx.cur_season,))
                      or {}).get("g") or 0

    goalies = [p for p in ctx.players.values() if p.group == "G" and p.nhl_id in stats]
    totals = defaultdict(float)
    gp_total = 0.0
    for p in goalies:
        for season, weight in ((ctx.cur_season, 1.0), (ctx.prior_season, w)):
            s = stats[p.nhl_id].get(season)
            if s and s["gp"]:
                gp_total += weight * s["gp"]
                for col in GOALIE_COLUMNS:
                    totals[col] += weight * (s[col] or 0)
    pop = {col: (totals[col] / gp_total if gp_total else 0.0) for col in GOALIE_COLUMNS}
    avg_gaa = totals["goals_against"] / totals["toi_seconds"] * 3600 if totals["toi_seconds"] else 0.0
    avg_sv = totals["saves"] / totals["shots_against"] if totals["shots_against"] else 0.0

    for p in goalies:
        cur = stats[p.nhl_id].get(ctx.cur_season) or {}
        prior = stats[p.nhl_id].get(ctx.prior_season) or {}
        p.gp_cur, p.gp_prior = cur.get("gp") or 0, prior.get("gp") or 0
        per_gp = {col: _blend(cur.get(col) or 0, p.gp_cur, prior.get(col) or 0, p.gp_prior, w, k, pop[col])
                  for col in GOALIE_COLUMNS}
        # Share of team games this goalie plays; last season only counts if he was in the NHL.
        prior_games = w * PRIOR_SEASON_GAMES if p.gp_prior else 0.0
        p.start_share = min(1.0, (p.gp_cur + w * p.gp_prior + k * 0.5) / (team_games_cur + prior_games + k))
        p.rates = {col: v * p.start_share for col, v in per_gp.items()}

        gaa = per_gp["goals_against"] / per_gp["toi_seconds"] * 3600 if per_gp["toi_seconds"] else avg_gaa
        sv = per_gp["saves"] / per_gp["shots_against"] if per_gp["shots_against"] else avg_sv
        for c in ctx.cats("GOALIE"):
            if c.short_name == "GAA":
                p.display["GAA"] = gaa
                # Goals saved vs. an average goalie, per team game.
                p.impact["GAA"] = (avg_gaa - gaa) * p.rates["toi_seconds"] / 3600
            elif c.short_name == "SV%":
                p.display["SV%"] = sv
                p.impact["SV%"] = (sv - avg_sv) * p.rates["shots_against"]
            else:
                v = p.rates[GOALIE_COUNTING[c.short_name]]
                p.display[c.short_name] = v
                p.impact[c.short_name] = -v if c.lower_is_better else v


def _mean_std(values: Sequence[float]) -> Tuple[float, float]:
    if not values:
        return 0.0, 1.0
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / len(values)
    return m, (math.sqrt(var) or 1.0)


def _assign_z(players: List[Player], cats: List[Category], pool_size: int) -> None:
    """z-scores per category against the top `pool_size` players (two passes)."""
    names = [c.short_name for c in cats]
    pool = [p for p in players if p.gp_cur + p.gp_prior >= MIN_POOL_GAMES] or players
    for _ in range(2):
        params = {c: _mean_std([p.impact[c] for p in pool]) for c in names}
        for p in players:
            p.z = {c: (p.impact[c] - params[c][0]) / params[c][1] for c in names}
        pool = sorted(pool, key=lambda p: sum(p.z.values()), reverse=True)[:pool_size]


def load_context(conn: sqlite3.Connection, settings: Settings) -> Context:
    league_id = settings.league_id
    league = row(conn, "SELECT * FROM league WHERE id=?", (league_id,))
    if not league:
        raise NotSynced("League {} not synced yet: run `python -m cfl sync`".format(league_id))
    categories = [Category(r["short_name"], r["name"], r["grp"], r["weight"], bool(r["lower_is_better"]))
                  for r in rows(conn, "SELECT * FROM scoring_categories WHERE league_id=? ORDER BY sort_order",
                                (league_id,))]
    unsupported = [c.short_name for c in categories
                   if not (c.short_name in SKATER_STATS if c.grp == "SKATER"
                           else c.short_name in GOALIE_COUNTING or c.short_name in GOALIE_RATIOS)]
    teams = {r["id"]: r["name"] for r in rows(conn, "SELECT id, name FROM teams WHERE league_id=? ORDER BY name",
                                               (league_id,))}
    rp = row(conn, "SELECT MAX(period) AS p FROM roster_items WHERE league_id=?", (league_id,))
    roster_period = rp["p"] if rp else None
    roster = {r["player_id"]: r for r in rows(conn, "SELECT * FROM roster_items WHERE league_id=? AND period=?",
                                              (league_id, roster_period))}
    pool = {r["player_id"]: r for r in rows(conn, "SELECT * FROM league_players WHERE league_id=?", (league_id,))}

    players: Dict[str, Player] = {}
    for r in rows(conn, "SELECT * FROM players"):
        fid = r["fantrax_id"]
        if fid not in pool and fid not in roster:
            continue
        lp = pool.get(fid) or {}
        ri = roster.get(fid) or {}
        eligible = [e for e in (lp.get("eligible_pos") or r["position"] or "").split(",") if e]
        players[fid] = Player(
            fantrax_id=fid, name=r["name"], nhl_team=r["nhl_team"], position=r["position"], eligible=eligible,
            group=_group_for(r["position"]), nhl_id=r["nhl_id"], team_id=ri.get("team_id"),
            roster_status=ri.get("status"), adp=r["adp"],
        )

    roster_info = json.loads(league["roster_info"] or "{}")
    slots = {pos: c.get("maxActive", 0) for pos, c in (roster_info.get("positionConstraints") or {}).items()}
    cur_season, prior_season = stat_seasons(conn, league_id)
    ctx = Context(league_id, league, categories, teams, roster_period, players, slots, cur_season, prior_season,
                  unsupported)

    _project_skaters(conn, ctx, settings)
    _project_goalies(conn, ctx, settings)
    n_teams = max(len(teams), 1)
    goalie_slots = slots.get("G", 2)
    skater_slots = max(sum(slots.values()) - goalie_slots, 1)
    projected = [p for p in players.values() if p.impact]
    _assign_z([p for p in projected if p.group != "G"], ctx.cats("SKATER"), n_teams * skater_slots)
    _assign_z([p for p in projected if p.group == "G"], ctx.cats("GOALIE"), n_teams * goalie_slots)
    return ctx


# --------------------------------------------------------------------------- lineups & team profiles


def best_lineup(ctx: Context, team_id: str) -> List[Player]:
    """Greedy best active lineup from the team's non-minors, non-IR players by league-weight value."""
    candidates = [p for p in ctx.roster(team_id) if p.roster_status not in NON_LINEUP_STATUSES and p.impact]
    weights = {"S": ctx.league_weights("SKATER"), "G": ctx.league_weights("GOALIE")}
    candidates.sort(key=lambda p: player_value(p, weights), reverse=True)
    open_slots = dict(ctx.slots)
    lineup = []
    for p in candidates:
        # Specific positions first, then flex (F); a skater fills at most one slot.
        specific = [e for e in p.eligible if e != "F" and open_slots.get(e, 0) > 0]
        flex = ["F"] if "F" in p.eligible and open_slots.get("F", 0) > 0 else []
        for slot in specific + flex:
            open_slots[slot] -= 1
            lineup.append(p)
            break
    return lineup


def _team_totals(ctx: Context, lineup: List[Player]) -> Dict[str, Optional[float]]:
    totals: Dict[str, Optional[float]] = {}
    skaters = [p for p in lineup if p.group != "G"]
    goalies = [p for p in lineup if p.group == "G"]
    for c in ctx.cats("SKATER"):
        totals[c.short_name] = sum(p.rates[SKATER_STATS[c.short_name]] for p in skaters) * GAMES_PER_WEEK
    g = lambda col: sum(p.rates[col] for p in goalies)  # noqa: E731
    for c in ctx.cats("GOALIE"):
        if c.short_name == "GAA":
            totals["GAA"] = g("goals_against") / g("toi_seconds") * 3600 if g("toi_seconds") else None
        elif c.short_name == "SV%":
            totals["SV%"] = g("saves") / g("shots_against") if g("shots_against") else None
        else:
            totals[c.short_name] = g(GOALIE_COUNTING[c.short_name]) * GAMES_PER_WEEK
    return totals


def _rank_and_z(ctx: Context, values: Dict[str, Dict[str, Optional[float]]]) -> Dict[str, Dict[str, Dict]]:
    """values: team_id -> category -> value. Returns team -> category -> {value, rank, z} (z: higher = better)."""
    out: Dict[str, Dict[str, Dict]] = {t: {} for t in values}
    for c in ctx.cats("SKATER") + ctx.cats("GOALIE"):
        present = {t: v[c.short_name] for t, v in values.items() if v.get(c.short_name) is not None}
        sign = -1.0 if c.lower_is_better else 1.0
        m, s = _mean_std([sign * v for v in present.values()])
        ordered = sorted(present, key=lambda t: sign * present[t], reverse=True)
        for t in values:
            v = values[t].get(c.short_name)
            out[t][c.short_name] = {
                "value": v,
                "rank": ordered.index(t) + 1 if t in present else None,
                "z": (sign * v - m) / s if v is not None else None,
            }
    return out


def projected_profiles(ctx: Context, lineup: str = "best") -> Dict[str, Dict]:
    """Projected weekly category totals and league rank for every team."""
    values, lineups = {}, {}
    for team_id in ctx.teams:
        if lineup == "active":
            players = [p for p in ctx.roster(team_id, ["ACTIVE"]) if p.impact]
        else:
            players = best_lineup(ctx, team_id)
        lineups[team_id] = players
        values[team_id] = _team_totals(ctx, players)
    ranked = _rank_and_z(ctx, values)
    return {t: {"team_id": t, "team_name": ctx.teams[t], "categories": ranked[t], "lineup": lineups[t]}
            for t in ctx.teams}


def actual_profiles(conn: sqlite3.Connection, ctx: Context) -> Dict[str, Dict]:
    """Season-to-date H2H results per category, from stored matchup scores."""
    data = rows(conn, "SELECT period, team_id, category, value, outcome FROM matchup_category_scores "
                      "WHERE league_id=? ORDER BY period", (ctx.league_id,))
    records: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"W": 0, "L": 0, "T": 0}))
    per_period: Dict[str, Dict[str, Dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in data:
        records[r["team_id"]][r["category"]][(r["outcome"] or "T")[0]] += 1
        if r["value"] is not None:
            per_period[r["team_id"]][r["category"]][r["period"]] = r["value"]  # same in every matchup that period
    values: Dict[str, Dict[str, Optional[float]]] = {}
    for t in ctx.teams:
        values[t] = {}
        for c in ctx.cats("SKATER") + ctx.cats("GOALIE"):
            pv = list(per_period[t][c.short_name].values())
            if not pv:
                values[t][c.short_name] = None
            elif c.short_name in GOALIE_RATIOS:
                values[t][c.short_name] = sum(pv) / len(pv)
            else:
                values[t][c.short_name] = sum(pv)
    ranked = _rank_and_z(ctx, values)
    periods = sorted({r["period"] for r in data})
    out = {}
    for t in ctx.teams:
        cats = {}
        for c, info in ranked[t].items():
            rec = records[t][c]
            cats[c] = dict(info, record="{W}-{L}-{T}".format(**rec))
        out[t] = {"team_id": t, "team_name": ctx.teams[t], "periods": periods, "categories": cats}
    return out


# --------------------------------------------------------------------------- needs & targets


def need_weights(ctx: Context, team_id: str, profiles: Dict[str, Dict], strategy: str = "marginal",
                 spread: float = 1.5, punt: Sequence[str] = (), overrides: Optional[Dict[str, float]] = None
                 ) -> Dict[str, float]:
    """How much an extra unit of each category is worth to `team_id`.

    marginal: weight by how many opponents are close to you in that category (Gaussian kernel on
              the team-level z gap). Categories you dominate or are hopeless in get little weight —
              that is where an extra unit flips the fewest weekly matchups.
    weakness: linear in league rank (worst category = 1.5, best = 0.5).
    even:     every category weighted equally.
    Weights are normalised to average 1, then scaled by the league's own category weights.
    """
    me = profiles[team_id]["categories"]
    others = [p["categories"] for t, p in profiles.items() if t != team_id]
    n = len(profiles)
    raw: Dict[str, float] = {}
    for c in ctx.cats("SKATER") + ctx.cats("GOALIE"):
        name = c.short_name
        z_me, rank = me[name]["z"], me[name]["rank"]
        if strategy == "even" or z_me is None:
            raw[name] = 1.0
        elif strategy == "weakness":
            raw[name] = 0.5 + ((rank or 1) - 1) / max(n - 1, 1)
        elif strategy == "marginal":
            gaps = [z_me - o[name]["z"] for o in others if o[name]["z"] is not None]
            raw[name] = sum(math.exp(-(g * g) / (2 * spread * spread)) for g in gaps) / max(len(gaps), 1)
        else:
            raise ValueError("strategy must be one of: marginal, weakness, even")
    mean = sum(raw.values()) / len(raw) if raw else 1.0
    weights = {}
    for c in ctx.cats("SKATER") + ctx.cats("GOALIE"):
        w = raw[c.short_name] / mean if mean else 1.0
        if c.short_name in punt:
            w = 0.0
        if overrides and c.short_name in overrides:
            w = overrides[c.short_name]
        weights[c.short_name] = w * c.weight
    return weights


def _split(weights: Dict[str, float], ctx: Context) -> Dict[str, Dict[str, float]]:
    sk = {c.short_name for c in ctx.cats("SKATER")}
    return {"S": {c: w for c, w in weights.items() if c in sk},
            "G": {c: w for c, w in weights.items() if c not in sk}}


def player_value(p: Player, weights: Dict[str, Dict[str, float]]) -> float:
    return p.value(weights["G"] if p.group == "G" else weights["S"])


def _matches_position(p: Player, position: Optional[str]) -> bool:
    if not position:
        return True
    position = position.upper()
    if position in ("F", "D", "G"):
        return p.group == position
    return position in p.eligible


def player_summary(p: Player, ctx: Context, weights: Optional[Dict[str, Dict[str, float]]] = None) -> Dict:
    generic = _split({c.short_name: c.weight for c in ctx.categories}, ctx)
    out = {
        "fantrax_id": p.fantrax_id, "name": p.name, "nhl_team": p.nhl_team, "position": p.position,
        "eligible": p.eligible, "fantasy_team_id": p.team_id,
        "fantasy_team": ctx.teams.get(p.team_id) if p.team_id else None,
        "roster_status": p.roster_status, "gp_this_season": p.gp_cur, "gp_last_season": p.gp_prior,
        "adp": p.adp,
        "per_game": {c: round(v, 3) for c, v in p.display.items()},
        "z": {c: round(v, 2) for c, v in p.z.items()},
        "value": round(player_value(p, generic), 2) if p.z else None,
    }
    if p.start_share is not None:
        out["start_share"] = round(p.start_share, 2)
    if weights is not None and p.z:
        out["need_value"] = round(player_value(p, weights), 2)
    return out


def team_profile(conn: sqlite3.Connection, ctx: Context, team_id: str, **need_kwargs) -> Dict:
    lineup_mode = need_kwargs.pop("lineup", "best")
    projected = projected_profiles(ctx, lineup_mode)
    actual = actual_profiles(conn, ctx)
    weights = need_weights(ctx, team_id, projected, **need_kwargs)
    w_split = _split(weights, ctx)
    n = len(ctx.teams)
    cats = []
    for c in ctx.cats("SKATER") + ctx.cats("GOALIE"):
        pj = projected[team_id]["categories"][c.short_name]
        ac = actual[team_id]["categories"][c.short_name]
        cats.append({
            "category": c.short_name, "name": c.name, "group": c.grp,
            "projected_per_week": _r(pj["value"], 3 if c.short_name in GOALIE_RATIOS else 2),
            "projected_rank": pj["rank"], "projected_z": _r(pj["z"], 2),
            # Share of the other teams you'd out-project in this category.
            "projected_beats_pct": round((n - pj["rank"]) / max(n - 1, 1), 2) if pj["rank"] else None,
            "actual_value": _r(ac["value"], 3), "actual_rank": ac["rank"], "actual_record": ac["record"],
            "need_weight": round(weights[c.short_name], 2),
        })
    # Skaters then goalies; players without NHL stats (prospects) last.
    roster = sorted(ctx.roster(team_id),
                    key=lambda p: (p.group == "G", not p.z, -player_value(p, w_split) if p.z else 0))
    lineup_ids = {p.fantrax_id for p in projected[team_id]["lineup"]}
    return {
        "team_id": team_id, "team_name": ctx.teams[team_id], "roster_period": ctx.roster_period,
        "lineup_mode": lineup_mode,
        "projected_total_z": round(sum(v["z"] or 0 for v in projected[team_id]["categories"].values()), 2),
        "projected_overall_rank": _overall_rank(projected, team_id),
        "categories": cats,
        "strengths": [c["category"] for c in sorted(cats, key=lambda c: c["projected_rank"] or n)[:3]],
        "weaknesses": [c["category"] for c in sorted(cats, key=lambda c: -(c["projected_rank"] or 0))[:3]],
        "roster": [dict(player_summary(p, ctx, w_split), in_projected_lineup=p.fantrax_id in lineup_ids)
                   for p in roster],
        "unsupported_categories": ctx.unsupported_categories,
    }


def league_overview(conn: sqlite3.Connection, ctx: Context, lineup: str = "best") -> List[Dict]:
    projected = projected_profiles(ctx, lineup)
    actual = actual_profiles(conn, ctx)
    out = []
    for t in ctx.teams:
        out.append({
            "team_id": t, "team_name": ctx.teams[t],
            "projected_total_z": round(sum(v["z"] or 0 for v in projected[t]["categories"].values()), 2),
            "projected_overall_rank": _overall_rank(projected, t),
            "projected_ranks": {c: v["rank"] for c, v in projected[t]["categories"].items()},
            "actual_records": {c: v["record"] for c, v in actual[t]["categories"].items()},
        })
    return sorted(out, key=lambda r: r["projected_overall_rank"])


def _overall_rank(projected: Dict[str, Dict], team_id: str) -> int:
    totals = {t: sum(v["z"] or 0 for v in p["categories"].values()) for t, p in projected.items()}
    return sorted(totals, key=totals.get, reverse=True).index(team_id) + 1


def targets(conn: sqlite3.Connection, ctx: Context, team_id: str, source: str = "fa", position: Optional[str] = None,
            limit: int = 25, min_games: int = 5, include_minors: bool = False, **need_kwargs) -> Dict:
    """Players to go after, ranked by value to `team_id` given its category needs.

    source="fa": unrostered players, each paired with the weakest droppable player in the same
                 position group on your roster ("upgrade" = need value gained by the swap).
    source="trade": players on other teams.
    """
    lineup_mode = need_kwargs.pop("lineup", "best")
    projected = projected_profiles(ctx, lineup_mode)
    weights = _split(need_weights(ctx, team_id, projected, **need_kwargs), ctx)

    def eligible(p: Player) -> bool:
        if not p.z or p.gp_cur + p.gp_prior < min_games or not _matches_position(p, position):
            return False
        if source == "fa":
            return p.team_id is None
        if source == "trade":
            return p.team_id not in (None, team_id) and (include_minors or p.roster_status != "MINORS")
        raise ValueError("source must be 'fa' or 'trade'")

    mine = [p for p in ctx.roster(team_id) if p.roster_status != "MINORS" and p.z]
    weakest = {}
    for grp in ("F", "D", "G"):
        group = [p for p in mine if p.group == grp]
        if group:
            weakest[grp] = min(group, key=lambda p: player_value(p, weights))

    def rank_key(p: Player) -> float:
        # For pickups, rank by gain over the player you'd drop: that compares skaters and
        # goalies on the same footing (their z-sums cover different numbers of categories).
        drop = weakest.get(p.group) if source == "fa" else None
        return player_value(p, weights) - (player_value(drop, weights) if drop else 0.0)

    pool = sorted((p for p in ctx.players.values() if eligible(p)), key=rank_key, reverse=True)[:limit]
    results = []
    for p in pool:
        item = player_summary(p, ctx, weights)
        drop = weakest.get(p.group)
        if source == "fa" and drop is not None:
            item["replaces"] = {"fantrax_id": drop.fantrax_id, "name": drop.name,
                                "need_value": round(player_value(drop, weights), 2)}
            item["upgrade"] = round(rank_key(p), 2)
        results.append(item)
    return {
        "team_id": team_id, "team_name": ctx.teams[team_id], "source": source,
        "need_weights": {c: round(w, 2) for c, w in {**weights["S"], **weights["G"]}.items()},
        "targets": results,
    }


def trade_partners(conn: sqlite3.Connection, ctx: Context, team_id: str, per_team: int = 3, **need_kwargs) -> Dict:
    """Rank other teams by how well their category needs complement yours.

    fit = sum over categories of (your edge x their need) + (their edge x your need), using
    team-level projected z-scores. For each partner, list what you'd want from them and what of
    yours they'd value most (by their own need weights).
    """
    lineup_mode = need_kwargs.pop("lineup", "best")
    projected = projected_profiles(ctx, lineup_mode)
    my_w = need_weights(ctx, team_id, projected, **need_kwargs)
    my_split = _split(my_w, ctx)
    my_cats = projected[team_id]["categories"]
    tradeable = lambda t: [p for p in ctx.roster(t) if p.roster_status != "MINORS" and p.z]  # noqa: E731

    partners = []
    for t in ctx.teams:
        if t == team_id:
            continue
        their_w = need_weights(ctx, t, projected, strategy=need_kwargs.get("strategy", "marginal"),
                               spread=need_kwargs.get("spread", 1.5))
        their_split = _split(their_w, ctx)
        their_cats = projected[t]["categories"]
        fit = 0.0
        give, get = [], []
        for c in my_w:
            zm, zt = my_cats[c]["z"], their_cats[c]["z"]
            if zm is None or zt is None:
                continue
            edge = zm - zt
            fit += max(edge, 0) * their_w[c] + max(-edge, 0) * my_w[c]
            if edge > 0.5:
                give.append(c)
            elif edge < -0.5:
                get.append(c)
        want = sorted(tradeable(t), key=lambda p: player_value(p, my_split), reverse=True)[:per_team]
        offer = sorted(tradeable(team_id), key=lambda p: player_value(p, their_split), reverse=True)[:per_team]
        partners.append({
            "team_id": t, "team_name": ctx.teams[t], "fit": round(fit, 2),
            "you_can_supply": give, "they_can_supply": get,
            "players_you_want": [_brief(p, my_split) for p in want],
            "your_players_they_want": [_brief(p, their_split) for p in offer],
        })
    partners.sort(key=lambda r: r["fit"], reverse=True)

    generic = _split({c.short_name: c.weight for c in ctx.categories}, ctx)
    # Only players with real league value (above the average fantasy starter) are worth shopping.
    assets = [p for p in tradeable(team_id) if player_value(p, generic) > 0] or tradeable(team_id)
    chips = sorted(assets, key=lambda p: player_value(p, generic) - player_value(p, my_split), reverse=True)[:8]
    return {
        "team_id": team_id, "team_name": ctx.teams[team_id],
        "need_weights": {c: round(w, 2) for c, w in my_w.items()},
        # Players the league values more than you do, given your needs: natural trade bait.
        "trade_chips": [dict(_brief(p, my_split), league_value=round(player_value(p, generic), 2)) for p in chips],
        "partners": partners,
    }


def _brief(p: Player, weights: Dict[str, Dict[str, float]]) -> Dict:
    top = sorted(p.z.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return {"fantrax_id": p.fantrax_id, "name": p.name, "position": ",".join(e for e in p.eligible if e != "F"),
            "nhl_team": p.nhl_team, "value": round(player_value(p, weights), 2),
            "best_categories": [c for c, _ in top]}


def _r(v: Optional[float], nd: int) -> Optional[float]:
    return None if v is None else round(v, nd)
