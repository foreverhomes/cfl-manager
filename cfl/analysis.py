"""Category analysis for an H2H-categories league.

Pipeline:
  1. Project every player's per-game production by blending this season with last
     season (weighted), shrunk slightly toward the league average for tiny samples.
  2. Convert each scoring category into a z-score against the fantasy-relevant pool
     (roughly the top N players, N = league-wide active roster slots).
  3. Build each fantasy team's projected weekly category totals by filling every remaining
     day's lineup with the best players whose NHL team plays that day, and rank teams per category.
  4. Turn a team's category standing into "need" weights and rank free agents /
     other teams' players by need-weighted value.
"""

import json
import math
from collections import defaultdict
from datetime import date
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .config import Settings
from .db import Connection, row, rows
from .sync import MAX_CONTRACT_YEARS, app_owned_teams, current_period, current_salaries, stat_seasons

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
# Fantrax lists categories in no particular order; every view shows them the way a stat line
# reads (NHL.com's), skaters first.
STAT_LINE_ORDER = ("G", "A", "Pt", "+/-", "PPG", "PPP", "SHP", "GWG", "SOG", "Hit", "Blk", "PIM",
                   "W", "L", "GA", "GAA", "SV", "SV%", "SHO")


def stat_line_key(short_name: str, grp: str) -> Tuple[bool, int]:
    order = STAT_LINE_ORDER.index(short_name) if short_name in STAT_LINE_ORDER else len(STAT_LINE_ORDER)
    return grp != "SKATER", order

PRIOR_SEASON_GAMES = 82
GAMES_PER_WEEK = 3.5          # fallback when no NHL schedule is synced
MIN_POOL_GAMES = 10           # games (this + last season) to count toward z-score baselines
NON_LINEUP_STATUSES = {"MINORS", "INJURED_RESERVE"}
CAPPED_STATUSES = {"ACTIVE", "RESERVE"}   # what counts against the roster limit and the salary cap

# League rules: cap fallback when Fantrax doesn't report one, the contract-year bank, and the
# release / buyout threshold.
DEFAULT_SALARY_CAP = 220.0
CONTRACT_YEAR_BANK = 50
FREE_RELEASE_MAX_SALARY = 3.0
# A player whose contract ends becomes a UFA if he is this old on July 1 after his last season,
# otherwise an RFA. In-season UFA pickups go back to the auction as UFAs whatever their age.
UFA_AGE = 27


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
    birth_date: Optional[str] = None           # YYYY-MM-DD
    # From the team salary sheet, when loaded: current salary, seasons left (incl. this one),
    # and the salary for each of those seasons.
    salary: Optional[float] = None
    contract_years: Optional[int] = None
    contract_salaries: List[float] = field(default_factory=list)
    contract_type: str = "STANDARD"            # STANDARD / ELC / IN_SEASON_UFA
    contract_source: Optional[str] = None      # "sheet", "override" (app edit over a sheet), "app" (app-owned team)
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
    _trade_ranks: Optional[Dict[str, int]] = field(default=None, repr=False)
    # NHL teams playing on each remaining game day, and the calendar days in that window.
    schedule: List[frozenset] = field(default_factory=list)
    horizon_days: int = 0
    roster_cap: Optional[int] = None
    salary_caps: Dict[str, float] = field(default_factory=dict)
    contract_teams: set = field(default_factory=set)          # teams with a salary sheet loaded
    app_owned: set = field(default_factory=set)               # teams whose contracts live in this app
    buyouts: Dict[str, List[List[float]]] = field(default_factory=dict)   # team -> per-season dead cap
    _equiv_ranks: Dict[str, float] = field(default_factory=dict, repr=False)
    _market: Optional[Dict[str, float]] = field(default=None, repr=False)

    def cats(self, grp: str) -> List[Category]:
        return [c for c in self.categories if c.grp == grp and c.short_name not in self.unsupported_categories]

    def league_weights(self, grp: str) -> Dict[str, float]:
        return {c.short_name: c.weight for c in self.cats(grp)}

    def roster(self, team_id: str, statuses: Optional[Iterable[str]] = None) -> List[Player]:
        wanted = set(statuses) if statuses else None
        return [p for p in self.players.values()
                if p.team_id == team_id and (wanted is None or p.roster_status in wanted)]

    def resolve_team(self, value: str) -> Optional[str]:
        """Team id from an exact id or a unique, case-insensitive piece of the team name."""
        if value in self.teams:
            return value
        matches = [t for t, name in self.teams.items() if value.lower() in name.lower()]
        return matches[0] if len(matches) == 1 else None

    def resolve_player(self, value: str, team_id: str) -> Optional[str]:
        """Player id on `team_id` from an exact id or a unique, case-insensitive piece of the name."""
        roster = self.roster(team_id)
        if any(p.fantrax_id == value for p in roster):
            return value
        matches = [p.fantrax_id for p in roster if value.lower() in p.name.lower()]
        return matches[0] if len(matches) == 1 else None

    def trade_ranks(self) -> Dict[str, int]:
        """League-wide market rank (1 = best) on one scale for skaters and goalies.

        Each group is ranked by equal-weight value; a goalie's rank is then multiplied by
        skater slots / goalie slots, so the last starting goalie lines up with the last starting
        skater (with 18 skater and 2 goalie slots, the #1 goalie sits level with the #9 skater).
        """
        if self._trade_ranks is None:
            goalie_slots = max(self.slots.get("G", 0), 1)
            ratio = max(sum(self.slots.values()) - self.slots.get("G", 0), 1) / goalie_slots
            weights = {"S": self.league_weights("SKATER"), "G": self.league_weights("GOALIE")}
            equiv = {}
            for is_goalie in (False, True):
                group = sorted((p for p in self.players.values() if p.z and (p.group == "G") == is_goalie),
                               key=lambda p: player_value(p, weights), reverse=True)
                for i, p in enumerate(group, 1):
                    equiv[p.fantrax_id] = (i * ratio if is_goalie else i, is_goalie)
            self._equiv_ranks = {fid: e[0] for fid, e in equiv.items()}
            self._trade_ranks = {fid: i for i, fid in enumerate(sorted(equiv, key=equiv.get), 1)}
        return self._trade_ranks

    def market_values(self) -> Dict[str, float]:
        """Additive market value: value over replacement on one curve for skaters and goalies.

        A player's value is the equal-weight skater value at his skater-equivalent rank (goalies
        mapped as in trade_ranks), minus the value of the last fantasy starter (rank = teams x
        skater slots), floored at 0. Depth players are worth ~0, so a pile of them never adds
        up to a star.
        """
        if self._market is None:
            self.trade_ranks()
            weights = {"S": self.league_weights("SKATER"), "G": self.league_weights("GOALIE")}
            curve = sorted((player_value(p, weights) for p in self.players.values() if p.z and p.group != "G"),
                           reverse=True)
            if not curve:
                self._market = {}
                return self._market
            skater_slots = max(sum(self.slots.values()) - self.slots.get("G", 0), 1)
            replacement = curve[min(len(self.teams) * skater_slots, len(curve)) - 1]

            def at(rank: float) -> float:
                i = min(max(rank, 1.0), float(len(curve))) - 1
                lo = int(math.floor(i))
                hi = min(lo + 1, len(curve) - 1)
                return curve[lo] + (curve[hi] - curve[lo]) * (i - lo)

            self._market = {fid: max(0.0, at(r) - replacement) for fid, r in self._equiv_ranks.items()}
        return self._market

    def equivalent_rank(self, market_value: float) -> int:
        """Trade rank of the best player whose market value doesn't exceed `market_value`."""
        ranks, market = self.trade_ranks(), self.market_values()
        for fid in sorted(ranks, key=ranks.get):
            if market.get(fid, 0.0) <= market_value + 1e-9:
                return ranks[fid]
        return len(ranks)


