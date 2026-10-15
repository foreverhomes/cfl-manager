# CFL Manager

A local data store and analysis API for the **Competelevel Fantasy League** on Fantrax: an 11-team
dynasty league with an auction draft and H2H "each category" scoring.

It pulls league structure, rosters, schedules and weekly category results from Fantrax, and player
stats from NHL.com. Everything goes into a single SQLite file. The analysis layer projects every
player, profiles every team by category, and ranks waiver and trade targets by what *your* team needs.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # then set FANTRAX_MY_TEAM_ID
.venv/bin/python -m cfl sync    # ~2s, writes data/league.db
.venv/bin/python -m cfl teams   # find your team id for .env
.venv/bin/python -m cfl serve   # http://127.0.0.1:8000/docs
```

macOS has no bare `python` command, and the dependencies live in `.venv`. Either prefix commands
with `.venv/bin/python` as above, or activate the venv once per terminal session
(`source .venv/bin/activate`). After that, `python -m cfl ...` works, as written in the rest of this file.

## Data sources

| Source | What | Auth |
|---|---|---|
| Fantrax public API (`/fxea/general/*`) | league settings, scoring categories, teams, schedule, rosters, standings, weekly matchup category scores, player IDs/eligibility/ADP | none |
| NHL.com stats API (`api.nhle.com/stats/rest`) | per-player season stats: G, A, Pt, +/-, PPP, SOG, Hits, Blocks; goalie W, GA, SA, saves, SO, TOI | none |

The Fantrax public API has no per-player stats, and its internal stats endpoint needs a login for
private leagues. So player stats come from NHL.com. They're linked to Fantrax IDs by name, with
team and position breaking ties, plus a nickname fallback (Matt/Matthew, Dmitry/Dmitri). The `match`
sync step reports anything it couldn't link.

## Sync

```bash
python -m cfl sync                    # everything, in dependency order
python -m cfl sync rosters matchups   # just the fast-changing bits
```

Resources: `league`, `players`, `rosters`, `standings`, `matchups`, `stats`, `match`. Matchup scores
for completed periods are fetched once and skipped after that. Every run is recorded in `sync_log`.

## API

Interactive docs are at `/docs`. Anywhere a `team_id` goes, you can use `me` (it reads
`FANTRAX_MY_TEAM_ID`).

| Endpoint | |
|---|---|
| `POST /sync?resources=rosters&resources=matchups` | refresh from Fantrax / NHL.com |
| `GET /league`, `/categories`, `/teams`, `/standings` | league data |
| `GET /teams/{id}/roster` | roster with per-game projections and z-scores |
| `GET /matchups?period=N` | schedule + category scores for a week |
| `GET /players?status=fa&position=D&sort=value` | search the player pool |
| `GET /players/{fantrax_id}` | one player, including raw NHL season stats |
| `GET /analysis/overview` | every team's projected rank per category, plus actual H2H records |
| `GET /analysis/teams/me/profile` | your category strengths and weaknesses, need weights, valued roster |
| `GET /analysis/teams/me/targets?source=fa` | free agents ranked by upgrade over your weakest player at the position |
| `GET /analysis/teams/me/targets?source=trade&position=G` | players on other teams that fit your needs |
| `GET /analysis/teams/me/trade-partners` | teams whose strengths match your weaknesses, players each way, your trade chips |

Common analysis parameters:
- `strategy`: `marginal` (default), `weakness` or `even`
- `punt`: categories to ignore, e.g. `Hit,PM` (use `PM` for +/-)
- `weights`: manual weights, e.g. `Hit:0,G:1.5`
- `lineup`: `best` (default) or `active`

## How the analysis works

1. **Player projection.** Each stat's per-game rate blends this season with last season:
   `(this + 0.5·last + 5·league_avg) / (gp_this + 0.5·gp_last + 5)`. Last season dominates in
   October, and this season takes over as games accumulate. The 5 pseudo-games stop a hot
   three-game call-up from looking like McDavid. Goalie counting stats are scaled by start share,
   so a backup's W/SHO reflect how often he actually plays. GAA and SV% are valued as goals saved
   vs. an average goalie, weighted by workload.
2. **z-scores** for each category, measured against the fantasy-relevant pool: the top
   *teams × active slots* players.
3. **Team profiles.** Each team's best legal lineup (2C/2LW/2RW/6F/6D/2G from non-minors, non-IR
   players) gives projected weekly totals per category, ranked across the league. The actual
   season-to-date category W-L-T comes from Fantrax matchup scores.
4. **Need weights.** In H2H each-category, an extra unit matters most in categories where several
   opponents are close to you. It matters least where you're already far ahead, or so far behind
   it's a lost cause. `marginal` puts a Gaussian kernel on your z-gap to each opponent;
   `weakness` simply weights by rank. Players are ranked by `Σ need_weight × z`.

## Layout

```
cfl/
  clients.py    Fantrax + NHL.com HTTP clients
  db.py         SQLite schema + helpers
  sync.py       ingest + Fantrax↔NHL id matching
  analysis.py   projections, z-scores, team profiles, targets, trade partners
  api.py        FastAPI app
  __main__.py   CLI (sync / serve / teams / profile / targets)
tests/          offline unit tests (pytest)
data/           league.db (git-ignored)
```

CLI shortcuts for quick looks without the server:

```bash
python -m cfl profile                  # your team (or pass a team id / part of a name)
python -m cfl targets --position D
python -m cfl targets --source trade --punt Hit
```
