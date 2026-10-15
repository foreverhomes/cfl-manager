"""CLI: python -m cfl {sync,serve,teams,profile,targets}"""

import argparse
import json
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
    conn = connect(settings.db_path)
    try:
        results = sync_all(conn, settings, args.resources or None)
    except ValueError as exc:
        sys.exit(str(exc))
    ok = True
    for r in results:
        ok &= r["status"] == "ok"
        detail = r["detail"] if r["status"] == "ok" else r["detail"]["error"]
        print("{:<10} {:<6} {}".format(r["resource"], r["status"], json.dumps(detail)))
    print("db:", settings.db_path)
    if not ok:
        sys.exit(1)


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("cfl.api:app", host=args.host, port=args.port, reload=args.reload)


def cmd_teams(_args) -> None:
    settings = get_settings()
    ctx = A.load_context(connect(settings.db_path), settings)
    for t, name in ctx.teams.items():
        print("{}  {}{}".format(t, name, "   <- me" if t == settings.my_team_id else ""))


def cmd_profile(args) -> None:
    settings = get_settings()
    conn = connect(settings.db_path)
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
    conn = connect(settings.db_path)
    ctx = A.load_context(conn, settings)
    team = _team_arg(ctx, args.team)
    from_team = _team_arg(ctx, args.from_team) if args.from_team else None
    offer = None
    if args.offer:
        offer = ctx.resolve_player(args.offer, team)
        if offer is None:
            sys.exit("No single player on {} matches '{}'.".format(ctx.teams[team], args.offer))
    source = args.source or ("trade" if from_team or offer else "fa")
    try:
        res = A.targets(conn, ctx, team, source=source, position=args.position, limit=args.limit,
                        min_games=args.min_games, strategy=args.strategy, punt=args.punt or [], from_team=from_team,
                        offer=offer, tolerance=args.tolerance)
    except ValueError as exc:
        sys.exit(str(exc))
    print("Need weights:", ", ".join("{} {:.2f}".format(c, w) for c, w in res["need_weights"].items()))
    if res["offer"]:
        o = res["offer"]
        print("Offering {} (trade rank #{}, need value {:.2f}) -> comparable ranks #{}-#{}".format(
            o["name"], o["trade_rank"], o["need_value"], *o["comparable_trade_ranks"]))
    for i, p in enumerate(res["targets"], 1):
        top = sorted(p["z"].items(), key=lambda kv: kv[1], reverse=True)[:3]
        extra = ""
        if res["offer"]:
            extra = "  #{:<4} you {:+.2f} / them {:+.2f}{}  ({})".format(
                p["trade_rank"], p["your_gain"], p["their_gain"], "  WIN-WIN" if p["win_win"] else "",
                p["fantasy_team"])
        elif source == "fa" and "replaces" in p:
            extra = "  upgrade {:+.2f} over {}".format(p["upgrade"], p["replaces"]["name"])
        elif p.get("fantasy_team"):
            extra = "  ({})".format(p["fantasy_team"])
        print("{:>2}. {:<24} {:<4} {:<10} need {:>6.2f}  gp {:>2}/{:<2}  best: {}{}".format(
            i, p["name"], p["nhl_team"] or "", ",".join(e for e in p["eligible"] if e != "F"), p["need_value"],
            p["gp_this_season"], p["gp_last_season"], ", ".join("{} {:+.1f}".format(c, z) for c, z in top), extra))


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

    p = sub.add_parser("serve", help="Run the HTTP API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("teams", help="List team ids")
    p.set_defaults(func=cmd_teams)

    for name, func in (("profile", cmd_profile), ("targets", cmd_targets)):
        p = sub.add_parser(name)
        p.add_argument("team", nargs="?", help="Team id or part of the name (default: FANTRAX_MY_TEAM_ID)")
        p.add_argument("--strategy", default="marginal", choices=["marginal", "weakness", "even"])
        p.add_argument("--punt", nargs="*", help="Categories to ignore, e.g. --punt Hit +/-")
        if name == "targets":
            p.add_argument("--source", choices=["fa", "trade"], help="default: trade with --from-team, else fa")
            p.add_argument("--from-team", help="Only players on this team (id or part of the name)")
            p.add_argument("--offer", help="Player on your roster to trade away (id or part of the name)")
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