def comparable_band(rank: int, tolerance: float, min_window: int = 3) -> Tuple[int, int]:
    """Trade ranks within ±tolerance of `rank` (multiplicative, so #40 at 0.25 -> #32-#50),
    never narrower than ±min_window ranks so elite players still have comparables."""
    lo = min(rank / (1 + tolerance), rank - min_window)
    hi = max(rank * (1 + tolerance), rank + min_window)
    return max(1, math.floor(lo)), math.ceil(hi)


# --------------------------------------------------------------------------- projection


def _blend(cur: float, cur_gp: float, prior: float, prior_gp: float, w: float, k: float, pop_rate: float) -> float:
    return (cur + w * prior + k * pop_rate) / (cur_gp + w * prior_gp + k)


def _group_for(position: Optional[str]) -> str:
    if position == "G":
        return "G"
    if position == "D":
        return "D"
    return "F"


def _project_skaters(conn: Connection, ctx: Context, settings: Settings) -> None:
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


def _project_goalies(conn: Connection, ctx: Context, settings: Settings) -> None:
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


def load_context(conn: Connection, settings: Settings) -> Context:
    league_id = settings.league_id
    league = row(conn, "SELECT * FROM league WHERE id=?", (league_id,))
    if not league:
        raise NotSynced("League {} not synced yet: run `python -m cfl sync`".format(league_id))
    categories = sorted((Category(r["short_name"], r["name"], r["grp"], r["weight"], bool(r["lower_is_better"]))
                         for r in rows(conn, "SELECT * FROM scoring_categories WHERE league_id=? ORDER BY sort_order",
                                       (league_id,))), key=lambda c: stat_line_key(c.short_name, c.grp))
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
            roster_status=ri.get("status"), adp=r["adp"], birth_date=r["birth_date"],
        )

    roster_info = json.loads(league["roster_info"] or "{}")
    slots = {pos: c.get("maxActive", 0) for pos, c in (roster_info.get("positionConstraints") or {}).items()}
    cur_season, prior_season = stat_seasons(conn, league_id)
    ctx = Context(league_id, league, categories, teams, roster_period, players, slots, cur_season, prior_season,
                  unsupported, roster_cap=roster_info.get("maxTotalPlayers"))

    # Salaries: caps from Fantrax, contracts from team salary sheets.
    ctx.salary_caps = {r["id"]: r["salary_cap"] for r in rows(
        conn, "SELECT id, salary_cap FROM teams WHERE league_id=? AND salary_cap IS NOT NULL", (league_id,))}
    for c in rows(conn, "SELECT * FROM contracts WHERE league_id=?", (league_id,)):
        ctx.contract_teams.add(c["team_id"])
        salaries = json.loads(c["salaries"] or "[]")
        if c["section"] == "BUYOUT":
            ctx.buyouts.setdefault(c["team_id"], []).append(salaries)
            continue
        p = players.get(c["player_id"]) if c["player_id"] else None
        # A player listed on two sheets (a trade one sheet hasn't caught up with): trust the
        # sheet of the team that rosters him.
        if p is not None and (p.salary is None or p.team_id == c["team_id"]):
            p.salary, p.contract_years, p.contract_salaries = c["salary"], c["years_remaining"], salaries
            p.contract_source = "sheet"
    # Contracts kept in the app (edits, or every contract of an app-owned team) win over the sheets,
    # for the seasons they still cover.
    ctx.app_owned = set(app_owned_teams(conn, settings))
    ctx.contract_teams |= ctx.app_owned
    for o in rows(conn, "SELECT * FROM contract_overrides WHERE league_id=?", (league_id,)):
        p = players.get(o["player_id"])
        salaries = current_salaries(o, league["season_year"])
        if p is None or not salaries:
            continue
        p.salary, p.contract_years, p.contract_salaries = salaries[0], len(salaries), salaries
        p.contract_type = o["contract_type"]
        p.contract_source = "app" if p.team_id in ctx.app_owned else "override"
        if p.team_id:
            ctx.contract_teams.add(p.team_id)

    # Rest-of-season schedule: from today (or the league start) to the league's end date.
    start = max(date.today().isoformat(), league["start_date"] or "")
    end = league["end_date"] or ""
    by_day: Dict[str, set] = defaultdict(set)
    for g in rows(conn, "SELECT game_date, away, home FROM nhl_games WHERE game_type=2 AND game_date BETWEEN ? AND ?",
                  (start, end)):
        by_day[g["game_date"]].update((g["away"], g["home"]))
    if by_day:
        ctx.schedule = [frozenset(by_day[d]) for d in sorted(by_day)]
        ctx.horizon_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1

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


def _fill_slots(ctx: Context, ordered: Iterable[Player]) -> List[Player]:
    """Greedy lineup from players sorted best-first: specific positions first, then the F flex."""
    open_slots = dict(ctx.slots)
    lineup = []
    for p in ordered:
        specific = [e for e in p.eligible if e != "F" and open_slots.get(e, 0) > 0]
        flex = ["F"] if "F" in p.eligible and open_slots.get("F", 0) > 0 else []
        for slot in specific + flex:
            open_slots[slot] -= 1
            lineup.append(p)
            break
    return lineup


