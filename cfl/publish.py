"""Publish an app-owned team's contracts to its Google Sheet, the read-only view for other GMs.

The app is the source of truth for teams marked `"owner": "app"` in salary_sheets.json. This
module rebuilds that team's sheet tab from the app (Fantrax roster status plus the app's
contracts) and posts it to an Apps Script web app bound to the sheet (cfl/sheet_publisher.gs),
which rewrites the tab. Totals are sheet formulas, so other GMs can check the math.
"""

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from . import analysis as A
from .config import ROOT, Settings
from .db import Connection, row, transaction, upsert
from .sync import MAX_CONTRACT_YEARS, app_owned_teams, now_iso

WIDTH = 2 + MAX_CONTRACT_YEARS + 2           # Player, Pos, one column per season, At expiry, Notes
SEASON_COLS = [chr(ord("C") + i) for i in range(MAX_CONTRACT_YEARS)]
HEADER_STYLE = {"bold": True, "background": "#ccffff"}
SECTION_STYLE = {"bold": True}
TOTAL_STYLE = {"bold": True, "background": "#ccffff"}
TYPE_NOTE = {"ELC": "ELC", "IN_SEASON_UFA": "In-season UFA (no bank years)"}
TEMPLATE = ROOT / "cfl" / "sheet_publisher.gs"


def _pos(p: A.Player) -> str:
    return "/".join(e for e in p.eligible if e != "F") or (p.position or "")


def _num(x: float) -> Any:
    return int(x) if float(x).is_integer() else x


def _at_expiry(ctx: A.Context, p: A.Player) -> str:
    end = A.contract_end(ctx, p)
    if not end:
        return ""
    return "{} {}".format(end["status"], end["year"]) if end["status"] else "? {}".format(end["year"])


def _player_row(ctx: A.Context, p: A.Player, note: str = "") -> List[Any]:
    seasons = [_num(x) for x in p.contract_salaries[:MAX_CONTRACT_YEARS]]
    seasons += [""] * (MAX_CONTRACT_YEARS - len(seasons))
    type_note = TYPE_NOTE.get(p.contract_type, "") if p.salary is not None else ""
    return [p.name, _pos(p)] + seasons + [_at_expiry(ctx, p), "; ".join(x for x in (type_note, note) if x)]


def _ranges(row_numbers: List[int]) -> List[str]:
    """Contiguous runs of sheet rows as season-column ranges: [3,4,5,9] -> ["C3:G5", "C9:G9"]."""
    out, run = [], []
    for r in sorted(row_numbers):
        if run and r != run[-1] + 1:
            out.append("{}{}:{}{}".format(SEASON_COLS[0], run[0], SEASON_COLS[-1], run[-1]))
            run = []
        run.append(r)
    if run:
        out.append("{}{}:{}{}".format(SEASON_COLS[0], run[0], SEASON_COLS[-1], run[-1]))
    return out


def build_roster_sheet(ctx: A.Context, team_id: str) -> Dict[str, Any]:
    """The tab as rows of cell values (formulas as "=..." strings) plus row styles (1-based rows)."""
    values: List[List[Any]] = []
    styles: List[Dict[str, Any]] = []

    def add(cells: List[Any], style: Optional[Dict[str, Any]] = None) -> int:
        values.append(cells)
        if style:
            styles.append(dict(style, row=len(values)))
        return len(values)

    by_value = lambda p: (-(p.salary or 0), p.name)  # noqa: E731
    roster = ctx.roster(team_id)
    capped = [p for p in roster if p.roster_status in A.CAPPED_STATUSES]
    ir = sorted((p for p in roster if p.roster_status == "INJURED_RESERVE"), key=by_value)
    minors = sorted((p for p in roster if p.roster_status == "MINORS"), key=by_value)
    exempt = lambda p: p.contract_type == "IN_SEASON_UFA"  # noqa: E731

    # A leading apostrophe keeps "2026/2027" as text rather than letting Sheets read it as a date.
    add(["Player", "Pos"] + ["'" + A.season_label(ctx, i).replace("/", "/20") for i in range(MAX_CONTRACT_YEARS)]
        + ["At expiry", "Notes"], HEADER_STYLE)
    cap_rows, bank_rows = [], []
    for label, group in (("Forwards", "F"), ("Defense", "D"), ("Goalies", "G")):
        add([label], SECTION_STYLE)
        for p in sorted((p for p in capped if p.group == group), key=by_value):
            r = add(_player_row(ctx, p, "" if p.salary is not None else "No contract on file"))
            cap_rows.append(r)
            if not exempt(p):
                bank_rows.append(r)
    add(["Buyouts"], SECTION_STYLE)
    for salaries in ctx.buyouts.get(team_id, []):
        cap_rows.append(add(["Buyout", ""] + [_num(x) for x in salaries[:MAX_CONTRACT_YEARS]]
                            + [""] * (MAX_CONTRACT_YEARS - len(salaries)) + ["", "Half salary, rest of contract"]))
    add([])
    committed_at = add(["Committed"], TOTAL_STYLE)
    cap_at = add(["Salary cap", ""] + [_num(ctx.salary_caps.get(team_id) or A.DEFAULT_SALARY_CAP)] * MAX_CONTRACT_YEARS,
                 SECTION_STYLE)
    space_at = add(["Cap space"], TOTAL_STYLE)
    years_at = add(["Contract years"], SECTION_STYLE)
    add([])
    add(["Injured Reserve"], SECTION_STYLE)
    ir_rows = []
    for p in ir:
        ir_rows.append(add(_player_row(ctx, p, "IR: doesn't count against the cap or contract years")))
    add([])
    add(["Minor League"], SECTION_STYLE)
    for p in minors:
        add(_player_row(ctx, p))

    # Totals as formulas. IR relief applies to the current season only; later seasons count IR players.
    def sums(col: str, rows_: List[int]) -> List[str]:
        return ["SUM({})".format(rng.replace("C", col, 1).replace(":G", ":" + col)) for rng in _ranges(rows_)]

    committed, space = ["Committed", ""], ["Cap space", ""]
    for i, col in enumerate(SEASON_COLS):
        parts = sums(col, cap_rows) + ([] if i == 0 else sums(col, ir_rows))
        committed.append("=" + "+".join(parts) if parts else 0)
        space.append("={c}{cap}-{c}{tot}".format(c=col, cap=cap_at, tot=committed_at))
    values[committed_at - 1] = committed + ["", "Active + bench + buyouts; IR counts from next season"]
    values[space_at - 1] = space + ["", ""]
    bank = _ranges(bank_rows)
    values[years_at - 1] = ["Contract years", "", "=COUNT({})".format(",".join(bank)) if bank else 0] \
        + [""] * (MAX_CONTRACT_YEARS - 1) + ["", "Seasons left on each contract, this one included; minors and IR excluded"]
    return {"values": values, "styles": styles, "width": WIDTH}


