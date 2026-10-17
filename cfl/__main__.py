"""CLI: python -m cfl {sync,import-sqlite,serve,publish,reconcile-contracts,teams,profile,targets,watch}"""

import argparse
import json
import os
import sys
from typing import List, Optional

from . import analysis as A
from .config import get_settings
from .db import connect
from .sync import RESOURCES, sync_all


def _team_arg(ctx: A.Context, value: Optional[str]) -> str:
    settings = get_settings()
    team = value or settings.my_team_id
    if not team:
        sys.exit("No team given and FANTRAX_MY_TEAM_ID is not set. Run `python -m cfl teams` to find your id.")
    resolved = ctx.resolve_team(team)
    if resolved is None:
        several = [n for n in ctx.teams.values() if team.lower() in n.lower()]
        if several:
            sys.exit("'{}' matches several teams: {}. Be more specific.".format(team, ", ".join(several)))
        sys.exit("Unknown team '{}'. Run `python -m cfl teams`.".format(team))
    return resolved


def cmd_sync(args) -> None:
    settings = get_settings()
    conn = connect(settings.database)
    try:
        results = sync_all(conn, settings, args.resources or None)
    except ValueError as exc:
        sys.exit(str(exc))
    ok = True
    for r in results:
        ok &= r["status"] == "ok"
        detail = r["detail"] if r["status"] == "ok" else r["detail"]["error"]
        print("{:<10} {:<6} {}".format(r["resource"], r["status"], json.dumps(detail)))
    print("db:", settings.database_label)
    if not ok:
        sys.exit(1)


def cmd_import_sqlite(args) -> None:
    from pathlib import Path

    from .db import TABLES, copy_database, is_postgres

    settings = get_settings()
    if not is_postgres(settings.database_url):
        sys.exit("Set DATABASE_URL (in .env or the environment) to the Postgres database to copy into.")
    source = Path(args.path) if args.path else settings.db_path
    if not source.exists():
        sys.exit("No SQLite database at {}".format(source))
    src, dst = connect(source), connect(settings.database_url)
    if not args.replace and any(dst.execute("SELECT 1 FROM {} LIMIT 1".format(t)).fetchone() for t in TABLES):
        sys.exit("{} already has data. Run again with --replace to overwrite all of it with {}.".format(
            settings.database_label, source))
    for table, n in copy_database(src, dst).items():
        print("{:<24} {:>7}".format(table, n))
    print("Copied {} into {}".format(source, settings.database_label))


def cmd_publish(args) -> None:
    import secrets

    from . import publish as P
    from .config import ROOT
    from .sync import _logged

    settings = get_settings()
    if args.script:
        if not settings.sheet_publish_token:
            env = ROOT / ".env"
            with open(env, "a") as f:
                f.write("\n# Shared secret for the sheet publisher (cfl/sheet_publisher.gs)\n"
                        "CFL_SHEET_PUBLISH_TOKEN={}\n".format(secrets.token_urlsafe(24)))
            os.environ.pop("CFL_SHEET_PUBLISH_TOKEN", None)
            settings = get_settings()
            print("Added CFL_SHEET_PUBLISH_TOKEN to .env")
        print("Apps Script written to {} (it contains your token; don't commit or share it).".format(
            P.write_script(settings)))
        print("Paste it into your sheet: Extensions > Apps Script. Then Deploy > New deployment > Web app,\n"
              "Execute as: Me, Who has access: Anyone. Put the web app URL in .env as CFL_SHEET_PUBLISH_URL.")
        return
    conn = connect(settings.database)
    result = _logged(conn, "publish", lambda: P.publish(conn, settings, force=args.force))
    print(json.dumps(result, indent=2))
    if result["status"] != "ok":
        sys.exit(1)