def _lineup_pool(ctx: Context, team_id: str, lineup: str) -> List[Player]:
    """Players a team can start, best first: everyone but minors/IR ('best') or today's ACTIVE ('active')."""
    if lineup == "active":
        pool = ctx.roster(team_id, ["ACTIVE"])
    else:
        pool = [p for p in ctx.roster(team_id) if p.roster_status not in NON_LINEUP_STATUSES]
    weights = {"S": ctx.league_weights("SKATER"), "G": ctx.league_weights("GOALIE")}
    return sorted((p for p in pool if p.impact), key=lambda p: player_value(p, weights), reverse=True)


def best_lineup(ctx: Context, team_id: str) -> List[Player]:
    """Best lineup if every player had a game that day (ignores the schedule)."""
    return _fill_slots(ctx, _lineup_pool(ctx, team_id, "best"))


def lineup_games(ctx: Context, team_id: str, lineup: str = "best") -> Dict[str, float]:
    """Projected games per week each player spends in the lineup.

    Lineups are set daily, so each remaining game day is filled with the best players whose NHL
    team plays that night: depth players (a third goalie, a bench skater) start on the nights
    the regulars are idle. Without a synced schedule, the best lineup plays GAMES_PER_WEEK.
    """
    pool = _lineup_pool(ctx, team_id, lineup)
    games: Dict[str, float] = defaultdict(float)
    if not ctx.schedule:
        for p in _fill_slots(ctx, pool):
            games[p.fantrax_id] = GAMES_PER_WEEK
        return games
    per_day = 7 / ctx.horizon_days
    for playing in ctx.schedule:
        for p in _fill_slots(ctx, (p for p in pool if p.nhl_team in playing)):
            games[p.fantrax_id] += per_day
    return games


def _team_totals(ctx: Context, games: Dict[str, float]) -> Dict[str, Optional[float]]:
    """Weekly category totals from lineup games per week (goalie rates are per team game, so a
    goalie in the lineup is credited in proportion to his start share)."""
    totals: Dict[str, Optional[float]] = {}
    played = [(ctx.players[fid], n) for fid, n in games.items()]
    skaters = [(p, n) for p, n in played if p.group != "G"]
    goalies = [(p, n) for p, n in played if p.group == "G"]
    for c in ctx.cats("SKATER"):
        col = SKATER_STATS[c.short_name]
        totals[c.short_name] = sum(p.rates[col] * n for p, n in skaters)
    g = lambda col: sum(p.rates[col] * n for p, n in goalies)  # noqa: E731
    for c in ctx.cats("GOALIE"):
        if c.short_name == "GAA":
            totals["GAA"] = g("goals_against") / g("toi_seconds") * 3600 if g("toi_seconds") else None
        elif c.short_name == "SV%":
            totals["SV%"] = g("saves") / g("shots_against") if g("shots_against") else None
        else:
            totals[c.short_name] = g(GOALIE_COUNTING[c.short_name])
    return totals


def _rank_and_z(ctx: Context, values: Dict[str, Dict[str, Optional[float]]],
                baseline: Optional[Dict[str, Dict[str, Optional[float]]]] = None) -> Dict[str, Dict[str, Dict]]:
    """values: team_id -> category -> value. Returns team -> category -> {value, rank, z} (z: higher = better).

    z is measured against `baseline`'s league mean/spread (default: `values` itself), so a simulated
    trade can be scored on the pre-trade scale.
    """
    baseline = baseline or values
    out: Dict[str, Dict[str, Dict]] = {t: {} for t in values}
    for c in ctx.cats("SKATER") + ctx.cats("GOALIE"):
        present = {t: v[c.short_name] for t, v in values.items() if v.get(c.short_name) is not None}
        sign = -1.0 if c.lower_is_better else 1.0
        m, s = _mean_std([sign * v[c.short_name] for v in baseline.values() if v.get(c.short_name) is not None])
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
    values, games = {}, {}
    for team_id in ctx.teams:
        games[team_id] = lineup_games(ctx, team_id, lineup)
        values[team_id] = _team_totals(ctx, games[team_id])
    ranked = _rank_and_z(ctx, values)
    return {t: {"team_id": t, "team_name": ctx.teams[t], "categories": ranked[t], "games": games[t]}
            for t in ctx.teams}


def capped_count(ctx: Context, team_id: str) -> int:
    return sum(1 for p in ctx.roster(team_id) if p.roster_status in CAPPED_STATUSES)


def simulate_moves(ctx: Context, projected: Dict[str, Dict], moves: Dict[str, Optional[str]],
                   lineup: str = "best") -> Dict[str, Dict]:
    """Every team's projected profile after moving players ({player_id: new_team_id or None}).

    Affected teams re-run their daily lineups, so a player only counts for the nights he'd
    actually start. A free agent picked up joins the bench, so he counts against the roster
    limit. A team pushed over the roster limit drops its lowest-value players (never the ones
    it just received); those show up in the team's `dropped` list. z-scores stay on the
    pre-trade league scale. Rosters are restored afterwards.
    """
    previous = {fid: ctx.players[fid].team_id for fid in moves}
    statuses = {fid: ctx.players[fid].roster_status for fid, team in moves.items()
                if team is not None and previous[fid] is None}
    affected = {t for t in set(previous.values()) | set(moves.values()) if t}
    before = {t: {c: v["value"] for c, v in p["categories"].items()} for t, p in projected.items()}
    values = dict(before)
    games = {t: p.get("games", {}) for t, p in projected.items()}
    dropped: Dict[str, List[str]] = {}
    weights = {"S": ctx.league_weights("SKATER"), "G": ctx.league_weights("GOALIE")}
    try:
        for fid, team in moves.items():
            ctx.players[fid].team_id = team
        for fid in statuses:
            ctx.players[fid].roster_status = "RESERVE"
        for t in affected:
            over = capped_count(ctx, t) - ctx.roster_cap if ctx.roster_cap else 0
            if over > 0:
                incoming = {fid for fid, team in moves.items() if team == t}
                cuttable = sorted((p for p in ctx.roster(t)
                                   if p.roster_status in CAPPED_STATUSES and p.fantrax_id not in incoming),
                                  key=lambda p: player_value(p, weights) if p.z else -1e9)
                dropped[t] = [p.fantrax_id for p in cuttable[:over]]
                for fid in dropped[t]:
                    previous.setdefault(fid, t)
                    ctx.players[fid].team_id = None
            games[t] = lineup_games(ctx, t, lineup)
            values[t] = _team_totals(ctx, games[t])
    finally:
        for fid, team in previous.items():
            ctx.players[fid].team_id = team
        for fid, status in statuses.items():
            ctx.players[fid].roster_status = status
    ranked = _rank_and_z(ctx, values, baseline=before)
    return {t: {"team_id": t, "team_name": ctx.teams[t], "categories": ranked[t], "games": games[t],
                "dropped": dropped.get(t, [])} for t in ctx.teams}


