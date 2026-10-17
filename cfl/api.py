"""HTTP API over the league store. Run: python -m cfl serve (or deploy to Vercel; see README)"""

import json
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from typing import Dict, Iterator, List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis as A
from . import auth
from . import changes
from . import contracts as C
from . import offseason as O
from . import picks as PK
from . import publish as P
from .auth import User
from .config import Settings, get_settings
from .db import Connection, connect, row, rows, transaction
from .sync import RESOURCES, _logged, current_period, now_iso, sync_all

app = FastAPI(
    title="CFL Manager",
    description="Local store + analysis API for a Fantrax fantasy hockey league.",
    version="0.1.0",
)

# The web app is served "no-cache": the browser keeps its copy but checks it with the server
# (a cheap 304 when unchanged) on every load, so an update shows up without a hard refresh.
NO_CACHE = {"Cache-Control": "no-cache"}


class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.update(NO_CACHE)
        return response


# "+" in an unencoded query string arrives as a space, so "+/-" shows up as "/-" after stripping.
WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/static", NoCacheStaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(str(WEB_DIR / "index.html"), headers=NO_CACHE)


@app.get("/privacy", include_in_schema=False)
def privacy():
    """Public, like the page: Google's sign-in setup asks for a privacy policy link."""
    return FileResponse(str(WEB_DIR / "privacy.html"), headers=NO_CACHE)


CATEGORY_ALIASES = {"pm": "+/-", "plusminus": "+/-", "/-": "+/-", "svpct": "SV%"}


def get_conn() -> Iterator[Connection]:
    conn = connect(get_settings().database)
    try:
        yield conn
    finally:
        conn.close()


@app.exception_handler(A.NotSynced)
def _not_synced(_, exc: A.NotSynced):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# --------------------------------------------------------------------------- who's asking

NO_LOGINS = ("Sign-in isn't set up for this deployment: set GOOGLE_CLIENT_ID, CFL_SESSION_SECRET and "
             "CFL_ADMIN_EMAILS in the Vercel project (see README, \"Logins\"), then redeploy.")


def _same_site(request: Request) -> bool:
    """A write carrying the login cookie must come from this site's own pages. Browsers send Origin
    on cross-site writes; a request without one (curl, or an older browser's same-site form) passes."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    return urlsplit(origin).netloc in {request.headers.get("host"), request.headers.get("x-forwarded-host")}


def _logins_ready() -> None:
    """Refuse everything on a deployment whose logins aren't set up, before touching the database."""
    s = get_settings()
    if (s.on_vercel or s.google_client_id) and not (s.google_client_id and s.session_secret):
        raise HTTPException(503, detail=NO_LOGINS)


def signed_in_user(request: Request, _: None = Depends(_logins_ready), conn: Connection = Depends(get_conn)) -> User:
    """Whoever sent the request, approved or not. 401 when nobody is signed in."""
    s = get_settings()
    if not s.google_client_id:
        return auth.local_admin(s)       # no logins: your own computer
    email = auth.read_session(request.cookies.get(auth.COOKIE), s.session_secret)
    user = auth.load_user(conn, s, email) if email else None
    if user is None:
        raise HTTPException(401, detail="Sign in to continue")
    if request.method not in ("GET", "HEAD") and not _same_site(request):
        raise HTTPException(403, detail="Refused a request from another site")
    return user


def current_user(user: User = Depends(signed_in_user)) -> User:
    if user.status != "active":
        raise HTTPException(403, detail="Your account is waiting for the admin's approval")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, detail="Only the admin can do that")
    return user


# Every data route needs an approved user. The page itself and /auth/* don't.
data = APIRouter(dependencies=[Depends(current_user)])
ADMIN_ONLY = [Depends(require_admin)]


def _ctx(conn: Connection, settings: Settings) -> A.Context:
    return A.load_context(conn, settings)


def _team(ctx: A.Context, team_id: str, user: User) -> str:
    if team_id == "me":
        if not user.team_id:
            raise HTTPException(400, detail={
                "error": "No team is linked to your account yet: set it in the Admin tab" if get_settings().google_client_id
                         else "Set FANTRAX_MY_TEAM_ID in .env to use 'me'",
                "teams": [{"id": t, "name": n} for t, n in ctx.teams.items()],
            })
        team_id = user.team_id
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


