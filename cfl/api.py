"""Local HTTP API over the league store. Run: python -m cfl serve"""

import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis as A
from .config import Settings, get_settings
from .db import connect, row, rows
from .sync import RESOURCES, current_period, sync_all

app = FastAPI(
    title="CFL Manager",
    description="Local store + analysis API for a Fantrax fantasy hockey league.",
    version="0.1.0",
)

# "+" in an unencoded query string arrives as a space, so "+/-" shows up as "/-" after stripping.
WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(str(WEB_DIR / "index.html"))


CATEGORY_ALIASES = {"pm": "+/-", "plusminus": "+/-", "/-": "+/-", "svpct": "SV%"}


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect(get_settings().db_path)
    try:
        yield conn
    finally:
        conn.close()


@app.exception_handler(A.NotSynced)
def _not_synced(_, exc: A.NotSynced):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def _ctx(conn: sqlite3.Connection, settings: Settings) -> A.Context:
    return A.load_context(conn, settings)


def _team(ctx: A.Context, team_id: str, settings: Settings) -> str:
    if team_id == "me":
        if not settings.my_team_id:
            raise HTTPException(400, detail={
                "error": "Set FANTRAX_MY_TEAM_ID in .env to use 'me'",
                "teams": [{"id": t, "name": n} for t, n in ctx.teams.items()],
            })
        team_id = settings.my_team_id
    if team_id not in ctx.teams:
        raise HTTPException(404, detail="Unknown team id {}".format(team_id))
    return team_id


def _parse_cats(ctx: A.Context, value: Optional[str]) -> List[str]:
    if not value:
        return []
    known = {c.short_name.lower(): c.short_name for c in ctx.categories}
    out = []
    for raw in value.split(","):
        key = raw.strip().lower()
        name = known.get(key) or CATEGORY_ALIASES.get(key)
        if not name:
            raise HTTPException(400, detail="Unknown category '{}' (valid: {}; use PM for +/-)".format(
                raw, ", ".join(c.short_name for c in ctx.categories)))
        out.append(name)
    return out


def _parse_weights(ctx: A.Context, value: Optional[str]) -> Optional[Dict[str, float]]:
    if not value:
        return None
    out = {}
    for part in value.split(","):
        if ":" not in part:
            raise HTTPException(400, detail="weights format is CAT:NUMBER,CAT:NUMBER (e.g. Hit:0,G:1.5)")
        cat, num = part.rsplit(":", 1)
        out[_parse_cats(ctx, cat)[0]] = float(num)
    return out


def _need_kwargs(ctx: A.Context, strategy: str, spread: float, punt: Optional[str], weights: Optional[str],
                 lineup: str) -> Dict:
    if strategy not in ("marginal", "weakness", "even"):
        raise HTTPException(400, detail="strategy must be marginal, weakness or even")
    if lineup not in ("best", "active"):
        raise HTTPException(400, detail="lineup must be best or active")
    return {"strategy": strategy, "spread": spread, "punt": _parse_cats(ctx, punt),
            "overrides": _parse_weights(ctx, weights), "lineup": lineup}


# Shared query parameter docs.
STRATEGY = Query("marginal", description="marginal (swing categories), weakness (worst categories), or even")
SPREAD = Query(1.5, gt=0, description="marginal strategy only: wider = flatter weights")
PUNT = Query(None, description="Comma-separated categories to ignore, e.g. Hit,PM")
WEIGHTS = Query(None, description="Manual category weights, e.g. Hit:0,G:1.5 (overrides strategy)")
LINEUP = Query("best", description="best = daily best lineups from non-minors/IR players over the rest-of-season "
                                    "NHL schedule; active = only players currently ACTIVE in Fantrax")


# --------------------------------------------------------------------------- sync & raw data


@app.get("/health")
def health(conn: sqlite3.Connection = Depends(get_conn)):
    s = get_settings()
    last = row(conn, "SELECT resource, finished_at, status FROM sync_log ORDER BY id DESC LIMIT 1")
    return {"ok": True, "league_id": s.league_id, "my_team_id": s.my_team_id, "db_path": str(s.db_path),
            "last_sync": last}


@app.post("/sync")
def sync(resources: Optional[List[str]] = Query(None, description="Subset of: " + ", ".join(RESOURCES)),
         conn: sqlite3.Connection = Depends(get_conn)):
    try:
        results = sync_all(conn, get_settings(), resources)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return {"ok": all(r["status"] == "ok" for r in results), "results": results}