def expected_category_wins(ctx: Context, profiles: Dict[str, Dict], team_id: str, spread: float = 1.5,
                           importance: Optional[Dict[str, float]] = None,
                           opponents: Optional[Dict[str, Dict]] = None) -> float:
    """Expected categories won per week against an average opponent.

    For each category and opponent, P(win) = Phi(z gap / spread): the same noise model behind the
    marginal need weights. `importance` scales categories (0 = punted); default is the league weight.
    `opponents` (default: `profiles`) lets a simulated roster face the unchanged league, so the
    result reflects only that team's own change.
    """
    me = profiles[team_id]["categories"]
    total = 0.0
    for c in ctx.cats("SKATER") + ctx.cats("GOALIE"):
        z_me = me[c.short_name]["z"]
        gaps = [z_me - p["categories"][c.short_name]["z"] for t, p in (opponents or profiles).items()
                if t != team_id and z_me is not None and p["categories"][c.short_name]["z"] is not None]
        if not gaps:
            continue
        weight = importance.get(c.short_name, c.weight) if importance else c.weight
        total += weight * sum(0.5 * (1 + math.erf(g / spread / math.sqrt(2))) for g in gaps) / len(gaps)
    return total


def actual_profiles(conn: Connection, ctx: Context) -> Dict[str, Dict]:
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
        "trade_rank": ctx.trade_ranks().get(p.fantrax_id),
        "market_value": round(ctx.market_values().get(p.fantrax_id, 0.0), 2) if p.z else None,
        "salary": p.salary,
        "contract_years": p.contract_years,
        "contract_salaries": p.contract_salaries,
        "contract_type": p.contract_type if p.salary is not None else None,
        "contract_source": p.contract_source,
        "birth_date": p.birth_date,
        "age": age_on(p.birth_date, date.today()) if p.birth_date else None,
        "contract_end": contract_end(ctx, p),
    }
    if p.start_share is not None:
        out["start_share"] = round(p.start_share, 2)
    if weights is not None and p.z:
        out["need_value"] = round(player_value(p, weights), 2)
    return out


def season_label(ctx: Context, offset: int) -> str:
    year = (ctx.league.get("season_year") or 0) + offset
    return "{}/{:02d}".format(year, (year + 1) % 100)


def cap_summary(ctx: Context, team_id: str, players: Optional[List[Player]] = None,
                extra_dead_cap: float = 0.0) -> Optional[Dict]:
    """Salary-cap position under league rules, or None if the team has no salary sheet loaded.

    Active and bench salaries count, plus buyouts; players in the minors or on IR don't.
    `players` overrides the team's current roster (used to price a trade); `extra_dead_cap` adds
    buyout charges the caller incurs. Future seasons count everyone under contract except minors.
    A player whose contract ends with him an RFA is carried into every later season as an RFA
    hold at his final-season salary, kept apart from `committed` and `space` (no contract yet).
    """
    if team_id not in ctx.contract_teams:
        return None
    players = ctx.roster(team_id) if players is None else players
    cap = ctx.salary_caps.get(team_id) or DEFAULT_SALARY_CAP
    buyouts = ctx.buyouts.get(team_id, [])
    counting = [p for p in players if p.roster_status in CAPPED_STATUSES]
    dead = sum(b[0] for b in buyouts if b) + extra_dead_cap
    committed = sum(p.salary for p in counting if p.salary is not None) + dead
    rfas = [p for p in players if p.roster_status != "MINORS" and p.contract_salaries
            and (contract_end(ctx, p) or {}).get("status") == "RFA"]
    future = []
    for i in range(1, 5):
        total = sum(p.contract_salaries[i] for p in players
                    if p.roster_status != "MINORS" and len(p.contract_salaries) > i)
        total += sum(b[i] for b in buyouts if len(b) > i)
        holds = sorted(({"fantrax_id": p.fantrax_id, "name": p.name, "salary": p.contract_salaries[-1],
                         "after_season": season_label(ctx, len(p.contract_salaries) - 1)}
                        for p in rfas if len(p.contract_salaries) <= i), key=lambda x: (-x["salary"], x["name"]))
        future.append({"season": season_label(ctx, i), "committed": total, "space": cap - total,
                       "rfa_holds": sum(x["salary"] for x in holds), "rfa_players": holds})
    return {
        "cap": cap, "committed": committed, "space": cap - committed, "dead_cap": dead,
        "ir_relief": sum(p.salary or 0 for p in players if p.roster_status == "INJURED_RESERVE"),
        "minors_relief": sum(p.salary or 0 for p in players if p.roster_status == "MINORS"),
        "unknown_salary": sorted(p.name for p in counting if p.salary is None),
        # Contract-year bank: seasons left on every contract, this one included, for active and
        # bench players. Minors and IR don't count; in-season UFA pickups are exempt by rule.
        "contract_years": contract_years(players),
        "contract_year_bank": CONTRACT_YEAR_BANK,
        "season": season_label(ctx, 0),
        "future": future,
    }


def contract_years(players: Iterable[Player]) -> int:
    return sum(p.contract_years or 0 for p in players
               if p.roster_status in CAPPED_STATUSES and p.contract_type != "IN_SEASON_UFA")


def age_on(birth_date: str, on: date) -> int:
    born = date.fromisoformat(birth_date)
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def contract_end(ctx: Context, p: Player) -> Optional[Dict[str, Any]]:
    """What the player becomes when his contract runs out: UFA or RFA, per league rules.

    The contract ends after its last season; free agency is decided by age on July 1 that summer.
    Returns None for a player with no contract on file.
    """
    if p.salary is None or not p.contract_years:
        return None
    season_year = ctx.league.get("season_year") or date.today().year
    july1 = date(season_year + p.contract_years, 7, 1)
    out: Dict[str, Any] = {"after_season": season_label(ctx, p.contract_years - 1), "year": july1.year,
                           "status": None, "age_at_expiry": None, "reason": None}
    if p.contract_type == "IN_SEASON_UFA":
        out.update(status="UFA", reason="In-season pickups return to the auction as UFAs")
    elif not p.birth_date:
        out["reason"] = "Birth date unknown"
    else:
        age = age_on(p.birth_date, july1)
        out.update(status="UFA" if age >= UFA_AGE else "RFA", age_at_expiry=age,
                   reason="{} on July 1, {}".format(age, july1.year))
    return out