def cmd_reconcile_contracts(args) -> None:
    import csv
    import io

    from . import changes
    from . import publish as P
    from . import reconcile as R
    from .clients import fetch_sheet_csv
    from .sync import _logged

    settings = get_settings()
    conn = connect(settings.database)
    ctx = A.load_context(conn, settings)
    try:
        report = R.reconcile(conn, ctx, list(csv.reader(io.StringIO(fetch_sheet_csv(args.sheet)))))
    except (ValueError, RuntimeError) as exc:
        sys.exit(str(exc))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(R.format_report(report))
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=R.CSV_COLUMNS)
            w.writeheader()
            w.writerows(R.csv_rows(report))
        print("\nWrote the players to review to", args.csv)
    if not args.apply:
        print("\nDry run: nothing saved. Add --apply to save the {} imports to {}.".format(
            len(report["imports"]), settings.database_label))
        return
    n = R.import_contracts(conn, settings.league_id, report["imports"])
    print("\nSaved {} contracts to {}.".format(n, settings.database_label))
    print("changes:", json.dumps(_logged(conn, "changes", lambda: changes.record(
        conn, A.load_context(conn, settings), "Contracts sheet"))["detail"]))
    if P.publish_status(conn, settings)["configured"]:
        print("publish:", json.dumps(_logged(conn, "publish", lambda: P.publish(conn, settings))["detail"]))


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("cfl.api:app", host=args.host, port=args.port, reload=args.reload)


def cmd_teams(_args) -> None:
    settings = get_settings()
    ctx = A.load_context(connect(settings.database), settings)
    for t, name in ctx.teams.items():
        print("{}  {}{}".format(t, name, "   <- me" if t == settings.my_team_id else ""))


def cmd_profile(args) -> None:
    settings = get_settings()
    conn = connect(settings.database)
    ctx = A.load_context(conn, settings)
    team = _team_arg(ctx, args.team)
    prof = A.team_profile(conn, ctx, team, strategy=args.strategy, punt=args.punt or [])
    print("{} — projected overall rank {}/{} (total z {:+.2f})".format(
        prof["team_name"], prof["projected_overall_rank"], len(ctx.teams), prof["projected_total_z"]))
    print("{:<5} {:>9} {:>6} {:>7}  {:>8} {:>6} {:>8}  {:>6}".format(
        "cat", "proj/wk", "rank", "z", "actual", "rank", "W-L-T", "need"))
    for c in prof["categories"]:
        print("{:<5} {:>9} {:>6} {:>+7.2f}  {:>8} {:>6} {:>8}  {:>6.2f}".format(
            c["category"], _fmt(c["projected_per_week"]), _fmt(c["projected_rank"]), c["projected_z"] or 0,
            _fmt(c["actual_value"]), _fmt(c["actual_rank"]), c["actual_record"], c["need_weight"]))
    print("strengths:", ", ".join(prof["strengths"]), "| weaknesses:", ", ".join(prof["weaknesses"]))


def cmd_targets(args) -> None:
    settings = get_settings()
    conn = connect(settings.database)
    ctx = A.load_context(conn, settings)
    team = _team_arg(ctx, args.team)
    from_team = _team_arg(ctx, args.from_team) if args.from_team else None
    offer = drop = None
    if args.offer:
        offer = ctx.resolve_player(args.offer, team)
        if offer is None:
            sys.exit("No single player on {} matches '{}'.".format(ctx.teams[team], args.offer))
    if args.drop:
        drop = ctx.resolve_player(args.drop, team)
        if drop is None:
            sys.exit("No single player on {} matches '{}'.".format(ctx.teams[team], args.drop))
    source = args.source or ("trade" if from_team or offer else "fa")
    try:
        res = A.targets(conn, ctx, team, source=source, position=args.position, limit=args.limit,
                        min_games=args.min_games, strategy=args.strategy, punt=args.punt or [], from_team=from_team,
                        offer=offer, tolerance=args.tolerance, drop=drop)
    except ValueError as exc:
        sys.exit(str(exc))
    print("Need weights:", ", ".join("{} {:.2f}".format(c, w) for c, w in res["need_weights"].items()))
    if res["offer"]:
        o = res["offer"]
        print("Offering {} (trade rank #{}, need value {:.2f}) -> comparable ranks #{}-#{}".format(
            o["name"], o["trade_rank"], o["need_value"], *o["comparable_trade_ranks"]))
    if res["drop"]:
        print("Dropping {} for each pickup".format(res["drop"]["name"]))
    for i, p in enumerate(res["targets"], 1):
        top = sorted(p["z"].items(), key=lambda kv: kv[1], reverse=True)[:3]
        extra = ""
        if res["offer"]:
            extra = "  #{:<4} you {:+.2f} / them {:+.2f}{}  ({})".format(
                p["trade_rank"], p["your_gain"], p["their_gain"], "  WIN-WIN" if p["win_win"] else "",
                p["fantasy_team"])
        elif source == "fa":
            extra = "  you {:+.2f}  drop {}".format(
                p["your_gain"], ", ".join(d["name"] for d in p["drops"]) or "nobody (open roster spot)")
        elif p.get("fantasy_team"):
            extra = "  ({})".format(p["fantasy_team"])
        print("{:>2}. {:<24} {:<4} {:<10} need {:>6.2f}  gp {:>2}/{:<2}  best: {}{}".format(
            i, p["name"], p["nhl_team"] or "", ",".join(e for e in p["eligible"] if e != "F"), p["need_value"],
            p["gp_this_season"], p["gp_last_season"], ", ".join("{} {:+.1f}".format(c, z) for c, z in top), extra))