@app.get("/sync/log")
def sync_log(limit: int = 20, conn: sqlite3.Connection = Depends(get_conn)):
    out = rows(conn, "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,))
    for r in out:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return out


@app.get("/league")
def league(conn: sqlite3.Connection = Depends(get_conn)):
    s = get_settings()
    lg = row(conn, "SELECT * FROM league WHERE id=?", (s.league_id,))
    if not lg:
        raise A.NotSynced("League not synced yet: POST /sync")
    for k in ("roster_info", "playoffs", "draft_settings"):
        lg[k] = json.loads(lg[k]) if lg[k] else None
    lg["categories"] = rows(conn, "SELECT short_name, name, code, grp, weight, lower_is_better "
                                  "FROM scoring_categories WHERE league_id=? ORDER BY sort_order", (s.league_id,))
    lg["current_scoring_period"] = current_period(conn, s.league_id, "scoring")
    lg["current_roster_period"] = current_period(conn, s.league_id, "roster")
    lg["scoring_periods"] = rows(conn, "SELECT number, start_date, end_date FROM periods "
                                       "WHERE league_id=? AND kind='scoring' ORDER BY number", (s.league_id,))
    return lg


@app.get("/categories")
def categories(conn: sqlite3.Connection = Depends(get_conn)):
    return rows(conn, "SELECT short_name, name, code, grp, weight, lower_is_better FROM scoring_categories "
                      "WHERE league_id=? ORDER BY sort_order", (get_settings().league_id,))


@app.get("/teams")
def teams(conn: sqlite3.Connection = Depends(get_conn)):
    s = get_settings()
    return rows(conn, """
        SELECT t.id, t.name, t.short_name, s.rank, s.points AS record, s.games_back, s.win_pct,
               COALESCE(t.id = ?, 0) AS is_me
        FROM teams t LEFT JOIN standings s ON s.team_id = t.id AND s.league_id = t.league_id
        WHERE t.league_id = ? ORDER BY s.rank, t.name""", (s.my_team_id, s.league_id))


@app.get("/standings")
def standings(conn: sqlite3.Connection = Depends(get_conn)):
    return teams(conn)


@app.get("/teams/{team_id}/roster")
def team_roster(team_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, team_id, s)
    players = sorted(ctx.roster(team_id), key=lambda p: (p.roster_status or "", p.group, p.name))
    return {"team_id": team_id, "team_name": ctx.teams[team_id], "roster_period": ctx.roster_period,
            "players": [A.player_summary(p, ctx) for p in players]}


@app.get("/teams/{team_id}/contracts")
def team_contracts(team_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    """Contracts from the team's salary sheet, how each line matched a Fantrax player, and the
    cap summary. Empty when no sheet is configured for the team (see salary_sheets.json)."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, team_id, s)
    contracts = rows(conn, """
        SELECT c.sheet_name, c.section, c.salary, c.years_remaining, c.salaries, c.player_id, c.match_method,
               p.name AS player_name, ri.team_id AS current_team_id, ri.status AS roster_status
        FROM contracts c
        LEFT JOIN players p ON p.fantrax_id = c.player_id
        LEFT JOIN roster_items ri ON ri.player_id = c.player_id AND ri.league_id = c.league_id
             AND ri.period = (SELECT MAX(period) FROM roster_items WHERE league_id = c.league_id)
        WHERE c.league_id = ? AND c.team_id = ? ORDER BY c.section, c.salary DESC""", (s.league_id, team_id))
    for c in contracts:
        c["salaries"] = json.loads(c["salaries"] or "[]")
        c["on_this_roster"] = c["current_team_id"] == team_id
    return {"team_id": team_id, "team_name": ctx.teams[team_id], "cap": A.cap_summary(ctx, team_id),
            "contracts": contracts}


@app.get("/matchups")
def matchups(period: Optional[int] = None, conn: sqlite3.Connection = Depends(get_conn)):
    s = get_settings()
    period = period or current_period(conn, s.league_id, "scoring")
    names = {r["id"]: r["name"] for r in rows(conn, "SELECT id, name FROM teams WHERE league_id=?", (s.league_id,))}
    sched = rows(conn, "SELECT seq, away_team_id, home_team_id FROM matchups WHERE league_id=? AND period=? "
                       "ORDER BY seq", (s.league_id, period))
    results = {(r["team_id"], r["opponent_id"]): r for r in rows(
        conn, "SELECT * FROM matchup_results WHERE league_id=? AND period=?", (s.league_id, period))}
    cats = rows(conn, "SELECT team_id, opponent_id, category, value, outcome FROM matchup_category_scores "
                      "WHERE league_id=? AND period=?", (s.league_id, period))
    by_pair: Dict = {}
    for c in cats:
        by_pair.setdefault((c["team_id"], c["opponent_id"]), {})[c["category"]] = {
            "value": c["value"], "outcome": c["outcome"]}
    out = []
    for m in sched:
        entry = {"away": None, "home": None}
        for side, me, opp in (("away", m["away_team_id"], m["home_team_id"]),
                              ("home", m["home_team_id"], m["away_team_id"])):
            if me is None:
                entry[side] = {"bye": True}
                continue
            res = results.get((me, opp)) or {}
            entry[side] = {"team_id": me, "team_name": names.get(me),
                           "category_wins": res.get("category_wins"), "category_losses": res.get("category_losses"),
                           "category_ties": res.get("category_ties"), "games_played": res.get("games_played"),
                           "categories": by_pair.get((me, opp), {})}
        out.append(entry)
    return {"period": period, "matchups": out}


@app.get("/players")
def players(status: str = Query("all", description="all, fa, or rostered"),
            position: Optional[str] = Query(None, description="F, D, G, C, LW or RW"),
            team_id: Optional[str] = None, q: Optional[str] = Query(None, description="Name search"),
            sort: str = Query("value", description="value or adp"),
            min_games: int = 0, limit: int = 50, offset: int = 0,
            conn: sqlite3.Connection = Depends(get_conn)):
    s = get_settings()
    ctx = _ctx(conn, s)
    if team_id:
        team_id = _team(ctx, team_id, s)
    generic = A._split({c.short_name: c.weight for c in ctx.categories}, ctx)
    needle = (q or "").lower()

    def keep(p: A.Player) -> bool:
        if status == "fa" and p.team_id is not None:
            return False
        if status == "rostered" and p.team_id is None:
            return False
        if team_id and p.team_id != team_id:
            return False
        if needle and needle not in p.name.lower():
            return False
        if min_games and p.gp_cur + p.gp_prior < min_games:
            return False
        if not needle and not team_id and not p.z:
            return False  # hide the thousands of pool players with no NHL stats unless asked for
        return A._matches_position(p, position)

    pool = [p for p in ctx.players.values() if keep(p)]
    if sort == "adp":
        pool.sort(key=lambda p: (p.adp is None, p.adp or 0))
    else:
        pool.sort(key=lambda p: -(A.player_value(p, generic) if p.z else -1e9))
    return {"total": len(pool), "players": [A.player_summary(p, ctx) for p in pool[offset:offset + limit]]}


@app.get("/players/{fantrax_id}")
def player(fantrax_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    ctx = _ctx(conn, get_settings())
    p = ctx.players.get(fantrax_id)
    if not p:
        raise HTTPException(404, detail="Unknown player id {}".format(fantrax_id))
    out = A.player_summary(p, ctx)
    if p.nhl_id:
        table = "goalie_stats" if p.group == "G" else "skater_stats"
        out["nhl_id"] = p.nhl_id
        out["season_stats"] = rows(conn, "SELECT * FROM {} WHERE nhl_id=? ORDER BY season_id DESC".format(table),
                                   (p.nhl_id,))
    return out


# --------------------------------------------------------------------------- analysis


@app.get("/analysis/overview")
def overview(lineup: str = LINEUP, conn: sqlite3.Connection = Depends(get_conn)):
    """Every team's projected category ranks (1 = best) and actual H2H category records."""
    ctx = _ctx(conn, get_settings())
    return {"teams": A.league_overview(conn, ctx, lineup)}


@app.get("/analysis/teams/{team_id}/profile")
def profile(team_id: str, strategy: str = STRATEGY, spread: float = SPREAD, punt: Optional[str] = PUNT,
            weights: Optional[str] = WEIGHTS, lineup: str = LINEUP, conn: sqlite3.Connection = Depends(get_conn)):
    """Category strengths/weaknesses (projected and actual) plus your roster valued by your needs."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, team_id, s)
    return A.team_profile(conn, ctx, team_id, **_need_kwargs(ctx, strategy, spread, punt, weights, lineup))


@app.get("/analysis/teams/{team_id}/targets")
def targets(team_id: str,
            source: Optional[str] = Query(None, description="fa (waivers/free agents) or trade. "
                                                             "Defaults to trade when from_team/offer is set, else fa"),
            from_team: Optional[str] = Query(None, description="Only players on this team: team id or a unique "
                                                                "part of its name (implies source=trade)"),
            offer: Optional[str] = Query(None, description="Player(s) on your roster to trade away, comma-separated "
                                                            "ids or unique parts of names. Returns only players of "
                                                            "comparable market value (implies source=trade)"),
            tolerance: float = Query(0.25, gt=0, description="With offer: how far the trade rank may differ, as a "
                                                              "fraction (0.25 = within 25%, at least ±3 ranks)"),
            position: Optional[str] = Query(None, description="F, D, G, C, LW or RW"),
            limit: int = 25, min_games: int = Query(5, description="Min NHL games (this + last season)"),
            include_minors: bool = False, strategy: str = STRATEGY, spread: float = SPREAD,
            punt: Optional[str] = PUNT, weights: Optional[str] = WEIGHTS, lineup: str = LINEUP,
            conn: sqlite3.Connection = Depends(get_conn)):
    """Players ranked by how much they help *your* category needs."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, team_id, s)
    if from_team is not None:
        resolved = ctx.resolve_team(from_team)
        if resolved is None:
            raise HTTPException(404, detail={"error": "No single team matches from_team '{}'".format(from_team),
                                             "teams": [{"id": t, "name": n} for t, n in ctx.teams.items()]})
        from_team = resolved
    offer_ids: List[str] = []
    for part in (offer.split(",") if offer else []):
        resolved = ctx.resolve_player(part.strip(), team_id)
        if resolved is None:
            raise HTTPException(404, detail={
                "error": "No single player on {} matches offer '{}'".format(ctx.teams[team_id], part.strip()),
                "roster": sorted(p.name for p in ctx.roster(team_id) if p.z)})
        offer_ids.append(resolved)
    source = source or ("trade" if from_team or offer_ids else "fa")
    if source not in ("fa", "trade"):
        raise HTTPException(400, detail="source must be fa or trade")
    try:
        return A.targets(conn, ctx, team_id, source=source, position=position, limit=limit, min_games=min_games,
                         include_minors=include_minors, from_team=from_team, offer=offer_ids or None,
                         tolerance=tolerance,
                         **_need_kwargs(ctx, strategy, spread, punt, weights, lineup))
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))