def release_cost(p: Player) -> float:
    """Cap charge for cutting a player: free at $3 or less, otherwise a buyout of half the salary
    (rounded down) for the rest of the contract. Unknown salaries are treated as free."""
    if p.salary is None or p.salary <= FREE_RELEASE_MAX_SALARY:
        return 0.0
    return float(math.floor(p.salary / 2))


def team_profile(conn: Connection, ctx: Context, team_id: str, **need_kwargs) -> Dict:
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
    games = projected[team_id]["games"]
    return {
        "team_id": team_id, "team_name": ctx.teams[team_id], "roster_period": ctx.roster_period,
        "lineup_mode": lineup_mode,
        "projected_total_z": round(sum(v["z"] or 0 for v in projected[team_id]["categories"].values()), 2),
        "projected_overall_rank": _overall_rank(projected, team_id),
        "expected_category_wins": round(expected_category_wins(ctx, projected, team_id,
                                                               need_kwargs.get("spread", 1.5)), 2),
        "categories": cats,
        "cap": cap_summary(ctx, team_id),
        "strengths": [c["category"] for c in sorted(cats, key=lambda c: c["projected_rank"] or n)[:3]],
        "weaknesses": [c["category"] for c in sorted(cats, key=lambda c: -(c["projected_rank"] or 0))[:3]],
        "roster": [dict(player_summary(p, ctx, w_split), **_games_fields(p, games.get(p.fantrax_id, 0.0)))
                   for p in roster],
        "unsupported_categories": ctx.unsupported_categories,
    }


def team_overview(conn: Connection, ctx: Context, team_id: str) -> Dict:
    """What a GM checks on their team: this week's matchup, season-to-date results by category,
    the roster with this NHL season's stats, and roster spots. Results only, no projections.
    The cap is on the Players & cap tab (GET /players?team_id=...)."""
    actual = actual_profiles(conn, ctx)[team_id]["categories"]
    players = sorted(ctx.roster(team_id), key=lambda p: (p.group, -(p.salary or 0), p.name))
    stats = season_stats(conn, ctx, players)
    return {
        "team_id": team_id, "team_name": ctx.teams[team_id], "roster_period": ctx.roster_period,
        "week": week_matchups(conn, ctx, team_id),
        "categories": [{"category": c.short_name, "name": c.name, "group": c.grp,
                        "lower_is_better": c.lower_is_better,
                        "record": actual[c.short_name]["record"], "rank": actual[c.short_name]["rank"]}
                       for c in ctx.cats("SKATER") + ctx.cats("GOALIE")],
        "roster": [dict(player_summary(p, ctx), season=stats.get(p.fantrax_id)) for p in players],
        "roster_spots": {"used": capped_count(ctx, team_id), "limit": ctx.roster_cap},
    }


def season_stats(conn: Connection, ctx: Context, players: Iterable[Player]) -> Dict[str, Dict[str, float]]:
    """This NHL season's totals in the league's categories, by Fantrax id. Players without a game
    this season are left out."""
    by_nhl = {p.nhl_id: p for p in players if p.nhl_id}
    if not by_nhl:
        return {}
    marks = ", ".join("?" * len(by_nhl))
    out: Dict[str, Dict[str, float]] = {}
    for r in rows(conn, "SELECT * FROM skater_stats WHERE season_id=? AND gp > 0 AND nhl_id IN ({})".format(marks),
                  (ctx.cur_season, *by_nhl)):
        p = by_nhl[r["nhl_id"]]
        if p.group != "G":
            out[p.fantrax_id] = {c.short_name: r[SKATER_STATS[c.short_name]] or 0 for c in ctx.cats("SKATER")}
    for r in rows(conn, "SELECT * FROM goalie_stats WHERE season_id=? AND gp > 0 AND nhl_id IN ({})".format(marks),
                  (ctx.cur_season, *by_nhl)):
        p = by_nhl[r["nhl_id"]]
        if p.group != "G":
            continue
        line: Dict[str, Optional[float]] = {}
        for c in ctx.cats("GOALIE"):
            if c.short_name == "GAA":
                line["GAA"] = _r(r["goals_against"] / r["toi_seconds"] * 3600, 2) if r["toi_seconds"] else None
            elif c.short_name == "SV%":
                line["SV%"] = _r(r["saves"] / r["shots_against"], 3) if r["shots_against"] else None
            else:
                line[c.short_name] = r[GOALIE_COUNTING[c.short_name]] or 0
        out[p.fantrax_id] = line
    return out


def week_matchups(conn: Connection, ctx: Context, team_id: str) -> Optional[Dict]:
    """The team's head-to-head matchups in the current scoring period, with each category's score
    as of the last matchups sync. With 11 teams a week can bring two opponents, or a bye (none)."""
    period = current_period(conn, ctx.league_id, "scoring")
    if period is None:
        return None
    dates = row(conn, "SELECT start_date, end_date FROM periods WHERE league_id=? AND kind='scoring' AND number=?",
                (ctx.league_id, period)) or {}
    opponents = [g["home_team_id"] if g["away_team_id"] == team_id else g["away_team_id"] for g in rows(
        conn, "SELECT away_team_id, home_team_id FROM matchups WHERE league_id=? AND period=? "
              "AND (away_team_id=? OR home_team_id=?) ORDER BY seq", (ctx.league_id, period, team_id, team_id))]
    scores = rows(conn, "SELECT team_id, opponent_id, category, value, outcome FROM matchup_category_scores "
                        "WHERE league_id=? AND period=? AND (team_id=? OR opponent_id=?)",
                  (ctx.league_id, period, team_id, team_id))
    results = {r["opponent_id"]: r for r in rows(
        conn, "SELECT opponent_id, category_wins, category_losses, category_ties FROM matchup_results "
              "WHERE league_id=? AND period=? AND team_id=?", (ctx.league_id, period, team_id))}
    synced = row(conn, "SELECT MAX(finished_at) AS at FROM sync_log WHERE resource='matchups' AND status='ok'")
    matchups = []
    for opp in (o for o in opponents if o):
        mine = {s["category"]: s for s in scores if s["team_id"] == team_id and s["opponent_id"] == opp}
        theirs = {s["category"]: s["value"] for s in scores if s["team_id"] == opp and s["opponent_id"] == team_id}
        res = results.get(opp)
        matchups.append({
            "team_id": opp, "team_name": ctx.teams.get(opp),
            # None until Fantrax reports a score for the week.
            "record": [int(res[k] or 0) for k in ("category_wins", "category_losses", "category_ties")] if res else None,
            "categories": {c: {"value": s["value"], "opponent_value": theirs.get(c), "outcome": s["outcome"]}
                           for c, s in mine.items()},
        })
    return {"period": period, "start_date": dates.get("start_date"), "end_date": dates.get("end_date"),
            "bye": None in opponents, "matchups": matchups, "scores_as_of": (synced or {}).get("at")}