def _state(conn: Connection, key: str) -> Optional[Dict[str, Any]]:
    r = row(conn, "SELECT value FROM app_state WHERE key=?", (key,))
    return json.loads(r["value"]) if r else None


def _save_state(conn: Connection, key: str, value: Dict[str, Any]) -> None:
    with transaction(conn):
        upsert(conn, "app_state", [{"key": key, "value": json.dumps(value), "updated_at": now_iso()}], ["key"])


def publish_status(conn: Connection, settings: Settings) -> Dict[str, Any]:
    owned = app_owned_teams(conn, settings)
    teams = {}
    for team_id, spec in owned.items():
        t = row(conn, "SELECT name FROM teams WHERE id=?", (team_id,))
        teams[team_id] = dict({"team_name": t["name"] if t else team_id, "sheet_url": spec.get("url"),
                               "tab": spec.get("publish_tab", "roster")}, **(_state(conn, "publish:" + team_id) or {}))
    has_url = bool(settings.sheet_publish_url or any(s.get("publish_url") for s in owned.values()))
    missing = None
    if not settings.sheet_publish_token:
        missing = "token"
    elif not has_url:
        missing = "url"
    return {"configured": bool(owned) and missing is None, "missing": missing, "teams": teams}


def publish(conn: Connection, settings: Settings, force: bool = False) -> Dict[str, Any]:
    """Rebuild and send every app-owned team's sheet tab. Skips a team whose content hasn't changed."""
    owned = app_owned_teams(conn, settings)
    if not owned:
        return {"skipped": 'no team has "owner": "app" in salary_sheets.json'}
    if not settings.sheet_publish_token:
        return {"skipped": "CFL_SHEET_PUBLISH_TOKEN is not set"}
    ctx = A.load_context(conn, settings)
    out = {}
    for team_id, spec in owned.items():
        name = ctx.teams.get(team_id, team_id)
        url = spec.get("publish_url") or settings.sheet_publish_url
        if not url:
            out[name] = {"published": False, "reason": "CFL_SHEET_PUBLISH_URL is not set"}
            continue
        sheet = build_roster_sheet(ctx, team_id)
        digest = hashlib.sha256(json.dumps(sheet, sort_keys=True, default=str).encode()).hexdigest()
        key = "publish:" + team_id
        last = _state(conn, key) or {}
        if not force and last.get("hash") == digest:
            out[name] = {"published": False, "reason": "unchanged since {}".format(last.get("published_at"))}
            continue
        stamp = "Published from CFL Manager {}".format(datetime.now().strftime("%b %d, %Y %I:%M %p"))
        payload = {"token": settings.sheet_publish_token, "tab": spec.get("publish_tab", "roster"),
                   "values": sheet["values"] + [[], [stamp]], "styles": sheet["styles"], "width": sheet["width"],
                   "frozen_rows": 1}
        # Apps Script answers a POST with a redirect to the result, which requests follows as a GET.
        resp = requests.post(url, json=payload, timeout=60)
        try:
            reply = resp.json()
        except ValueError:
            raise RuntimeError("The publish web app didn't answer with JSON (HTTP {}). Check it is deployed with "
                               "'Who has access: Anyone'.".format(resp.status_code))
        if not reply.get("ok"):
            raise RuntimeError("The publish web app refused the update: {}".format(reply.get("error")))
        _save_state(conn, key, {"hash": digest, "published_at": now_iso(), "rows": len(payload["values"])})
        out[name] = {"published": True, "rows": len(payload["values"])}
    return out


def write_script(settings: Settings) -> str:
    """Write the Apps Script with this app's token filled in, to a git-ignored path. Returns the path."""
    if not settings.sheet_publish_token:
        raise RuntimeError("Set CFL_SHEET_PUBLISH_TOKEN in .env first")
    target = settings.db_path.parent / "sheet_publisher.gs"
    target.write_text(TEMPLATE.read_text().replace("__PUBLISH_TOKEN__", settings.sheet_publish_token))
    return str(target)