@app.get("/analysis/teams/{team_id}/trade-partners")
def trade_partners(team_id: str, per_team: int = 3, strategy: str = STRATEGY, spread: float = SPREAD,
                   punt: Optional[str] = PUNT, weights: Optional[str] = WEIGHTS, lineup: str = LINEUP,
                   conn: sqlite3.Connection = Depends(get_conn)):
    """Teams whose strengths cover your weaknesses (and vice versa), with candidate players each way."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, team_id, s)
    return A.trade_partners(conn, ctx, team_id, per_team=per_team,
                            **_need_kwargs(ctx, strategy, spread, punt, weights, lineup))


class TradeRequest(BaseModel):
    team_id: str = "me"
    give: List[str] = []
    other_team_id: Optional[str] = None
    get: List[str] = []
    lineup: str = "best"
    spread: float = 1.5


@app.post("/analysis/trade")
def trade(req: TradeRequest, conn: sqlite3.Connection = Depends(get_conn)):
    """Cost/benefit of a trade for both teams: change in expected category wins per week, per-category
    before/after, games and starts volume, roster-limit drops and the market value moving each way.
    Leave other_team_id empty to see what losing the `give` players costs you."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, req.team_id, s)
    other = None
    if req.other_team_id:
        other = ctx.resolve_team(req.other_team_id)
        if other is None:
            raise HTTPException(404, detail="No single team matches '{}'".format(req.other_team_id))
    if req.lineup not in ("best", "active"):
        raise HTTPException(400, detail="lineup must be best or active")
    try:
        return A.evaluate_trade(ctx, team_id, req.give, other, req.get, lineup=req.lineup, spread=req.spread)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
