# CFL Manager

A local data store and analysis API for the **Competelevel Fantasy League** on Fantrax: an 11-team
dynasty league with an auction draft and H2H "each category" scoring.

It pulls league structure, rosters, schedules and weekly category results from Fantrax, and player
stats from NHL.com. Everything goes into a single SQLite file, or a Postgres database when the app is
deployed (see [Deploying to Vercel](#deploying-to-vercel)). The analysis layer projects every
player, profiles every team by category, and ranks waiver and trade targets by what *your* team needs.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # then set FANTRAX_MY_TEAM_ID (your team, for the CLI and local use)
.venv/bin/python -m cfl sync    # ~2s, writes data/league.db
.venv/bin/python -m cfl teams   # find your team id for .env
(cd frontend && npm ci && npm run build)   # the web app, into cfl/web/dist (Node 24)
.venv/bin/python -m cfl serve   # app at http://127.0.0.1:8000, API docs at /docs
```

## Web app

`python -m cfl serve` (add `--reload` while developing) serves the app at http://127.0.0.1:8000.
The page's source is in `frontend/`. Vite builds it into `cfl/web/dist/`, which FastAPI serves.
Without a build, the API and the CLI work, but `/` returns an error.

To work on the page, run the API and Vite's dev server side by side:

```bash
.venv/bin/python -m cfl serve --reload   # the API, on port 8000
cd frontend && npm run dev               # the page at http://localhost:5173, updated as you edit
```

Vite sends the API's paths to port 8000. It reads them from `frontend/openapi.json`, so after you
change an endpoint, run `npm run api` in `frontend/`. It also regenerates the typed client,
`src/api/generated.ts`. A test fails until you do.

Before a PR, run `npm run check` in `frontend/` (Biome, TypeScript, then Vitest) and `pytest`.

- **Dashboard.** A GM's overview of a team, from real results only (no projections):
  - **At a glance:** this week's category score and opponent, standings, and roster spots.
  - **Categories:** this week's head-to-head in each category (your total, the opponent's, won or
    lost), next to the season's record and league rank in that category. Some weeks bring two
    opponents, or a bye.
  - **Standings** from Fantrax. Click a team to see its dashboard.
  - **Roster** by position: age, this NHL season's games and totals in each league category,
    salary, years left, and RFA or UFA at expiry. Click a column to sort.

  The team picker in the header lets you view any team. Projections and market values are on the
  Analysis tab.
- **Analysis.** The projections behind the trade builder, for the team in the header's picker:
  - **Tiles:** expected category wins per week against an average opponent, projected strength
    across the league, and skater games and goalie starts per week from projected daily lineups.
  - **Category profile:** projected strength in each category against the league average, with
    projected per-week totals, rank, actual W-L-T and need weight.
  - **League at a glance:** a heatmap of every team's projected rank in every category.
  - **Player values:** each player's value, need value, trade rank and market value, games per week
    and best categories.

  **Need weighting** sets how the team's needs are weighed for the need weights and need values (see
  [How the analysis works](#how-the-analysis-works)).
- **Players & cap.** Search the league or browse a team, filter to players missing a salary, and edit
  any player's contract. A GM can edit only their own team's players (see [Logins](#logins)).
  Each contract shows its salary for every season it runs, then RFA or UFA in the season after it ends.
  A team's page opens with its cap space, contract years used and the salary cap for this season and
  the next four, which follow every contract edit. It also shows the team's [draft picks](#draft-picks),
  where its GM records pick trades and RFA compensation.
- **Changes.** Roster moves and contract changes, newest first: players added, dropped or
  traded (from Fantrax syncs), salary changes, other contract changes (years, later
  seasons, type), and draft picks changing hands. Filter by team or kind. The **By** column says where each came from:
  `Sync`, `Contracts sheet`, or the name of the person who edited the contract. History
  starts with the first sync or edit after this view shipped. Lineup status moves (bench,
  IR, minors) and contract notes aren't logged. Neither is the first sync of a new season, since
  every contract is a season shorter by then. That sync only saves a new starting point, so a
  roster move it picks up isn't logged either.
- **Trade builder** (admin only). Select any of your players. Optionally pick a trade partner and
  their players.
  - **Live evaluation:** the trade is re-evaluated on every change, showing each side's change in
    expected category wins, per-category before/after, games and starts volume, roster-limit
    drops, and market value moving each way.
  - **Suggestions:** comparable-value targets for your selection are listed league-wide, or from
    the chosen partner. **Add** pulls a suggested player into the trade.
  - **Waiver wire:** pick it as the partner to compare free agents with your roster. Your
    selection becomes the players you'd drop. Suggestions rank free agents by simulated gain,
    with the best drop for each when you haven't chosen one. Search the free agents to add any of
    them, and the evaluation prices the pickup like a trade, including buyouts for the drop.
- **Offseason** (admin only). Every rostered player whose contract ends after a season, grouped
  into RFAs, UFAs, and players with no birth date (status unknown; click one to add it). Shows each
  player's age on July 1, contract type, final-season salary and value. Filter by team or status.
  It defaults to the next offseason, the summer after the current season. The picker also
  looks ahead to each later season a contract can run. Everything follows the league's current
  season, so the list moves on by itself once Fantrax starts a new season. A note under the table
  lists rostered players with no contract on file; the list can't place them.
- **Admin** (admin only). Approve GMs who signed in, link each to their team, change roles, and
  remove access.
- **Account.** Your name in the top right opens it: your team and role, **Sign out**, and the theme
  (match the device, light or dark). The theme is saved in the browser, so each device keeps its own.

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

In the app, **Refresh rosters & stats** pulls today's Fantrax rosters, standings and matchup scores,
and NHL.com stats (`rosters`, `standings`, `matchups`, `stats`, `match`, plus `bios` for any new
player's birth date). **Full sync…** re-reads every source after a confirmation, and
replaces each team's salary-sheet contracts with what the sheets say now. Neither touches contract
edits made in the app, and neither writes to Fantrax or Google.

**Refresh rosters & stats** runs at most once every 10 minutes for the whole league, admins included.
The button shows when it's available again. **Full sync…** has no limit.

```bash
python -m cfl sync                    # everything, in dependency order
python -m cfl sync rosters matchups   # just the fast-changing bits
```

Resources: `league`, `players`, `rosters`, `standings`, `matchups`, `stats`, `schedule`, `match`,
`bios`, `salaries`. `bios` loads birth dates. Season bios reports cover everyone who has played,
and rostered prospects are looked up one by one through NHL.com's player search, which also
handles transliterations like Yegor/Egor. Lookups happen only for players still missing a date. Matchup scores
for completed periods are fetched once and skipped after that. Every run is recorded in `sync_log`.
Every run ends with a `changes` step that logs what changed for the **Changes** view.

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
- **Editing contracts:** the **Players & cap** tab (or a click on any name in the roster table) opens a
  contract editor. It sets the years left on a contract, plus its type and a note; the salary stays
  as the sheet has it. A player on no sheet also needs a salary. Edits are stored locally as
  overrides. They win over the sheets, survive syncs, and can be reverted to the sheet. Each edit is
  tied to the season it was saved in: when Fantrax moves the league to a new season, the seasons
  that have passed drop off, so a 2-year deal saved in 2026-27 has 1 year left in 2027-28. Once
  every season has passed the edit has run out, and the sheet's contract (if any) applies again.
  The same goes for imports from the Contracts sheet and for the contracts of an app-owned team.
  Contract types follow the rules: **ELC** is $3 for at most 3 years, and an **in-season UFA** gets 1 year
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
- **RFA holds:** an RFA's salary doesn't drop to $0 after his contract ends. In every later
  season of the cap outlook he's carried at his final-season salary, as a placeholder. Holds
  follow the same rules as other future seasons: IR counts, minors don't. Players with no birth
  date on file aren't included, because their status is unknown. Holds stay out of `committed` and
  `space`. Each future season reports them as `rfa_holds` (the total) and `rfa_players` (the
  players). The cap card on the Players & cap tab draws them as a dotted, unfilled segment after the committed
  salary, and its tooltip lists the players. The published sheet doesn't add them.
- **Contract-year bank (50):** seasons left on every contract, this one included, for active and
  bench players. Minors and IR don't count, and in-season UFA deals are exempt. The Players & cap tab
  flags a team over the bank, and trades show contract years before → after.

### Checking against the league-wide contract sheet

A GM keeps a "CFL Contracts" sheet with every player in the league on one tab: Player, Team, Pos,
Owner, Birthday, Type, and a column per season (`26-27`, `27-28`, ...). It must be shared as
"anyone with the link can view".

```bash
python -m cfl reconcile-contracts "<sheet URL>" --csv data/contract-review.csv            # dry run
python -m cfl reconcile-contracts "<sheet URL>" --apply                                  # save the imports
```

- **Matching:** rows match Fantrax players by name. When several players share a name, the
  birthday, the owner's team and the position decide. Without an exact name, a player with the
  same birthday and a similar name matches (Matt/Matthew, Yegor/Egor), and then a fuzzy match on
  the owner's roster. Each sheet owner maps to the Fantrax team that rosters most of their players.
- **Import:** a rostered player with no contract in the app gets the sheet's: its salaries, plus
  its type when that's ELC or in-season UFA. It's saved like a contract edit, with a note that starts
  "From the CFL Contracts sheet", so it survives syncs and wins over the GM's salary sheet. A
  salary with no posted length is saved as one season, and its note says so.
- **Review:** nothing else changes. The report (and the `--csv` file) lists contracts where the
  app and the sheet disagree on salary, years or later seasons, or where the sheet has no contract. It
  also lists players the two sources put on different teams, same-salary contracts where only the
  sheet records the type, unmatched sheet rows, and rostered players with no contract anywhere.

## Draft picks

Every team gets 5 picks in each draft. Picks change hands in trades, and as compensation when a team
signs another team's RFA. Fantrax doesn't track them for this league, so the app does.

- **Which drafts:** the next three, starting with the one after this season (2027, 2028 and 2029
  in 2026-27). When the season rolls over, the oldest drops off and a new one appears. Nothing
  needs setting up: a pick no deal has moved still belongs to its original team.
- **On the Players & cap tab:** a team's page shows a grid of drafts by round. Each cell lists the
  picks the team owns: `Own`, or the name of the team the pick came from. Its own picks that went
  elsewhere show dashed, as `→ Team`, and don't count. Hover a pick for how it moved, when, and who
  recorded it. Below the grid, **Pick deals** lists every deal the team was part of.
- **Recording a deal:** **Trade or send picks…** on your own team's page. A trade can move picks
  both ways. RFA compensation goes one way: your team signed another team's RFA and sends it the
  picks. A GM sends only their own team's picks, so in a trade each GM records the picks they send.
  The admin can record both sides at once, for any team.
- **Undo:** a deal recorded by mistake can be undone by whoever could have sent its picks, until one
  of them moves again.
- Every pick that moves shows on the **Changes** tab, under **Draft picks**.

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

## Deploying to Vercel

The app runs on Vercel's free Hobby plan with a free Neon Postgres database. Vercel keeps no
files between requests, so a deployed app can't use `data/league.db`. When `DATABASE_URL` is set,
the app and the CLI use that Postgres database instead. Nothing else changes.

1. **Create the project.** In Vercel, choose **Add New > Project** and import this repository. No
   settings are needed: Vercel loads the app from `app.py`, installs `requirements.txt` and uses
   Python 3.12 (from `.python-version`). `vercel.json` then builds the web app in `frontend/` with
   Node. The site won't work until it has a database.
2. **Add the database.** In the project's **Storage** tab, create a **Neon** database on the free
   plan, in AWS `us-east-1` (next to Vercel's default region, Washington, D.C.). Connecting it to
   the project adds `DATABASE_URL`.
3. **Copy your data.** Put the same `DATABASE_URL` in your local `.env` (copy it from the
   project's **Environment Variables**), then run `python -m cfl import-sqlite`. It copies
   everything in `data/league.db`, contract edits and hand-entered birth dates included. It won't
   overwrite a database that already has data unless you add `--replace`.
4. **Set the other variables** under **Settings > Environment Variables**, then redeploy:
   - `FANTRAX_LEAGUE_ID`, if it isn't the default. Leave out `FANTRAX_MY_TEAM_ID`: on the site,
     everyone's team, yours included, is the one linked in the Admin tab.
   - `CFL_SALARY_SHEETS_JSON`: the contents of `salary_sheets.json`, which isn't in the repository.
     Key your own sheet by its team name rather than `me`, which only works with
     `FANTRAX_MY_TEAM_ID`.
   - `CFL_SHEET_PUBLISH_URL` and `CFL_SHEET_PUBLISH_TOKEN`, if you publish to your sheet.
   - `GOOGLE_CLIENT_ID`, `CFL_SESSION_SECRET` and `CFL_ADMIN_EMAILS`, for [logins](#logins). A
     deployment without them refuses every request, so the data is never left open.
5. **Choose who can reach it.** Under **Settings > Deployment Protection**, **Vercel
   Authentication** for **All Deployments** (free on Hobby) lets only your Vercel account open the
   site. Keep that on until logins work. Then switch it to **Standard Protection**, which still
   guards preview deployments, so GMs can reach the production site and sign in with Google.

With `DATABASE_URL` in `.env`, local commands (`sync`, `serve`, `watch`, ...) read and write the
Neon database too, so the site and your terminal agree. Remove it to go back to `data/league.db`.

Free-plan limits: Vercel Hobby includes 4 CPU-hours of function time a month, and a request can
run for 5 minutes (a full sync takes about 10 seconds). Neon's free plan sleeps the database after
5 idle minutes, so the first request after a break takes a moment longer.

## Logins

GMs sign in with Google. Anyone can ask to join; you approve them and link their team.

- **Who sees what.** A GM sees every team's dashboard, analysis and players, and edits contracts and birth
  dates for players on their own team. They can run **Refresh rosters & stats**. Admins also get
  full syncs, sheet publishing, the trade builder, the **Offseason** list and the **Admin** tab. The API enforces all of
  this, not just the page.
- **Joining.** Send GMs the site's link. When someone signs in for the first time they wait for
  approval and show up under **Requests** in the Admin tab, where you pick their team. You can
  also add someone's Google email and team ahead of time, so they get straight in.
- **Admins.** Emails in `CFL_ADMIN_EMAILS` are always admins, so you can't lock yourself out. After
  your first sign-in, pick your own team on your row in the Admin tab: teams come from there, not
  from `FANTRAX_MY_TEAM_ID`. You can make other people admins in the Admin tab too.
- **Sessions.** Signing in sets a cookie that lasts 30 days. Every request checks the `users`
  table, so a new team, role or removal applies on the next click.
- **Running locally.** Without `GOOGLE_CLIENT_ID` there are no logins: the app and CLI act as the
  admin, as before. Vercel refuses that setup.

Set it up once:

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project. Under
   **Google Auth Platform**, set **Audience** to **External** and fill in **Branding**: app name,
   support email, developer contact email, and under **App domain** the home page
   `https://clfl.vercel.app`, the privacy policy `https://clfl.vercel.app/privacy` (served by the
   app) and the authorized domain `clfl.vercel.app`. Skip the logo, which needs a Google review.
   Then on **Audience**, click **Publish app**: while it's in **Testing**, only the test users you
   list can sign in. The app asks only for email and profile, which need no review.
2. Under **Clients**, choose **Create client** with type **Web application**. Add the site to
   **Authorized JavaScript origins**: `https://clfl.vercel.app` (or your domain), and to
   **Authorized redirect URIs**: `https://clfl.vercel.app/auth/google`. Google sends people back
   there after they pick an account (a full-page redirect, not a popup, which some browsers block).
   To use logins locally, also add the origins `http://localhost` and `http://localhost:8000` and
   the redirect URI `http://localhost:8000/auth/google`.
3. In Vercel, set `GOOGLE_CLIENT_ID` to the client ID, `CFL_SESSION_SECRET` to a long random
   string (for example `openssl rand -base64 32`; keep it a Secret), and `CFL_ADMIN_EMAILS` to your
   Google email. Redeploy.
4. Sign in yourself, then try a second Google account: it should wait for approval, and once you
   approve it, see only what a GM sees. Then switch Deployment Protection to Standard (step 5
   above) and send GMs the link.

## API

Interactive docs are at `/docs`. Every endpoint below needs a signed-in, approved user when logins
are on; those marked *admin* need an admin. Anywhere a `team_id` goes, you can use `me`: the
signed-in user's team (locally, `FANTRAX_MY_TEAM_ID`).

| Endpoint | |
|---|---|
| `POST /sync?resources=rosters&resources=matchups` | refresh from Fantrax / NHL.com (a GM: only `rosters`, `standings`, `matchups`, `stats`, `match`, `bios`; those run at most once every 10 minutes league-wide, 429 otherwise) |
| `GET /league`, `/categories`, `/teams`, `/standings` | league data |
| `GET /teams/{id}/overview` | the dashboard: this week's matchup by category, season category records and ranks, the roster with this NHL season's stats, and roster spots |
| `GET /teams/{id}/roster` | roster with per-game projections and z-scores |
| `GET /teams/{id}/contracts` | salary-sheet contracts, how each matched, and the cap summary |
| `GET /matchups?period=N` | schedule + category scores for a week |
| `GET /players?status=fa&position=D&sort=value` | search the player pool (with `team_id`, also that team's `cap` summary) |
| `GET /players/{fantrax_id}` | one player, including raw NHL season stats |
| `GET /changes?team_id=me&kind=salary` | player and contract changes, newest first (`kind`: added, dropped, traded, salary, contract, pick; repeat for several) |
| `GET /teams/{id}/picks` | the team's picks in the upcoming drafts, its own picks now elsewhere, and its pick deals |
| `POST /picks/deals` | move picks: `{"kind": "trade", "team_id": "me", "other_team_id": "...", "send": [{"draft_year": 2027, "round": 1, "original_team_id": "..."}], "receive": [...], "note": "..."}` (`kind`: trade or rfa; `receive` is for trades) |
| `DELETE /picks/deals/{deal_id}` | undo a pick deal |
| `GET /offseason?year=2027` | *admin*: rostered players whose contracts end in that offseason (July 1 of `year`), RFAs then UFAs then unknown, plus rostered players with no contract on file. `year` defaults to the next offseason |
| `GET /analysis/overview` | every team's projected rank per category, plus actual H2H records |
| `GET /analysis/teams/me/profile` | your category strengths and weaknesses, need weights, valued roster |
| `GET /analysis/teams/me/targets?source=fa` | *admin* (as are all `targets` queries): free agents ranked by simulated gain, each with its best drop (see below) |
| `GET /analysis/teams/me/targets?source=fa&drop=Evans` | the same, with the player you'd release fixed |
| `GET /analysis/teams/me/targets?source=trade&position=G` | players on other teams that fit your needs |
| `GET /analysis/teams/me/targets?from_team=Ruff` | only one team's players (team id or unique part of the name; implies `source=trade`) |
| `GET /analysis/teams/me/targets?offer=Connor` | trade targets of comparable value to one of your players (see below) |
| `GET /analysis/teams/me/trade-partners` | *admin*: teams whose strengths match your weaknesses, players each way, your trade chips |
| `GET /publish`, `POST /publish?force=true` | *admin*: sheet publishing status; publish app-owned teams now |
| `POST /analysis/trade` | *admin*: cost/benefit of any trade, e.g. `{"give": [ids], "other_team_id": "Ruff", "get": [ids]}`; omit the partner for a waiver move (`give` released, `get` free agents) or to price losing players |
| `GET /auth/config`, `GET /auth/me` | whether logins are on (and the Google client ID); who's signed in |
| `POST /auth/google`, `POST /auth/logout` | sign in with a Google ID token `{"credential": "..."}`; sign out |
| `GET /admin/users`, `POST /admin/users`, `PUT` / `DELETE /admin/users/{email}` | *admin*: list who has access; add, approve or link someone (`{"team_id": "...", "role": "gm", "status": "active"}`); remove them |

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
  db.py         schema + helpers, for SQLite or Postgres (DATABASE_URL)
  sync.py       ingest + Fantrax↔NHL id matching
  analysis.py   projections, z-scores, team profiles, targets, trade partners
  api.py        FastAPI app (also serves the web app)
  auth.py       Sign in with Google, sessions, who may do what
  web/dist/     the built web app (git-ignored)
  watch.py      day-to-day change tracking: IR moves, roster changes, category trends
  reconcile.py  compare a league-wide contract sheet with the app; import missing contracts
  changes.py    change log: compares every player's team and contract after each sync and edit
  picks.py      draft picks: who owns each one, and the trades and RFA compensation that moved them
  offseason.py  contracts ending in each offseason, and whether each player becomes an RFA or a UFA
  fsm.py        state machines for the offseason: checked, logged moves that two people can't both win
  __main__.py   CLI (sync / import-sqlite / serve / publish / reconcile-contracts / teams / profile / targets / watch)
app.py          Vercel entrypoint
frontend/       the web app's source (HTML, CSS, JS), built with Vite
tests/          offline unit tests (pytest)
data/           league.db (git-ignored)
```

`pytest` runs the tests on SQLite. To run them on Postgres, set `CFL_TEST_DATABASE_URL` to a
scratch database. It's wiped before every test.

CLI shortcuts for quick looks without the server:

```bash
python -m cfl profile                  # your team (or pass a team id / part of a name)
python -m cfl targets --position D
python -m cfl targets --source trade --punt Hit
python -m cfl targets --from-team Ruff --position D
python -m cfl targets --offer "Kyle Connor" --tolerance 0.15
python -m cfl targets --position G --drop "Evans"
```

### Watching your team (`watch`)

`python -m cfl watch` compares your team with the last check (saved in `data/watch_state.json`)
and prints what needs a roster move, plus roster changes and category ranks. Run it after a sync,
for example once a day. Add `--json` for machine-readable output.

- **Lineup:** flags today's lineup when bench players with a game could fill more starts than
  your active players, and says whom to start and sit. Run it before the day's games lock.
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

### Waiver pickups (`source=fa`)

Free agents are scored the same way as trades: each pickup is simulated with your daily lineups,
and **`your_gain`** is the change in expected category wins per week.

- **The drop.** A pickup joins your bench, so on a full roster someone has to go. Pass `drop`
  (ids or unique parts of names) to fix who. Otherwise each pickup tries your three lowest-value
  players and your two lowest at his position, and reports the best in **`drops`**. With an open
  roster spot, nobody is dropped.
- **Market value** works as for trades. `market_value_net` is the pickup's market value minus the
  drop's. A negative number means the drop might fetch more in a trade than on waivers.
- **Buyouts.** Each drop shows its `release_cost`: free at $3 or less, otherwise half the salary.
- **Not modelled:** dynasty value. Market value measures this season's production only, so check
  a prospect's age and contract before dropping him.
- **Waivers vs. free agents:** Fantrax's public data marks a player as rostered or not, with no
  waiver status. Every unrostered player is treated as available.
- Only the best free agents by need value at each position (F, D, G) are simulated, so a query
  takes about half a second.