@data.get("/health")
def health(conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    s = get_settings()
    last = row(conn, "SELECT resource, finished_at, status FROM sync_log ORDER BY id DESC LIMIT 1")
    return {"ok": True, "league_id": s.league_id, "my_team_id": user.team_id, "database": s.database_label,
            "last_sync": last, "refresh_wait_seconds": refresh_wait(conn)}


def auto_publish() -> None:
    """Background job: republish app-owned teams' sheets if anything changed. Logged in sync_log."""
    settings = get_settings()
    conn = connect(settings.database)
    try:
        if P.publish_status(conn, settings)["configured"]:
            _logged(conn, "publish", lambda: P.publish(conn, settings))
    finally:
        conn.close()


REFRESH_COOLDOWN = timedelta(minutes=10)


def refresh_wait(conn: Connection) -> int:
    """Seconds until the next refresh may start. One cooldown for the whole league."""
    last = row(conn, "SELECT started_at FROM sync_log WHERE resource IN ({}) ORDER BY id DESC LIMIT 1"
               .format(", ".join("?" * len(auth.GM_SYNC_RESOURCES))), tuple(auth.GM_SYNC_RESOURCES))
    if not last:
        return 0
    left = datetime.fromisoformat(last["started_at"]) + REFRESH_COOLDOWN - datetime.now(timezone.utc)
    return max(0, ceil(left.total_seconds()))


@data.post("/sync")
def sync(background: BackgroundTasks,
         resources: Optional[List[str]] = Query(None, description="Subset of: " + ", ".join(RESOURCES)),
         conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    quick = resources and set(resources) <= auth.GM_SYNC_RESOURCES
    if not user.is_admin and not quick:
        raise HTTPException(403, detail="Only the admin can run a full sync")
    wait = refresh_wait(conn) if quick else 0
    if wait:
        raise HTTPException(429, headers={"Retry-After": str(wait)},
                            detail="Rosters and stats were refreshed in the last 10 minutes. "
                                   "Try again in {} min.".format(ceil(wait / 60)))
    try:
        results = sync_all(conn, get_settings(), resources)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    background.add_task(auto_publish)
    return {"ok": all(r["status"] == "ok" for r in results), "results": results}


@data.get("/publish", dependencies=ADMIN_ONLY)
def publish_status(conn: Connection = Depends(get_conn)):
    """Whether sheet publishing is set up, and when each app-owned team's sheet was last written."""
    out = P.publish_status(conn, get_settings())
    last = row(conn, "SELECT finished_at, status, detail FROM sync_log WHERE resource='publish' ORDER BY id DESC LIMIT 1")
    if last:
        last["detail"] = json.loads(last["detail"]) if last["detail"] else None
    out["last_run"] = last
    return out


@data.post("/publish", dependencies=ADMIN_ONLY)
def publish_now(force: bool = Query(False, description="Write even if nothing changed"),
                conn: Connection = Depends(get_conn)):
    """Write app-owned teams' contracts to their Google Sheets now."""
    s = get_settings()
    return _logged(conn, "publish", lambda: P.publish(conn, s, force=force))


@data.get("/sync/log")
def sync_log(limit: int = 20, conn: Connection = Depends(get_conn)):
    out = rows(conn, "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,))
    for r in out:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return out


@data.get("/league")
def league(conn: Connection = Depends(get_conn)):
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


@data.get("/categories")
def categories(conn: Connection = Depends(get_conn)):
    return rows(conn, "SELECT short_name, name, code, grp, weight, lower_is_better FROM scoring_categories "
                      "WHERE league_id=? ORDER BY sort_order", (get_settings().league_id,))


@data.get("/teams")
def teams(conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    s = get_settings()
    return rows(conn, """
        SELECT t.id, t.name, t.short_name, s.rank, s.points AS record, s.games_back, s.win_pct,
               CASE WHEN t.id = ? THEN 1 ELSE 0 END AS is_me
        FROM teams t LEFT JOIN standings s ON s.team_id = t.id AND s.league_id = t.league_id
        WHERE t.league_id = ? ORDER BY s.rank NULLS FIRST, t.name""", (user.team_id, s.league_id))


@data.get("/standings")
def standings(conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    return teams(conn, user)


@data.get("/teams/{team_id}/roster")
def team_roster(team_id: str, conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, team_id, user)
    players = sorted(ctx.roster(team_id), key=lambda p: (p.roster_status or "", p.group, p.name))
    return {"team_id": team_id, "team_name": ctx.teams[team_id], "roster_period": ctx.roster_period,
            "players": [A.player_summary(p, ctx) for p in players]}


@data.get("/teams/{team_id}/contracts")
def team_contracts(team_id: str, conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """Contracts from the team's salary sheet, how each line matched a Fantrax player, and the
    cap summary. Empty when no sheet is configured for the team (see salary_sheets.json)."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, team_id, user)
    contracts = rows(conn, """
        SELECT c.sheet_name, c.section, c.salary, c.years_remaining, c.salaries, c.player_id, c.match_method,
               p.name AS player_name, ri.team_id AS current_team_id, ri.status AS roster_status
        FROM contracts c
        LEFT JOIN players p ON p.fantrax_id = c.player_id
        LEFT JOIN roster_items ri ON ri.player_id = c.player_id AND ri.league_id = c.league_id
             AND ri.period = (SELECT MAX(period) FROM roster_items WHERE league_id = c.league_id)
        WHERE c.league_id = ? AND c.team_id = ? ORDER BY c.section, c.salary DESC NULLS LAST""", (s.league_id, team_id))
    for c in contracts:
        c["salaries"] = json.loads(c["salaries"] or "[]")
        c["on_this_roster"] = c["current_team_id"] == team_id
    return {"team_id": team_id, "team_name": ctx.teams[team_id], "cap": A.cap_summary(ctx, team_id),
            "contracts": contracts}


@data.get("/matchups")
def matchups(period: Optional[int] = None, conn: Connection = Depends(get_conn)):
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


@data.get("/players")
def players(status: str = Query("all", description="all, fa, or rostered"),
            position: Optional[str] = Query(None, description="F, D, G, C, LW or RW"),
            team_id: Optional[str] = None, q: Optional[str] = Query(None, description="Name search"),
            sort: str = Query("value", description="value or adp"),
            min_games: int = 0, limit: int = 50, offset: int = 0,
            conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    s = get_settings()
    ctx = _ctx(conn, s)
    if team_id:
        team_id = _team(ctx, team_id, user)
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


@data.get("/players/{fantrax_id}")
def player(fantrax_id: str, conn: Connection = Depends(get_conn)):
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


class ContractEdit(BaseModel):
    years: int                          # seasons left, this one included
    salary: Optional[float] = None      # per season; defaults to the player's current salary ($3 for an ELC)
    contract_type: str = "STANDARD"     # STANDARD / ELC / IN_SEASON_UFA
    note: Optional[str] = None


def _player_or_404(ctx: A.Context, fantrax_id: str) -> A.Player:
    p = ctx.players.get(fantrax_id)
    if not p:
        raise HTTPException(404, detail="Unknown player id {}".format(fantrax_id))
    return p


def _editable_player(ctx: A.Context, fantrax_id: str, user: User) -> A.Player:
    """The player, if this user may change his contract or birth date (403 otherwise)."""
    p = _player_or_404(ctx, fantrax_id)
    if not auth.can_edit_player(user, p.team_id):
        raise HTTPException(403, detail="You can only edit players on your own team")
    return p


def _contract_detail(conn: Connection, ctx: A.Context, fantrax_id: str, user: User) -> Dict:
    out = C.contract_detail(conn, ctx, fantrax_id)
    out["can_edit"] = auth.can_edit_player(user, ctx.players[fantrax_id].team_id)
    return out


def _record_edit(conn: Connection, user: User) -> A.Context:
    """Log the contract edit just saved, as made by this user. Returns the context that includes it."""
    ctx = _ctx(conn, get_settings())
    changes.record(conn, ctx, user.name or user.email)
    return ctx


@data.get("/players/{fantrax_id}/contract")
def player_contract(fantrax_id: str, conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """The player's contract as the app uses it, what his salary sheet says, and any edit made here."""
    ctx = _ctx(conn, get_settings())
    _player_or_404(ctx, fantrax_id)
    return _contract_detail(conn, ctx, fantrax_id, user)


@data.put("/players/{fantrax_id}/contract")
def edit_player_contract(fantrax_id: str, edit: ContractEdit, background: BackgroundTasks,
                         conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """Set how many years are left on a contract (and its type). It overrides the salary sheet, and
    survives syncs, until reverted. The salary stays as it is unless one is given. A GM can edit
    players on their own team; the admin, anyone."""
    s = get_settings()
    p = _editable_player(_ctx(conn, s), fantrax_id, user)
    salary = edit.salary
    if salary is None:
        salary = C.ELC_SALARY if edit.contract_type == "ELC" else p.salary
    if salary is None:
        raise HTTPException(400, detail="{} has no salary on file yet; include one".format(p.name))
    try:
        C.save_override(conn, s.league_id, fantrax_id, [salary] * max(edit.years, 0), edit.contract_type, edit.note)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    background.add_task(auto_publish)
    return _contract_detail(conn, _record_edit(conn, user), fantrax_id, user)


class BirthDateEdit(BaseModel):
    birth_date: Optional[str] = None    # YYYY-MM-DD; null drops a manual date so syncs can fill it again


@data.put("/players/{fantrax_id}/birth-date")
def set_birth_date(fantrax_id: str, edit: BirthDateEdit, background: BackgroundTasks,
                   conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """Set a player's birth date by hand (it decides RFA vs UFA). Syncs never overwrite it."""
    from datetime import date
    s = get_settings()
    _editable_player(_ctx(conn, s), fantrax_id, user)
    if edit.birth_date:
        try:
            born = date.fromisoformat(edit.birth_date)
        except ValueError:
            raise HTTPException(400, detail="birth_date must look like 2004-03-30")
        if not 1960 <= born.year <= date.today().year - 15:
            raise HTTPException(400, detail="That birth date doesn't look right")
        conn.execute("UPDATE players SET birth_date=?, birth_date_source='manual' WHERE fantrax_id=?",
                     (born.isoformat(), fantrax_id))
    else:
        conn.execute("UPDATE players SET birth_date=NULL, birth_date_source=NULL WHERE fantrax_id=?", (fantrax_id,))
    conn.commit()
    background.add_task(auto_publish)
    return _contract_detail(conn, _ctx(conn, s), fantrax_id, user)


@data.delete("/players/{fantrax_id}/contract")
def revert_player_contract(fantrax_id: str, background: BackgroundTasks, conn: Connection = Depends(get_conn),
                           user: User = Depends(current_user)):
    """Drop the app's contract for this player (for a sheet-read team, the sheet applies again)."""
    s = get_settings()
    _editable_player(_ctx(conn, s), fantrax_id, user)
    C.delete_override(conn, s.league_id, fantrax_id)
    background.add_task(auto_publish)
    return _contract_detail(conn, _record_edit(conn, user), fantrax_id, user)


# --------------------------------------------------------------------------- draft picks


def _pick_out(pick: Dict, teams: Dict[str, str]) -> Dict:
    last = pick["last_move"]
    return {"draft_year": pick["draft_year"], "round": pick["round"],
            "original_team_id": pick["original_team_id"], "original_team_name": teams[pick["original_team_id"]],
            "owner_team_id": pick["owner_team_id"], "owner_team_name": teams.get(pick["owner_team_id"]),
            "last_move": None if last is None else {
                "deal_id": last["deal_id"], "kind": last["kind"], "from_team_id": last["from_team_id"],
                "from_team_name": teams.get(last["from_team_id"]), "note": last["note"], "source": last["source"],
                "moved_at": last["moved_at"]}}


def _team_picks(conn: Connection, ctx: A.Context, team_id: str, user: User) -> Dict:
    season = ctx.league.get("season_year")
    teams = ctx.teams
    picks = [_pick_out(p, teams) for p in PK.all_picks(conn, ctx.league_id, list(teams), season)]
    latest = {(m["draft_year"], m["round"], m["original_team_id"]): m["id"]
              for m in PK.latest_moves(conn, ctx.league_id).values()}
    deals: Dict[str, Dict] = {}
    for m in rows(conn, "SELECT * FROM pick_moves WHERE league_id=? AND (from_team_id=? OR to_team_id=?) "
                        "ORDER BY id DESC", (ctx.league_id, team_id, team_id)):
        d = deals.setdefault(m["deal_id"], {"deal_id": m["deal_id"], "kind": m["kind"], "note": m["note"],
                                            "source": m["source"], "moved_at": m["moved_at"], "moves": [],
                                            "can_undo": True})
        d["moves"].append({"draft_year": m["draft_year"], "round": m["round"],
                           "original_team_id": m["original_team_id"], "original_team_name": teams.get(m["original_team_id"]),
                           "from_team_id": m["from_team_id"], "from_team_name": teams.get(m["from_team_id"]),
                           "to_team_id": m["to_team_id"], "to_team_name": teams.get(m["to_team_id"])})
        # Undo puts picks back, so it's for whoever could have sent them, and only while none has moved since.
        moved_again = latest[(m["draft_year"], m["round"], m["original_team_id"])] != m["id"]
        if moved_again or not auth.can_edit_team(user, m["from_team_id"]):
            d["can_undo"] = False
    for d in deals.values():
        d["moves"].reverse()
    return {"team_id": team_id, "team_name": teams[team_id], "draft_years": PK.draft_years(season),
            "rounds": PK.PICK_ROUNDS, "kinds": PK.MOVE_KINDS, "can_edit": auth.can_edit_team(user, team_id),
            "owned": [p for p in picks if p["owner_team_id"] == team_id],
            "sent": [p for p in picks if p["original_team_id"] == team_id and p["owner_team_id"] != team_id],
            "deals": list(deals.values())}


@data.get("/teams/{team_id}/picks")
def team_picks(team_id: str, conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """The team's draft picks in the upcoming drafts: its own picks it hasn't sent away, plus picks it
    received in trades or as RFA compensation. Also lists its own picks that are now elsewhere, and
    every pick deal it was part of, newest first."""
    ctx = _ctx(conn, get_settings())
    return _team_picks(conn, ctx, _team(ctx, team_id, user), user)


class PickRef(BaseModel):
    draft_year: int
    round: int
    original_team_id: str               # whose pick it was to start with


class PickDeal(BaseModel):
    kind: str = "trade"                 # trade / rfa (compensation for signing another team's RFA)
    team_id: str                        # sends `send`
    other_team_id: str                  # gets `send`; in a trade, sends `receive` back
    send: List[PickRef] = []
    receive: List[PickRef] = []
    note: Optional[str] = None


@data.post("/picks/deals")
def record_pick_deal(deal: PickDeal, conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """Move draft picks between two teams. A GM sends their own team's picks; picks coming back in a
    trade need the other team's GM or the admin. RFA compensation goes one way only."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id, other = _team(ctx, deal.team_id, user), _team(ctx, deal.other_team_id, user)
    for side, sender in ((deal.send, team_id), (deal.receive, other)):
        if side and not auth.can_edit_team(user, sender):
            raise HTTPException(403, detail="Only {}'s GM or the admin can send its picks".format(ctx.teams[sender]))
    if deal.kind == "rfa" and deal.receive:
        raise HTTPException(400, detail="RFA compensation goes one way: from the team that signed him to the "
                                        "team that lost him")
    sends = [(team_id, other, [p.model_dump() for p in deal.send]),
             (other, team_id, [p.model_dump() for p in deal.receive])]
    try:
        PK.move(conn, s.league_id, list(ctx.teams), ctx.league.get("season_year"), deal.kind, sends, deal.note,
                user.name or user.email)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return _team_picks(conn, ctx, team_id, user)


@data.delete("/picks/deals/{deal_id}")
def undo_pick_deal(deal_id: str, conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """Undo a pick deal, e.g. one recorded by mistake: its picks go back to the teams that sent them."""
    s = get_settings()
    moves = PK.deal_moves(conn, s.league_id, deal_id)
    if not moves:
        raise HTTPException(404, detail="No pick deal {}".format(deal_id))
    if not all(auth.can_edit_team(user, m["from_team_id"]) for m in moves):
        raise HTTPException(403, detail="Only the GMs who sent these picks, or the admin, can undo this")
    try:
        return {"ok": True, "picks": PK.undo(conn, s.league_id, deal_id)}
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc))


@data.get("/changes")
def change_log(team_id: Optional[str] = None,
               kind: List[str] = Query([], description="Subset of: added, dropped, traded, salary, contract, pick"),
               limit: int = 200, conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """Player and contract changes, newest first: roster moves from Fantrax syncs, salary and contract
    changes, and draft picks changing hands (kind `pick`, one row per pick)."""
    s = get_settings()
    if team_id:
        team_id = _team(_ctx(conn, s), team_id, user)        # accepts "me", like every team_id
    player_kinds = [k for k in kind if k != "pick"]
    out: List[Dict] = []
    if player_kinds or not kind:
        sql = ("SELECT pc.*, p.name AS player_name, t.name AS team_name FROM player_changes pc "
               "LEFT JOIN players p ON p.fantrax_id = pc.player_id LEFT JOIN teams t ON t.id = pc.team_id "
               "WHERE pc.league_id = ?")
        params: List = [s.league_id]
        if team_id:
            sql += " AND (pc.team_id = ? OR (pc.kind = 'traded' AND pc.old_value = ?))"
            params += [team_id, json.dumps(team_id)]
        if player_kinds:
            sql += " AND pc.kind IN ({})".format(", ".join("?" for _ in player_kinds))
            params += player_kinds
        out = rows(conn, sql + " ORDER BY pc.id DESC LIMIT ?", params + [limit])
        for c in out:
            c["old_value"], c["new_value"] = json.loads(c["old_value"]), json.loads(c["new_value"])
    if "pick" in kind or not kind:
        sql = ("SELECT m.*, t.name AS team_name, o.name AS original_team_name FROM pick_moves m "
               "LEFT JOIN teams t ON t.id = m.to_team_id LEFT JOIN teams o ON o.id = m.original_team_id "
               "WHERE m.league_id = ?")
        params = [s.league_id]
        if team_id:
            sql += " AND (m.from_team_id = ? OR m.to_team_id = ?)"
            params += [team_id, team_id]
        for m in rows(conn, sql + " ORDER BY m.id DESC LIMIT ?", params + [limit]):
            out.append({"kind": "pick", "player_id": None, "player_name": None, "team_id": m["to_team_id"],
                        "team_name": m["team_name"], "old_value": m["from_team_id"], "new_value": m["to_team_id"],
                        "source": m["source"], "changed_at": m["moved_at"],
                        "pick": {"draft_year": m["draft_year"], "round": m["round"], "deal_id": m["deal_id"],
                                 "original_team_id": m["original_team_id"],
                                 "original_team_name": m["original_team_name"], "move": m["kind"], "note": m["note"]}})
    # Each list is newest first; a stable sort interleaves them by time and keeps that order within a second.
    out.sort(key=lambda c: c["changed_at"], reverse=True)
    return out[:limit]


@data.get("/offseason", dependencies=ADMIN_ONLY)
def offseason(year: Optional[int] = Query(None, description="The offseason's year: RFA or UFA is decided on July 1 "
                                                            "of it. Defaults to the next one, after this season"),
              conn: Connection = Depends(get_conn)):
    """Rostered players whose contracts end in an offseason, each an RFA or a UFA by league rules."""
    try:
        return O.expiring(_ctx(conn, get_settings()), year)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))


# --------------------------------------------------------------------------- analysis


@data.get("/analysis/overview")
def overview(lineup: str = LINEUP, conn: Connection = Depends(get_conn)):
    """Every team's projected category ranks (1 = best) and actual H2H category records."""
    ctx = _ctx(conn, get_settings())
    return {"teams": A.league_overview(conn, ctx, lineup)}


@data.get("/analysis/teams/{team_id}/profile")
def profile(team_id: str, strategy: str = STRATEGY, spread: float = SPREAD, punt: Optional[str] = PUNT,
            weights: Optional[str] = WEIGHTS, lineup: str = LINEUP, conn: Connection = Depends(get_conn),
            user: User = Depends(current_user)):
    """Category strengths/weaknesses (projected and actual) plus your roster valued by your needs."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, team_id, user)
    return A.team_profile(conn, ctx, team_id, **_need_kwargs(ctx, strategy, spread, punt, weights, lineup))


@data.get("/analysis/teams/{team_id}/targets", dependencies=ADMIN_ONLY)
def targets(team_id: str,
            source: Optional[str] = Query(None, description="fa (waivers/free agents) or trade. "
                                                             "Defaults to trade when from_team/offer is set, else fa"),
            drop: Optional[str] = Query(None, description="With source=fa: player(s) on your roster to release for "
                                                           "each pickup, comma-separated ids or unique parts of "
                                                           "names. Without it, the best drop is found for each"),
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
            conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """Players ranked by how much they help *your* category needs."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, team_id, user)
    if from_team is not None:
        resolved = ctx.resolve_team(from_team)
        if resolved is None:
            raise HTTPException(404, detail={"error": "No single team matches from_team '{}'".format(from_team),
                                             "teams": [{"id": t, "name": n} for t, n in ctx.teams.items()]})
        from_team = resolved
    def roster_players(value: Optional[str], param: str) -> List[str]:
        ids = []
        for part in (value.split(",") if value else []):
            resolved = ctx.resolve_player(part.strip(), team_id)
            if resolved is None:
                raise HTTPException(404, detail={
                    "error": "No single player on {} matches {} '{}'".format(ctx.teams[team_id], param, part.strip()),
                    "roster": sorted(p.name for p in ctx.roster(team_id) if p.z)})
            ids.append(resolved)
        return ids

    offer_ids, drop_ids = roster_players(offer, "offer"), roster_players(drop, "drop")
    source = source or ("trade" if from_team or offer_ids else "fa")
    if source not in ("fa", "trade"):
        raise HTTPException(400, detail="source must be fa or trade")
    try:
        return A.targets(conn, ctx, team_id, source=source, position=position, limit=limit, min_games=min_games,
                         include_minors=include_minors, from_team=from_team, offer=offer_ids or None,
                         tolerance=tolerance, drop=drop_ids or None,
                         **_need_kwargs(ctx, strategy, spread, punt, weights, lineup))
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))


@data.get("/analysis/teams/{team_id}/trade-partners", dependencies=ADMIN_ONLY)
def trade_partners(team_id: str, per_team: int = 3, strategy: str = STRATEGY, spread: float = SPREAD,
                   punt: Optional[str] = PUNT, weights: Optional[str] = WEIGHTS, lineup: str = LINEUP,
                   conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """Teams whose strengths cover your weaknesses (and vice versa), with candidate players each way."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, team_id, user)
    return A.trade_partners(conn, ctx, team_id, per_team=per_team,
                            **_need_kwargs(ctx, strategy, spread, punt, weights, lineup))


class TradeRequest(BaseModel):
    team_id: str = "me"
    give: List[str] = []
    other_team_id: Optional[str] = None
    get: List[str] = []
    lineup: str = "best"
    spread: float = 1.5


@data.post("/analysis/trade", dependencies=ADMIN_ONLY)
def trade(req: TradeRequest, conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """Cost/benefit of a trade for both teams: change in expected category wins per week, per-category
    before/after, games and starts volume, roster-limit drops and the market value moving each way.
    Leave other_team_id empty for a waiver move: `give` players are released and `get` must be
    free agents (or leave `get` empty to see what losing the `give` players costs you)."""
    s = get_settings()
    ctx = _ctx(conn, s)
    team_id = _team(ctx, req.team_id, user)
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


# --------------------------------------------------------------------------- logins


class GoogleSignIn(BaseModel):
    credential: str                     # the ID token Google's sign-in button hands the page


def _secure_cookie(request: Request) -> bool:
    return get_settings().on_vercel or request.url.scheme == "https"


@app.get("/auth/config")
def auth_config():
    """What the sign-in page needs: whether logins are on, and the (public) Google client id."""
    s = get_settings()
    return {"enabled": bool(s.google_client_id), "google_client_id": s.google_client_id}


@app.get("/auth/me")
def auth_me(user: User = Depends(signed_in_user)):
    """Who's signed in, including someone still waiting for approval. 401 when nobody is."""
    return user.public(auth=bool(get_settings().google_client_id))


@app.post("/auth/google")
def auth_google(sign_in: GoogleSignIn, request: Request, response: Response, conn: Connection = Depends(get_conn)):
    """Sign in with a Google ID token. Someone new is recorded as waiting for the admin's approval."""
    s = get_settings()
    if not (s.google_client_id and s.session_secret):
        raise HTTPException(503, detail=NO_LOGINS)
    if not _same_site(request):
        raise HTTPException(403, detail="Refused a request from another site")
    try:
        claims = auth.verify_google_credential(sign_in.credential, s.google_client_id)
    except Exception:  # noqa: BLE001 - a bad, expired or foreign token all get the same answer
        raise HTTPException(401, detail="Google sign-in didn't work. Try again.")
    user = auth.record_login(conn, s, claims)
    response.set_cookie(auth.COOKIE, auth.make_session(user.email, s.session_secret),
                        max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=_secure_cookie(request))
    return user.public()


@app.post("/auth/logout")
def auth_logout(request: Request, response: Response):
    response.delete_cookie(auth.COOKIE, httponly=True, samesite="lax", secure=_secure_cookie(request))
    return {"ok": True}


# --------------------------------------------------------------------------- admin: who has access


class UserAccess(BaseModel):
    team_id: Optional[str] = None       # the GM's team; None unlinks it
    role: str = "gm"                    # gm / admin
    status: str = "active"              # pending / active


class NewUser(UserAccess):
    email: str


def _check_access(conn: Connection, access: UserAccess) -> None:
    if access.role not in auth.ROLES:
        raise HTTPException(400, detail="role must be one of: " + ", ".join(auth.ROLES))
    if access.status not in auth.STATUSES:
        raise HTTPException(400, detail="status must be one of: " + ", ".join(auth.STATUSES))
    if access.team_id is not None and not row(conn, "SELECT id FROM teams WHERE id=? AND league_id=?",
                                              (access.team_id, get_settings().league_id)):
        raise HTTPException(404, detail="Unknown team id {}".format(access.team_id))


@data.get("/admin/users", dependencies=ADMIN_ONLY)
def admin_users(conn: Connection = Depends(get_conn)):
    """Everyone who has signed in or been added: requests waiting for approval first."""
    s = get_settings()
    names = {r["id"]: r["name"] for r in rows(conn, "SELECT id, name FROM teams WHERE league_id=?", (s.league_id,))}
    users = rows(conn, "SELECT * FROM users ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, created_at")
    for u in users:
        u["always_admin"] = u["email"] in s.admin_emails
        u["team_name"] = names.get(u["team_id"])
    return {"users": users, "admin_emails": list(s.admin_emails)}


@data.post("/admin/users", dependencies=ADMIN_ONLY)
def admin_add_user(new: NewUser, conn: Connection = Depends(get_conn)):
    """Give someone access before they sign in, or change it if they're already listed."""
    email = new.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, detail="That doesn't look like an email address")
    _check_access(conn, new)
    with transaction(conn):
        if row(conn, "SELECT email FROM users WHERE email=?", (email,)):
            conn.execute("UPDATE users SET team_id=?, role=?, status=? WHERE email=?",
                         (new.team_id, new.role, new.status, email))
        else:
            conn.execute("INSERT INTO users (email, team_id, role, status, created_at) VALUES (?, ?, ?, ?, ?)",
                         (email, new.team_id, new.role, new.status, now_iso()))
    return admin_users(conn)


@data.put("/admin/users/{email}", dependencies=ADMIN_ONLY)
def admin_set_access(email: str, access: UserAccess, conn: Connection = Depends(get_conn)):
    """Approve a request, link someone's team, or change their role."""
    email = email.strip().lower()
    if not row(conn, "SELECT email FROM users WHERE email=?", (email,)):
        raise HTTPException(404, detail="No user {}".format(email))
    _check_access(conn, access)
    with transaction(conn):
        conn.execute("UPDATE users SET team_id=?, role=?, status=? WHERE email=?",
                     (access.team_id, access.role, access.status, email))
    return admin_users(conn)


@data.delete("/admin/users/{email}", dependencies=ADMIN_ONLY)
def admin_remove_user(email: str, conn: Connection = Depends(get_conn), user: User = Depends(current_user)):
    """Take away someone's access. If they sign in again, they're a new request."""
    email = email.strip().lower()
    if email == user.email:
        raise HTTPException(400, detail="You can't remove yourself")
    with transaction(conn):
        conn.execute("DELETE FROM users WHERE email=?", (email,))
    return admin_users(conn)


app.include_router(data)
