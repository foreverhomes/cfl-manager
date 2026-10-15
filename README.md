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
.venv/bin/python -m cfl serve   # app at http://127.0.0.1:8000, API docs at /docs
```

## Web app

`python -m cfl serve` (add `--reload` while developing) serves the app at http://127.0.0.1:8000.
There's no build step: it's plain HTML, CSS and JavaScript in `cfl/web/`.

- **Dashboard.** Shows expected category wins per week, projected strength, standings, and
  skater-game and goalie-start volume. A category profile charts your projected strength against
  the league average, with actual weekly W-L-T and need weights. A league heatmap shows every
  team's projected rank in every category. A sortable roster table rounds it out. The team picker
  in the header lets you view any team.
- **Players.** Search the league or browse a team, filter to players missing a salary, and edit
  any player's contract.
- **Trade builder.** Select any of your players. Optionally pick a trade partner and their players.
  - **Live evaluation:** the trade is re-evaluated on every change, showing each side's change in
    expected category wins, per-category before/after, games and starts volume, roster-limit
    drops, and market value moving each way.
  - **Suggestions:** comparable-value targets for your selection are listed league-wide, or from
    the chosen partner. **Add** pulls a suggested player into the trade.

macOS has no bare `python` command, and the dependencies live in `.venv`. Either prefix commands
with `.venv/bin/python` as above, or activate the venv once per terminal session
(`source .venv/bin/activate`). After that, `python -m cfl ...` works, as written in the rest of this file.

## Data sources

| Source | What | Auth |
|---|---|---|
| Fantrax public API (`/fxea/general/*`) | league settings, scoring categories, teams, schedule, rosters, standings, weekly matchup category scores, player IDs/eligibility/ADP | none |
| NHL.com stats API (`api.nhle.com/stats/rest`) | per-player season stats: G, A, Pt, +/-, PPP, SOG, Hits, Blocks; goalie W, GA, SA, saves, SO, TOI | none |
| NHL.com schedule (`api-web.nhle.com/v1/schedule`) | every game day of the season, used to project daily lineups | none |

The Fantrax public API has no per-player stats, and its internal stats endpoint needs a login for
private leagues. So player stats come from NHL.com. They're linked to Fantrax IDs by name, with
team and position breaking ties, plus a nickname fallback (Matt/Matthew, Dmitry/Dmitri). The `match`
sync step reports anything it couldn't link.

## Sync

In the app, **Refresh rosters & stats** pulls today's Fantrax rosters and NHL.com stats
(`rosters`, `stats`, `match`, plus `bios` for any new player's birth date). **Full sync…** re-reads every source after a confirmation, and
replaces each team's salary-sheet contracts with what the sheets say now. Neither touches contract
edits made in the app, and neither writes to Fantrax or Google.

```bash
python -m cfl sync                    # everything, in dependency order
python -m cfl sync rosters matchups   # just the fast-changing bits
```

Resources: `league`, `players`, `rosters`, `standings`, `matchups`, `stats`, `schedule`, `match`,
`bios`, `salaries`. `bios` loads birth dates. Season bios reports cover everyone who has played,
and rostered prospects are looked up one by one through NHL.com's player search, which also
handles transliterations like Yegor/Egor. Lookups happen only for players still missing a date. Matchup scores
for completed periods are fetched once and skipped after that. Every run is recorded in `sync_log`.

## Salaries and the cap

The league tracks contracts in a Google Sheet per GM (shared from the CFL Drive folder), not in
Fantrax. List the sheets in `salary_sheets.json` (git-ignored; copy `salary_sheets.example.json`).

- **Keys:** a team id, a unique part of a team name, or `me`.
- **Values:** a sheet URL or a CSV path. A URL without a `gid` reads the first tab. A value can
  also be an object with column hints:

```json
{
  "me": "https://docs.google.com/spreadsheets/d/<id>/edit?gid=0#gid=0",
  "linseman": { "url": "https://docs.google.com/spreadsheets/d/<id>/edit",
                "salary_column": "AE", "years_column": "AF" }
}
```

Sheets must be shared as "anyone with the link can view". `python -m cfl sync salaries` reads them.
A Drive shortcut doesn't work; use the URL of the sheet it points to.

- **Layouts:** every GM's sheet is different, so the parser looks for:
  - **Seasons:** a header cell naming the current season in any common form (`2026/2027`,
    `2026-27`, `26/27`, `26-27`). Salaries are read from that column rightward, including
    unlabeled columns, until a blank or `UFA`/`RFA`.
  - **Salary + years:** no season columns, but salary/price and years-left/length columns.
  - **Hints:** for anything else (e.g. salary/years column pairs per season), set `salary_column`
    and `years_column` as column letters.
- **Rows:** `Last, First` names are flipped. Section labels (`BUYOUTS`/`CAP PUNISHMENT`,
  `Injured Reserve`/`IR GUYS`, `Minors`, `PROSPECTS`) are recognised. Totals and other summary
  rows, unsigned prospects, and duplicate rows are skipped.
- **Name matching:** names are matched to the team's Fantrax roster, fuzzily, because typos like
  "Slavkofsky" are common. Other players match only on an exact, unique name. The sync reports
  every fuzzy match, every unmatched name, and rostered players with no salary on the sheet.
- **Cap rules:** $220 cap (from Fantrax). Active and bench salaries count, plus buyouts; minors and
  IR don't. Cutting a player is free at $3 or less; otherwise it's a buyout of half the salary,
  rounded down.
- **Where it shows up:** salaries and contract years are on every player. The team profile has a
  `cap` summary covering this season and the next four. Trade evaluations show cap space
  before/after for each team that has a sheet, flag incoming players whose salary is unknown, and
  include buyout costs for auto-dropped players.
- **Editing contracts:** the **Players** tab (or a click on any name in the roster table) opens a
  contract editor. It sets the years left on a contract, plus its type and a note; the salary stays
  as the sheet has it. A player on no sheet also needs a salary. Edits are stored locally as
  overrides. They win over the sheets, survive syncs, and can be reverted to the sheet. Contract
  types follow the rules: **ELC** is $3 for at most 3 years, and an **in-season UFA** gets 1 year
  and doesn't use contract-bank years.
  API: `GET/DELETE /players/{id}/contract`, and `PUT` with
  `{"years": 2, "contract_type": "ELC", "note": "..."}` (optional `salary`).
- **Coverage:** the sync reports, per team, rostered players with no salary on the sheet. A high
  count usually means that GM's sheet is out of date.
- **RFA or UFA at expiry:** a player is a **UFA** if he's 27 or older on July 1 after his
  contract's last season, otherwise an **RFA**. In-season UFA pickups are always UFAs. Every player
  shows this as a badge, e.g. "RFA '29", along with his age. The published sheet has an
  **At expiry** column. Birth dates come from NHL.com; enter a missing or wrong one in the player
  editor. Hand-entered dates are never overwritten by a sync.
- **Contract-year bank (50):** seasons left on every contract, this one included, for active and
  bench players. Minors and IR don't count, and in-season UFA deals are exempt. The dashboard
  flags a team over the bank, and trades show contract years before → after.

## Publishing your team to its Google Sheet

For teams marked `"owner": "app"` in `salary_sheets.json` (yours), the app is the source of truth:

- **One-time adoption:** the first `sync salaries` copies that team's sheet contracts into the app.
  After that the sheet is never read again.
- **The sheet becomes a published view.** After every contract edit and every sync, the app
  rewrites the team's `roster` tab: Forwards / Defense / Goalies by Fantrax status, buyouts,
  totals, Injured Reserve, and Minor League. Committed, cap space and contract years are live
  formulas. Nothing is sent when nothing changed.
- **The cap card shows the last publish** and has a **Publish now** button. From the command
  line, run `python -m cfl publish --force`.

The app writes through a small Apps Script attached to the sheet, so no Google Cloud project is
needed. Set it up once:

1. Run `python -m cfl publish --script`. It adds a random `CFL_SHEET_PUBLISH_TOKEN` to `.env` and
   writes `data/sheet_publisher.gs`, the script with your token filled in. It's git-ignored;
   don't share it.
2. In the sheet, open **Extensions > Apps Script** and replace the editor's contents with that
   file. Save.
3. Choose **Deploy > New deployment**, type **Web app**. Set **Execute as: Me** and **Who has
   access: Anyone**. Authorize when asked, then copy the web app URL.
4. Add it to `.env` as `CFL_SHEET_PUBLISH_URL=...` and restart the server. Then click
   **Publish now** (or run `python -m cfl publish --force`).

"Anyone" means anyone with the URL can call the script, but every write must carry your token.
The template is `cfl/sheet_publisher.gs`. After changing it, redeploy with a new version.

## API

Interactive docs are at `/docs`. Anywhere a `team_id` goes, you can use `me` (it reads
`FANTRAX_MY_TEAM_ID`).

| Endpoint | |
|---|---|
| `POST /sync?resources=rosters&resources=matchups` | refresh from Fantrax / NHL.com |
| `GET /league`, `/categories`, `/teams`, `/standings` | league data |
| `GET /teams/{id}/roster` | roster with per-game projections and z-scores |
| `GET /teams/{id}/contracts` | salary-sheet contracts, how each matched, and the cap summary |
| `GET /matchups?period=N` | schedule + category scores for a week |
| `GET /players?status=fa&position=D&sort=value` | search the player pool |
| `GET /players/{fantrax_id}` | one player, including raw NHL season stats |
| `GET /analysis/overview` | every team's projected rank per category, plus actual H2H records |
| `GET /analysis/teams/me/profile` | your category strengths and weaknesses, need weights, valued roster |
| `GET /analysis/teams/me/targets?source=fa` | free agents ranked by upgrade over your weakest player at the position |
| `GET /analysis/teams/me/targets?source=trade&position=G` | players on other teams that fit your needs |
| `GET /analysis/teams/me/targets?from_team=Ruff` | only one team's players (team id or unique part of the name; implies `source=trade`) |
| `GET /analysis/teams/me/targets?offer=Connor` | trade targets of comparable value to one of your players (see below) |
| `GET /analysis/teams/me/trade-partners` | teams whose strengths match your weaknesses, players each way, your trade chips |
| `GET /publish`, `POST /publish?force=true` | sheet publishing status; publish app-owned teams now |
| `POST /analysis/trade` | cost/benefit of any trade, e.g. `{"give": [ids], "other_team_id": "Ruff", "get": [ids]}`; omit the partner to price losing players |

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
3. **Team profiles.** Lineups are set daily, so the projection walks every remaining game day
   of the NHL schedule. Each day it fills the 2C/2LW/2RW/6F/6D/2G slots with the best non-minors,
   non-IR players whose NHL team plays that night. Depth players (a third goalie, a 13th forward)
   get credit for the nights the regulars sit. A goalie in the lineup is credited in proportion
   to how often he starts for his NHL team. Totals are converted to per-week figures and ranked
   across the league. The team profile shows each player's `lineup_games_per_week`, plus
   `expected_starts_per_week` for goalies. The actual season-to-date category W-L-T comes from
   Fantrax matchup scores.
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
  api.py        FastAPI app (also serves the web app)
  web/          dashboard + trade builder (HTML/CSS/JS, no build)
  watch.py      day-to-day change tracking: IR moves, roster changes, category trends
  __main__.py   CLI (sync / serve / publish / teams / profile / targets / watch)
tests/          offline unit tests (pytest)
data/           league.db (git-ignored)
```

CLI shortcuts for quick looks without the server:

```bash
python -m cfl profile                  # your team (or pass a team id / part of a name)
python -m cfl targets --position D
python -m cfl targets --source trade --punt Hit
python -m cfl targets --from-team Ruff --position D
python -m cfl targets --offer "Kyle Connor" --tolerance 0.15
```

### Watching your team (`watch`)

`python -m cfl watch` compares your team with the last check (saved in `data/watch_state.json`)
and prints what needs a roster move, plus roster changes and category ranks. Run it after a sync,
for example once a day. Add `--json` for machine-readable output.

- **Activate:** a player in your IR slot who played an NHL game since the last check.
- **Possibly hurt:** an active or bench skater whose NHL team played 2+ games in a row without
  him (5 for goalies, who sit regularly). There's no injury feed, so this is inferred from games
  played: check the news before moving him to IR.
- **Trends:** projected category rank moves since the last check. The last 30 checks are kept
  in the state file for longer trends.

### Like-for-like trades (`offer`)

Pass `offer` (one or more players on your roster, comma-separated ids or unique parts of names)
and targets only returns players of similar market value, ranked by how much the swap improves
*your* category needs.

- **Market value** is `trade_rank`, shown on every player. Skaters and goalies are each ranked
  by equal-weight value, and goalie ranks are scaled by skater slots ÷ goalie slots (18 ÷ 2), so
  the #1 goalie sits level with the #9 skater.
- **Comparable** means a trade rank within ±`tolerance` of the offered player's (default 0.25:
  #40 → #32–#50), never narrower than ±3 ranks.
- **Packages** use `market_value`, which is additive: value over a replacement-level starter,
  so depth players are worth ~0 and two of them never add up to a star. A package is compared
  as the single player with the same summed value ("≈ #N").
- **Roster limit:** a team pushed over the 25-player limit (active plus bench) drops its weakest
  player in the simulation, and the result lists who.
- Each 1-for-1 swap is **simulated**: both teams re-run their daily lineups with the new rosters.
  **`your_gain`** and **`their_gain`** are the change in *expected category wins per week* (out of
  12) against the rest of the league. This counts depth properly: a bench skater who fills off
  nights has real value, and a 3rd goalie still starts when the top two are idle. `win_win` flags
  deals where both gains are positive, which are the ones most likely to get accepted.
- Combine with `from_team` to ask "what could I get from this team for X?"