def _games_fields(p: Player, lineup_games_per_week: float) -> Dict[str, float]:
    out = {"lineup_games_per_week": round(lineup_games_per_week, 2)}
    if p.start_share is not None:
        out["expected_starts_per_week"] = round(lineup_games_per_week * p.start_share, 2)
    return out


def league_overview(conn: Connection, ctx: Context, lineup: str = "best") -> List[Dict]:
    projected = projected_profiles(ctx, lineup)
    actual = actual_profiles(conn, ctx)
    out = []
    for t in ctx.teams:
        out.append({
            "team_id": t, "team_name": ctx.teams[t],
            "projected_total_z": round(sum(v["z"] or 0 for v in projected[t]["categories"].values()), 2),
            "projected_overall_rank": _overall_rank(projected, t),
            "expected_category_wins": round(expected_category_wins(ctx, projected, t), 2),
            "projected_ranks": {c: v["rank"] for c, v in projected[t]["categories"].items()},
            "projected": {c: {"rank": v["rank"], "z": _r(v["z"], 2),
                              "value": _r(v["value"], 3 if c in GOALIE_RATIOS else 2)}
                          for c, v in projected[t]["categories"].items()},
            "actual_records": {c: v["record"] for c, v in actual[t]["categories"].items()},
        })
    return sorted(out, key=lambda r: r["projected_overall_rank"])


def _overall_rank(projected: Dict[str, Dict], team_id: str) -> int:
    totals = {t: sum(v["z"] or 0 for v in p["categories"].values()) for t, p in projected.items()}
    return sorted(totals, key=totals.get, reverse=True).index(team_id) + 1