def cmd_watch(args) -> None:
    from pathlib import Path

    from . import watch as W
    from .config import ROOT

    settings = get_settings()
    conn = connect(settings.database)
    ctx = A.load_context(conn, settings)
    team = _team_arg(ctx, args.team)
    result = W.run(conn, ctx, team, Path(args.state or ROOT / "data" / "watch_state.json"))
    print(json.dumps(result, indent=1) if args.json else W.render(result))


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return "{:.3f}".format(v) if abs(v) < 1 else "{:.1f}".format(v)
    return str(v)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m cfl")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync", help="Pull league + stats data into the local store")
    p.add_argument("resources", nargs="*", metavar="RESOURCE",
                   help="Subset to sync (default all): " + ", ".join(RESOURCES))
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("import-sqlite", help="Copy the SQLite store into the DATABASE_URL Postgres database")
    p.add_argument("path", nargs="?", help="SQLite file (default: CFL_DB_PATH, data/league.db)")
    p.add_argument("--replace", action="store_true", help="Overwrite a Postgres database that already has data")
    p.set_defaults(func=cmd_import_sqlite)

    p = sub.add_parser("serve", help="Run the HTTP API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("publish", help="Write app-owned teams' contracts to their Google Sheets")
    p.add_argument("--force", action="store_true", help="Write even if nothing changed")
    p.add_argument("--script", action="store_true", help="Write the Apps Script (with your token) to set up publishing")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("reconcile-contracts",
                       help="Compare a league-wide contract sheet with the app; import contracts the app lacks")
    p.add_argument("sheet", help="Google Sheet URL (its contracts tab) or a CSV file")
    p.add_argument("--apply", action="store_true", help="Save the imports (default: dry run)")
    p.add_argument("--csv", help="Also write the players to review to this CSV file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_reconcile_contracts)

    p = sub.add_parser("teams", help="List team ids")
    p.set_defaults(func=cmd_teams)

    p = sub.add_parser("watch", help="Compare your team with the last check: IR moves, roster and category changes")
    p.add_argument("team", nargs="?", help="Team id or part of the name (default: FANTRAX_MY_TEAM_ID)")
    p.add_argument("--state", help="Snapshot file (default data/watch_state.json)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_watch)

    for name, func in (("profile", cmd_profile), ("targets", cmd_targets)):
        p = sub.add_parser(name)
        p.add_argument("team", nargs="?", help="Team id or part of the name (default: FANTRAX_MY_TEAM_ID)")
        p.add_argument("--strategy", default="marginal", choices=["marginal", "weakness", "even"])
        p.add_argument("--punt", nargs="*", help="Categories to ignore, e.g. --punt Hit +/-")
        if name == "targets":
            p.add_argument("--source", choices=["fa", "trade"], help="default: trade with --from-team, else fa")
            p.add_argument("--from-team", help="Only players on this team (id or part of the name)")
            p.add_argument("--offer", help="Player on your roster to trade away (id or part of the name)")
            p.add_argument("--drop", help="With --source fa: player on your roster to release for each pickup "
                                          "(default: the best drop is found for each)")
            p.add_argument("--tolerance", type=float, default=0.25,
                           help="With --offer: allowed trade-rank difference as a fraction (default 0.25)")
            p.add_argument("--position", help="F, D, G, C, LW or RW")
            p.add_argument("--limit", type=int, default=20)
            p.add_argument("--min-games", type=int, default=5)
        p.set_defaults(func=func)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except A.NotSynced as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