def targets(conn: Connection, ctx: Context, team_id: str, source: str = "fa", position: Optional[str] = None,
            limit: int = 25, min_games: int = 5, include_minors: bool = False, from_team: Optional[str] = None,
            offer: Union[None, str, Sequence[str]] = None, tolerance: float = 0.25,
            drop: Union[None, str, Sequence[str]] = None, **need_kwargs) -> Dict:
    """Players to go after, ranked by value to `team_id` given its category needs.

    source="fa": unrostered players. Each pickup is simulated (your daily lineups re-run with him
                 on the bench) and scored as the change in your expected category wins per week
                 (`your_gain`). With a full roster someone has to go: `drop` names the player(s)
                 you'd release; without it, your lowest-value players (overall and at the pickup's
                 position) are each tried and the best drop is reported in `drops`.
    source="trade": players on other teams; `from_team` narrows that to one team's roster.
    offer: one or more players on your roster to trade away. Only players of comparable market
           value are returned: trade rank within `tolerance` (see comparable_band) of the offered
           player, or of the package's equivalent rank (summed market value). Each swap is
           simulated (both teams re-run daily lineups, over-limit rosters drop their worst) and
           scored as the change in expected category wins per week, for you (`your_gain`) and
           the other team (`their_gain`).
    """
    offer_ids = [offer] if isinstance(offer, str) else list(offer or [])
    drop_ids = [drop] if isinstance(drop, str) else list(drop or [])
    if from_team is not None or offer_ids:
        if source != "trade":
            raise ValueError("from_team and offer only apply to source=trade")
        if from_team == team_id:
            raise ValueError("from_team is the team you're finding targets for")
    if drop_ids and source != "fa":
        raise ValueError("drop only applies to source=fa")
    lineup_mode = need_kwargs.pop("lineup", "best")
    projected = projected_profiles(ctx, lineup_mode)
    weights = _split(need_weights(ctx, team_id, projected, **need_kwargs), ctx)

    ranks, market = ctx.trade_ranks(), ctx.market_values()
    offered: List[Player] = []
    for fid in offer_ids:
        p = ctx.players.get(fid)
        if p is None or p.team_id != team_id:
            raise ValueError("offer must be players on your roster")
        if not p.z:
            raise ValueError("{} has no NHL stats, so there's no value to compare against".format(p.name))
        offered.append(p)
    dropping: List[Player] = []
    for fid in drop_ids:
        p = ctx.players.get(fid)
        if p is None or p.team_id != team_id:
            raise ValueError("drop must be players on your roster")
        dropping.append(p)
    band, anchor, package_value = (0, 0), 0, 0.0
    if offered:
        package_value = sum(market[p.fantrax_id] for p in offered)
        anchor = ranks[offered[0].fantrax_id] if len(offered) == 1 else ctx.equivalent_rank(package_value)
        band = comparable_band(anchor, tolerance)

    def eligible(p: Player) -> bool:
        if not p.z or p.gp_cur + p.gp_prior < min_games or not _matches_position(p, position):
            return False
        if source == "fa":
            return p.team_id is None
        if source == "trade":
            return (p.team_id not in (None, team_id) and (from_team is None or p.team_id == from_team)
                    and (include_minors or p.roster_status != "MINORS")
                    and (not offered or band[0] <= ranks.get(p.fantrax_id, 0) <= band[1]))
        raise ValueError("source must be 'fa' or 'trade'")

    candidates = [p for p in ctx.players.values() if eligible(p)]
    rank_key = lambda p: player_value(p, weights)  # noqa: E731
    # Your punts / manual weights decide what counts as a gain for you; the other team is scored
    # on plain category wins.
    spread = need_kwargs.get("spread", 1.5)
    importance = {c.short_name: c.weight for c in ctx.categories}
    importance.update({c: 0.0 for c in need_kwargs.get("punt") or ()})
    importance.update(need_kwargs.get("overrides") or {})

    swaps: Dict[str, Tuple[float, float]] = {}
    if offered:
        base_me = expected_category_wins(ctx, projected, team_id, spread, importance)
        base = {t: expected_category_wins(ctx, projected, t, spread) for t in ctx.teams}
        for p in candidates:
            moves: Dict[str, Optional[str]] = {o.fantrax_id: p.team_id for o in offered}
            moves[p.fantrax_id] = team_id
            after = simulate_moves(ctx, projected, moves, lineup_mode)
            swaps[p.fantrax_id] = (
                expected_category_wins(ctx, after, team_id, spread, importance, opponents=projected) - base_me,
                expected_category_wins(ctx, after, p.team_id, spread, opponents=projected) - base[p.team_id],
            )
        rank_key = lambda p: swaps[p.fantrax_id][0]  # noqa: E731

    pickups: Dict[str, Tuple[float, List[str]]] = {}
    open_spots = ctx.roster_cap - capped_count(ctx, team_id) if ctx.roster_cap else 1
    if source == "fa":
        # Simulating the whole pool is wasted work: shortlist the best free agents per position
        # group by need value, then rank the shortlist by simulated gain.
        shortlist: List[Player] = []
        for grp in ("F", "D", "G"):
            shortlist += sorted((p for p in candidates if p.group == grp), key=rank_key, reverse=True)[:max(limit, 20)]
        candidates = shortlist
        # Cheapest players to lose first; ones with no NHL stats add nothing this season.
        droppable = sorted(ctx.roster(team_id, CAPPED_STATUSES),
                           key=lambda p: player_value(p, weights) if p.z else -1e9)

        def drop_options(p: Player) -> List[List[str]]:
            if dropping:
                return [[d.fantrax_id for d in dropping]]
            if open_spots > 0:
                return [[]]
            tries = droppable[:3] + [d for d in droppable if d.group == p.group][:2]
            return [[fid] for fid in dict.fromkeys(d.fantrax_id for d in tries)] or [[]]

        base_me = expected_category_wins(ctx, projected, team_id, spread, importance)
        for p in candidates:
            best: Optional[Tuple[float, List[str]]] = None
            for out in drop_options(p):
                moves = {fid: None for fid in out}
                moves[p.fantrax_id] = team_id
                after = simulate_moves(ctx, projected, moves, lineup_mode)
                gain = expected_category_wins(ctx, after, team_id, spread, importance, opponents=projected) - base_me
                if best is None or gain > best[0]:
                    best = (gain, out + after[team_id]["dropped"])
            pickups[p.fantrax_id] = best
        rank_key = lambda p: pickups[p.fantrax_id][0]  # noqa: E731

    pool = sorted(candidates, key=rank_key, reverse=True)[:limit]
    results = []
    for p in pool:
        item = player_summary(p, ctx, weights)
        if source == "fa":
            gain, out = pickups[p.fantrax_id]
            item["your_gain"] = round(gain, 3)      # expected category wins per week
            item["drops"] = [_move_brief(ctx, f, weights) for f in out]
            item["market_value_net"] = round(market.get(p.fantrax_id, 0.0) - sum(market.get(f, 0.0) for f in out), 2)
        if offered:
            yours, theirs = swaps[p.fantrax_id]
            item["your_gain"] = round(yours, 3)      # expected category wins per week
            item["their_gain"] = round(theirs, 3)
            item["win_win"] = yours > 0 and theirs > 0
        results.append(item)
    return {
        "team_id": team_id, "team_name": ctx.teams[team_id], "source": source,
        "from_team": {"team_id": from_team, "team_name": ctx.teams[from_team]} if from_team else None,
        "offer": None if not offered else {
            "players": [{"fantrax_id": o.fantrax_id, "name": o.name, "trade_rank": ranks[o.fantrax_id],
                         "market_value": round(market[o.fantrax_id], 2)} for o in offered],
            "name": " + ".join(o.name for o in offered),
            # For a package: the rank of a single player worth the same summed market value.
            "trade_rank": anchor,
            "market_value": round(package_value, 2),
            "need_value": round(sum(player_value(o, weights) for o in offered), 2),
            "comparable_trade_ranks": list(band), "tolerance": tolerance,
        },
        "drop": None if not dropping else {
            "players": [_move_brief(ctx, d.fantrax_id, weights) for d in dropping],
            "name": " + ".join(d.name for d in dropping),
        },
        "roster": {"count": capped_count(ctx, team_id), "cap": ctx.roster_cap},
        "need_weights": {c: round(w, 2) for c, w in {**weights["S"], **weights["G"]}.items()},
        "targets": results,
    }


def _move_brief(ctx: Context, fid: str, weights: Optional[Dict[str, Dict[str, float]]] = None) -> Dict:
    """A player changing teams: who he is, what he's worth, and what he's paid."""
    p = ctx.players[fid]
    out = {"fantrax_id": fid, "name": p.name, "position": ",".join(e for e in p.eligible if e != "F"),
           "nhl_team": p.nhl_team, "roster_status": p.roster_status,
           "trade_rank": ctx.trade_ranks().get(fid), "market_value": round(ctx.market_values().get(fid, 0.0), 2),
           "salary": p.salary, "contract_years": p.contract_years, "release_cost": release_cost(p),
           "age": age_on(p.birth_date, date.today()) if p.birth_date else None}
    if weights is not None:
        out["need_value"] = round(player_value(p, weights), 2) if p.z else None
    return out


def evaluate_trade(ctx: Context, team_id: str, give: Sequence[str], other_team_id: Optional[str] = None,
                   get: Sequence[str] = (), lineup: str = "best", spread: float = 1.5) -> Dict:
    """Cost/benefit of a trade for both sides.

    With no other team it's a waiver move: `give` players are released (buyouts for $4+
    contracts) and `get` players are free agents picked up onto the bench.

    The trade is simulated with daily lineups over the rest of the season; each side's effect is
    the change in expected category wins per week against the unchanged rest of the league, with
    per-category before/after values and ranks, games/starts volume, roster-limit drops, and the
    summed market value moving each way.
    """
    if team_id not in ctx.teams:
        raise ValueError("Unknown team {}".format(team_id))
    if other_team_id is not None and (other_team_id not in ctx.teams or other_team_id == team_id):
        raise ValueError("other_team_id must be a different team in the league")
    if not give and not get:
        raise ValueError("Select at least one player")
    for fid in give:
        if fid not in ctx.players or ctx.players[fid].team_id != team_id:
            raise ValueError("{} is not on {}".format(fid, ctx.teams[team_id]))
    for fid in get:
        if fid not in ctx.players:
            raise ValueError("Unknown player {}".format(fid))
        if other_team_id is None and ctx.players[fid].team_id is not None:
            raise ValueError("{} is on {}: pick that team as the trade partner".format(
                ctx.players[fid].name, ctx.teams[ctx.players[fid].team_id]))
        if other_team_id is not None and ctx.players[fid].team_id != other_team_id:
            raise ValueError("{} is not on {}".format(fid, ctx.teams[other_team_id]))

    projected = projected_profiles(ctx, lineup)
    moves: Dict[str, Optional[str]] = {fid: other_team_id for fid in give}
    moves.update({fid: team_id for fid in get})
    counts = {t: capped_count(ctx, t) for t in (team_id, other_team_id) if t}
    after = simulate_moves(ctx, projected, moves, lineup)
    market = ctx.market_values()
    brief = lambda fid: _move_brief(ctx, fid)  # noqa: E731
    # Free agents picked up join the bench, so they count against the roster limit and the cap.
    joins_capped = lambda f: ctx.players[f].team_id is None or ctx.players[f].roster_status in CAPPED_STATUSES  # noqa: E731

    def cap_effect(t: str, out_ids: Sequence[str], in_ids: Sequence[str], dropped: Sequence[str]) -> Optional[Dict]:
        before = cap_summary(ctx, t)
        if before is None:
            return None
        leaving = set(out_ids) | set(dropped)
        roster_after = [p for p in ctx.roster(t) if p.fantrax_id not in leaving] + [ctx.players[f] for f in in_ids]
        buyouts = sum(release_cost(ctx.players[f]) for f in dropped)
        # With no trade partner the players are released: $4+ contracts cost a buyout.
        released = list(out_ids) if other_team_id is None else []
        release_buyouts = sum(release_cost(ctx.players[f]) for f in released)
        pickups = {f: ctx.players[f].roster_status for f in in_ids if ctx.players[f].team_id is None}
        try:
            for f in pickups:
                ctx.players[f].roster_status = "RESERVE"
            after = cap_summary(ctx, t, roster_after, extra_dead_cap=buyouts + release_buyouts)
        finally:
            for f, status in pickups.items():
                ctx.players[f].roster_status = status
        incoming = [ctx.players[f] for f in in_ids if joins_capped(f)]
        outgoing = [ctx.players[f] for f in out_ids if ctx.players[f].roster_status in CAPPED_STATUSES]
        return {
            "cap": before["cap"], "committed_before": before["committed"], "committed_after": after["committed"],
            "space_before": before["space"], "space_after": after["space"],
            "salary_out": sum(p.salary or 0 for p in outgoing), "salary_in": sum(p.salary or 0 for p in incoming),
            "drop_buyouts": buyouts,
            "release_buyouts": release_buyouts,
            # Incoming players with no known salary (their team's sheet isn't loaded, or a free
            # agent not signed yet): space_after is overstated by whatever they earn.
            "unknown_incoming": sorted(p.name for p in incoming if p.salary is None),
            "over_cap": after["space"] < 0,
            "contract_years_before": before["contract_years"], "contract_years_after": after["contract_years"],
            "contract_year_bank": CONTRACT_YEAR_BANK,
        }

    def volume(games: Dict[str, float]) -> Dict[str, float]:
        return {"skater_games": round(sum(n for f, n in games.items() if ctx.players[f].group != "G"), 2),
                "goalie_starts": round(sum(n * (ctx.players[f].start_share or 0) for f, n in games.items()
                                           if ctx.players[f].group == "G"), 2)}

    sides = []
    for t, out_ids, in_ids in ((team_id, give, get), (other_team_id, get, give)):
        if t is None:
            continue
        before_ecw = expected_category_wins(ctx, projected, t, spread)
        after_ecw = expected_category_wins(ctx, after, t, spread, opponents=projected)
        cats = []
        for c in ctx.cats("SKATER") + ctx.cats("GOALIE"):
            b, a = projected[t]["categories"][c.short_name], after[t]["categories"][c.short_name]
            nd = 3 if c.short_name in GOALIE_RATIOS else 2
            cats.append({
                "category": c.short_name, "name": c.name, "group": c.grp, "lower_is_better": c.lower_is_better,
                "before": {"value": _r(b["value"], nd), "rank": b["rank"], "z": _r(b["z"], 2)},
                "after": {"value": _r(a["value"], nd), "rank": a["rank"], "z": _r(a["z"], 2)},
                "delta_value": _r(a["value"] - b["value"], nd) if a["value"] is not None and b["value"] is not None
                else None,
                "delta_z": _r(a["z"] - b["z"], 2) if a["z"] is not None and b["z"] is not None else None,
            })
        gives_value = sum(market.get(f, 0.0) for f in out_ids)
        gets_value = sum(market.get(f, 0.0) for f in in_ids)
        n_out = sum(1 for f in out_ids if ctx.players[f].roster_status in CAPPED_STATUSES)
        n_in = sum(1 for f in in_ids if joins_capped(f))
        sides.append({
            "team_id": t, "team_name": ctx.teams[t], "role": "you" if t == team_id else "partner",
            "gives": [brief(f) for f in out_ids], "gets": [brief(f) for f in in_ids],
            "expected_category_wins": {"before": round(before_ecw, 3), "after": round(after_ecw, 3),
                                       "delta": round(after_ecw - before_ecw, 3)},
            "categories": cats,
            "volume": {"before": volume(projected[t]["games"]), "after": volume(after[t]["games"])},
            "roster": {"before": counts[t], "after": counts[t] - n_out + n_in - len(after[t]["dropped"]),
                       "cap": ctx.roster_cap},
            "auto_dropped": [brief(f) for f in after[t]["dropped"]],
            "cap": cap_effect(t, out_ids, in_ids, after[t]["dropped"]),
            "market_value": {"gives": round(gives_value, 2), "gets": round(gets_value, 2),
                             "net": round(gets_value - gives_value, 2)},
        })
    give_value = sum(market.get(f, 0.0) for f in give)
    get_value = sum(market.get(f, 0.0) for f in get)
    return {
        "lineup_mode": lineup, "horizon_days": ctx.horizon_days, "sides": sides,
        "market": {"you_give": round(give_value, 2), "you_get": round(get_value, 2),
                   "you_give_equiv_rank": ctx.equivalent_rank(give_value) if give else None,
                   "you_get_equiv_rank": ctx.equivalent_rank(get_value) if get else None},
    }


def trade_partners(conn: Connection, ctx: Context, team_id: str, per_team: int = 3, **need_kwargs) -> Dict:
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
