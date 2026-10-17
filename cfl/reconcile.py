"""Check the app's contracts against a league-wide contract sheet: one row per player, every team.

A GM keeps such a sheet (the "CFL Contracts" sheet: Player, Team, Pos, Owner, Birthday, Type, then a
column per season). Comparing it with the app:

- **Import:** a rostered player the app has no contract for takes the sheet's contract. It's saved
  as an app contract record (`contract_overrides`), like an edit made in the app, with a note
  saying where it came from, so it survives syncs.
- **Review:** a player whose app contract differs from the sheet's, or whom the two sources place
  on different teams, is listed to settle by hand. Nothing is changed for him.
"""

import difflib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import analysis as A
from .contracts import validate
from .db import Connection, rows, transaction, upsert
from .sync import MAX_CONTRACT_YEARS, _money, _season_start, normalize_name, now_iso

# Sheet contract types the app tracks separately; every other signed type is a standard contract.
SHEET_TYPES = {"elc": "ELC", "in-season ufa": "IN_SEASON_UFA"}
EXPIRY_MARKERS = {"UFA", "RFA"}
FREE_AGENT_OWNERS = {"", "free agent", "fa"}
NOTE_PREFIX = "From the CFL Contracts sheet"


@dataclass
class SheetContract:
    row: int                        # 1-based row on the sheet
    name: str
    owner: Optional[str]            # None for a free agent
    nhl_team: str
    position: str                   # F / D / G
    birth_date: Optional[str]
    sheet_type: str                 # as written: "UFA Draft", "ELC", "Prospect", ...
    salaries: List[float]           # current season first; empty when unsigned this season
    length_known: bool              # False when the sheet has a salary but no posted length
    note: str = ""

    @property
    def contract_type(self) -> str:
        return SHEET_TYPES.get(self.sheet_type.strip().lower(), "STANDARD")


@dataclass
class Match:
    entry: SheetContract
    player: Optional[A.Player] = None
    method: Optional[str] = None
    problem: Optional[str] = None   # why no player was matched
    candidates: List[str] = field(default_factory=list)


def parse_contract_sheet(sheet_rows: List[List[str]], season_year: int) -> List[SheetContract]:
    """Rows of the sheet's contracts tab. The header row starts with "Player"; salaries are read
    from the current season's column (26-27, 2026/27, ...) rightward until a UFA/RFA marker or a
    blank. A blank right after the salaries, before the last season column, means the sheet
    doesn't know the length."""
    header_at = next((i for i, r in enumerate(sheet_rows) if r and r[0].strip().lower() == "player"), None)
    if header_at is None:
        raise ValueError("No header row starting with 'Player'")
    header = [c.strip() for c in sheet_rows[header_at]]
    col = {c.lower(): j for j, c in enumerate(header) if c}
    for needed in ("owner", "type"):
        if needed not in col:
            raise ValueError("No '{}' column on the sheet".format(needed.title()))
    first = next((j for j, c in enumerate(header) if _season_start(c) == season_year), None)
    if first is None:
        raise ValueError("No {}-{:02d} season column on the sheet".format(season_year, (season_year + 1) % 100))
    last = first
    while last + 1 < len(header) and _season_start(header[last + 1]) is not None:
        last += 1
    last = min(last, first + MAX_CONTRACT_YEARS - 1)

    def cell(r: List[str], name: str) -> str:
        j = col.get(name)
        return r[j].strip() if j is not None and j < len(r) else ""

    out = []
    for i, r in enumerate(sheet_rows[header_at + 1:], start=header_at + 2):
        name = (r[0] if r else "").strip()
        if not name:
            continue
        salaries: List[float] = []
        j = first
        while j <= last and j < len(r) and _money(r[j]) is not None:
            salaries.append(_money(r[j]))
            j += 1
        after = r[j].strip().upper() if j <= last and j < len(r) else ""
        owner = cell(r, "owner")
        out.append(SheetContract(
            row=i, name=name, owner=None if owner.lower() in FREE_AGENT_OWNERS else owner,
            nhl_team=cell(r, "team"), position=cell(r, "pos").upper(), birth_date=cell(r, "birthday") or None,
            sheet_type=cell(r, "type"), salaries=salaries,
            length_known=not salaries or j > last or after in EXPIRY_MARKERS, note=cell(r, "notes")))
    return out


def _narrow(cands: List[A.Player], entry: SheetContract, team_id: Optional[str]) -> List[A.Player]:
    """Same-name players: keep those with the sheet's birthday, then its team, then its position."""
    for keep in (lambda p: entry.birth_date and p.birth_date == entry.birth_date,
                 lambda p: team_id and p.team_id == team_id,
                 lambda p: p.group == entry.position,
                 lambda p: p.nhl_team == entry.nhl_team):
        kept = [p for p in cands if keep(p)]
        if kept:
            cands = kept
        if len(cands) == 1:
            break
    return cands


def match_contracts(entries: Sequence[SheetContract], players: Dict[str, A.Player]
                    ) -> Tuple[List[Match], Dict[str, str]]:
    """Link sheet rows to Fantrax players, and sheet owners to Fantrax teams.

    By exact name, narrowed by birthday, owner's team and position when several players share it.
    Otherwise by birthday with a similar name (Matt/Matthew, Yegor/Egor), then fuzzily on the
    owner's roster (typos). A birthday on both sides that disagrees rejects a name match.
    Owners map to the Fantrax team that rosters most of their unambiguously named players.
    """
    by_key: Dict[str, List[A.Player]] = defaultdict(list)
    by_birth: Dict[str, List[A.Player]] = defaultdict(list)
    by_team: Dict[str, List[A.Player]] = defaultdict(list)
    for p in players.values():
        by_key[normalize_name(p.name)].append(p)
        if p.birth_date:
            by_birth[p.birth_date].append(p)
        if p.team_id:
            by_team[p.team_id].append(p)

    votes: Dict[str, Counter] = defaultdict(Counter)
    for e in entries:
        cands = by_key.get(normalize_name(e.name), [])
        if e.owner and len(cands) == 1 and cands[0].team_id:
            votes[e.owner][cands[0].team_id] += 1
    owner_team = {o: c.most_common(1)[0][0] for o, c in votes.items()
                  if c.most_common(1)[0][1] * 2 > sum(c.values())}

    def similar(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()

    matches: List[Match] = []
    for e in entries:
        team_id = owner_team.get(e.owner) if e.owner else None
        m = Match(e)
        cands = by_key.get(normalize_name(e.name), [])
        if len(cands) > 1:
            cands = _narrow(cands, e, team_id)
        if len(cands) == 1:
            p = cands[0]
            if e.birth_date and p.birth_date and p.birth_date != e.birth_date:
                m.problem = "same name, different birthday ({} on the sheet, {} in Fantrax)".format(
                    e.birth_date, p.birth_date)
                m.candidates = [p.fantrax_id]
            else:
                m.player, m.method = p, "name"
        elif cands:
            m.problem = "several Fantrax players with this name"
            m.candidates = [p.fantrax_id for p in cands]
        else:
            born = [p for p in by_birth.get(e.birth_date or "", [])
                    if p.group == e.position and similar(p.name, e.name) >= 0.6]
            fuzzy = sorted(((similar(p.name, e.name), p) for p in by_team.get(team_id or "", [])),
                           key=lambda x: -x[0])
            if len(born) == 1:
                m.player, m.method = born[0], "birthday"
            elif fuzzy and fuzzy[0][0] >= 0.8 and (len(fuzzy) == 1 or fuzzy[0][0] - fuzzy[1][0] >= 0.15):
                m.player, m.method = fuzzy[0][1], "fuzzy {:.2f}".format(fuzzy[0][0])
            else:
                m.problem = "no Fantrax player with this name"
        matches.append(m)

    # Several rows on one player (a stale copy under another spelling): keep the one whose owner
    # rosters him in Fantrax. With no such single row, none can be trusted.
    rows_for: Dict[str, List[Match]] = defaultdict(list)
    for m in matches:
        if m.player:
            rows_for[m.player.fantrax_id].append(m)
    for fid, ms in rows_for.items():
        if len(ms) == 1:
            continue
        keep = [m for m in ms if m.entry.owner and owner_team.get(m.entry.owner) == m.player.team_id]
        for m in ms:
            if len(keep) == 1 and m is keep[0]:
                continue
            m.problem = ("another row for this player matches his Fantrax team (row {})".format(keep[0].entry.row)
                         if len(keep) == 1 else "more than one sheet row matches this player")
            m.candidates, m.player, m.method = [fid], None, None
    return matches, owner_team


def _money_list(salaries: Sequence[float]) -> str:
    return " / ".join("${:g}".format(s) for s in salaries) if salaries else "none"


def compare(ctx: A.Context, matches: Sequence[Match], owner_team: Dict[str, str],
            app_sources: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Sort matched rows into:

    - imports: rostered, no app contract, a valid signed contract on the sheet, same team on both.
    - review: the app's contract differs from the sheet's (salary, years, later seasons, the sheet
      has none, an explicit type), or the sources put a contracted player on different teams.
    - type_only: same salaries, but the sheet says ELC / in-season UFA where the app records no
      type (a salary-sheet contract always reads as standard).
    - roster_only: signed on the sheet, no app contract, but Fantrax has him elsewhere or unrostered.
    `app_sources` labels where each app contract comes from (player id -> "Kevin's salary sheet").
    """
    app_sources = app_sources or {}
    out: Dict[str, List[Dict[str, Any]]] = {"imports": [], "review": [], "type_only": [], "roster_only": []}
    agree = 0
    for m in matches:
        p, e = m.player, m.entry
        if p is None:
            continue
        app = list(p.contract_salaries) if p.salary is not None else []
        if not app and not e.salaries:
            continue
        sheet_team = owner_team.get(e.owner) if e.owner else None
        reasons = []
        if sheet_team != p.team_id:
            reasons.append("sheet has him on {}, Fantrax on {}".format(
                ctx.teams.get(sheet_team, e.owner) if e.owner else "no team",
                ctx.teams.get(p.team_id, p.team_id) if p.team_id else "no team"))
        if app and e.salaries:
            if app[0] != e.salaries[0]:
                reasons.append("salary")
            if e.length_known and len(app) != len(e.salaries):
                reasons.append("years")
            elif app[0] == e.salaries[0] and app[:len(e.salaries)] != e.salaries[:len(app)]:
                reasons.append("later seasons' salaries")
            if p.contract_type != e.contract_type and (p.contract_type != "STANDARD" or reasons):
                reasons.append("type")
        elif app:
            reasons.append("sheet has no {} contract ({})".format(A.season_label(ctx, 0), e.sheet_type or "blank"))

        record = {
            "fantrax_id": p.fantrax_id, "name": p.name, "sheet_name": e.name, "sheet_row": e.row,
            "match": m.method, "team": ctx.teams.get(p.team_id) if p.team_id else None,
            "roster_status": p.roster_status, "sheet_owner": e.owner, "sheet_type": e.sheet_type,
            "sheet": e.salaries, "sheet_length_known": e.length_known, "sheet_contract_type": e.contract_type,
            "app": app, "app_contract_type": p.contract_type if app else None,
            "app_source": app_sources.get(p.fantrax_id, p.contract_source) if app else None,
            "sheet_note": e.note, "reasons": reasons,
        }
        if not app:
            if reasons:
                out["roster_only"].append(record)
                continue
            try:
                validate(e.salaries, e.contract_type)
            except ValueError as exc:
                out["review"].append(dict(record, reasons=["sheet contract breaks a league rule: {}".format(exc)]))
                continue
            out["imports"].append(record)
        elif reasons:
            out["review"].append(record)
        elif p.contract_type != e.contract_type:
            out["type_only"].append(dict(record, reasons=["type"]))
        else:
            agree += 1

    matched_ids = {m.player.fantrax_id for m in matches if m.player}
    on_sheet = {m.player.fantrax_id for m in matches if m.player and m.entry.salaries}
    imported = {r["fantrax_id"] for r in out["imports"]}
    not_on_sheet = [{"fantrax_id": p.fantrax_id, "name": p.name, "team": ctx.teams.get(p.team_id),
                     "app": p.contract_salaries, "app_source": app_sources.get(p.fantrax_id, p.contract_source)}
                    for p in ctx.players.values()
                    if p.team_id and p.salary is not None and p.fantrax_id not in matched_ids]
    still_missing = [{"fantrax_id": p.fantrax_id, "name": p.name, "team": ctx.teams.get(p.team_id),
                      "roster_status": p.roster_status}
                     for p in ctx.players.values()
                     if p.team_id and p.roster_status != "MINORS" and p.salary is None
                     and p.fantrax_id not in imported and p.fantrax_id not in on_sheet]
    unmatched = [{"sheet_row": m.entry.row, "sheet_name": m.entry.name, "sheet_owner": m.entry.owner,
                  "sheet": m.entry.salaries, "problem": m.problem, "candidates": m.candidates}
                 for m in matches if m.player is None and m.entry.salaries]
    order = {"salary": 0, "years": 1, "later seasons' salaries": 2}
    by_team = lambda r: (r.get("team") or "", r["name"])   # noqa: E731
    return {
        "sheet_rows": len(matches), "signed_on_sheet": sum(1 for m in matches if m.entry.salaries),
        "matched": len(matched_ids), "agree": agree,
        "owner_teams": {o: ctx.teams.get(t, t) for o, t in sorted(owner_team.items())},
        "imports": sorted(out["imports"], key=by_team),
        "review": sorted(out["review"], key=lambda r: (min(order.get(x, 3) for x in r["reasons"]),) + by_team(r)),
        "type_only": sorted(out["type_only"], key=by_team),
        "roster_only": sorted(out["roster_only"], key=by_team),
        "unmatched_signed": unmatched,
        "app_contracts_not_on_sheet": sorted(not_on_sheet, key=by_team),
        "still_missing": sorted(still_missing, key=by_team),
    }


def app_contract_sources(conn: Connection, ctx: A.Context) -> Dict[str, str]:
    """Where each player's app contract comes from, in words: whose salary sheet, or an app edit."""
    out = {}
    for c in rows(conn, "SELECT team_id, player_id FROM contracts WHERE league_id=? AND player_id IS NOT NULL",
                  (ctx.league_id,)):
        p = ctx.players.get(c["player_id"])
        if p is not None and p.contract_source == "sheet" and (c["player_id"] not in out or p.team_id == c["team_id"]):
            out[c["player_id"]] = "{} salary sheet".format(ctx.teams.get(c["team_id"], c["team_id"]))
    for p in ctx.players.values():
        if p.contract_source == "override":
            out[p.fantrax_id] = "app edit"
        elif p.contract_source == "app":
            out[p.fantrax_id] = "app ({} contracts live in the app)".format(ctx.teams.get(p.team_id, p.team_id))
    return out


def reconcile(conn: Connection, ctx: A.Context, sheet_rows: List[List[str]]) -> Dict[str, Any]:
    """Parse the sheet, match it to Fantrax players and compare it with the app's contracts."""
    entries = parse_contract_sheet(sheet_rows, ctx.league["season_year"])
    matches, owner_team = match_contracts(entries, ctx.players)
    return compare(ctx, matches, owner_team, app_contract_sources(conn, ctx))


def import_contracts(conn: Connection, league_id: str, imports: Sequence[Dict[str, Any]]) -> int:
    """Save the sheet's contract for each import as an app contract record, all in one transaction."""
    ts = now_iso()
    records = []
    for r in imports:
        note = "{}, row {}".format(NOTE_PREFIX, r["sheet_row"])
        if not r["sheet_length_known"]:
            note += " (length not posted; 1 season assumed)"
        if r["sheet_note"]:
            note += ": " + r["sheet_note"]
        records.append({"league_id": league_id, "player_id": r["fantrax_id"],
                        "salaries": json.dumps(validate(r["sheet"], r["sheet_contract_type"])),
                        "contract_type": r["sheet_contract_type"], "note": note, "updated_at": ts})
    with transaction(conn):
        upsert(conn, "contract_overrides", records, ["league_id", "player_id"])
    return len(records)


def _contract_text(r: Dict[str, Any], side: str) -> str:
    text = _money_list(r[side])
    if side == "sheet" and r["sheet"] and not r["sheet_length_known"]:
        text += " (length not posted)"
    kind = r["sheet_contract_type"] if side == "sheet" else r["app_contract_type"]
    return text + (" " + kind if r[side] and kind and kind != "STANDARD" else "")


def format_report(report: Dict[str, Any]) -> str:
    lines = ["{sheet_rows} sheet rows, {signed_on_sheet} signed this season; {matched} matched to Fantrax "
             "players; {agree} contracts agree.".format(**report),
             "Owners: " + ", ".join("{} = {}".format(o, t) for o, t in report["owner_teams"].items())]

    def section(title: str, items: List[Dict[str, Any]], fmt) -> None:
        lines.extend(["", "{} ({})".format(title, len(items))] + ["  " + fmt(r) for r in items])

    section("IMPORT: no contract in the app; the sheet's is saved", report["imports"],
            lambda r: "{:<26} {:<24} {}".format(r["name"], r["team"] or "", _contract_text(r, "sheet")))
    section("REVIEW: the app and the sheet disagree", report["review"],
            lambda r: "{:<26} {:<24} app {} ({}) | sheet {}  [{}]".format(
                r["name"], r["team"] or "", _contract_text(r, "app"), r["app_source"] or "-",
                _contract_text(r, "sheet"), "; ".join(r["reasons"])))
    section("TYPE ONLY: same salaries; the sheet adds a contract type the app doesn't record", report["type_only"],
            lambda r: "{:<26} {:<24} {} -> {}".format(r["name"], r["team"] or "", _money_list(r["app"]),
                                                     r["sheet_contract_type"]))
    section("ROSTER ONLY: signed on the sheet, no app contract, rostered elsewhere in Fantrax", report["roster_only"],
            lambda r: "{:<26} {}  [{}]".format(r["name"], _contract_text(r, "sheet"), "; ".join(r["reasons"])))
    section("UNMATCHED: signed on the sheet, no Fantrax player found", report["unmatched_signed"],
            lambda r: "row {:<4} {:<26} {:<10} {}  [{}]".format(r["sheet_row"], r["sheet_name"], r["sheet_owner"] or "",
                                                              _money_list(r["sheet"]), r["problem"]))
    section("NOT ON THE SHEET: rostered with an app contract", report["app_contracts_not_on_sheet"],
            lambda r: "{:<26} {:<24} {} ({})".format(r["name"], r["team"] or "", _money_list(r["app"]), r["app_source"]))
    section("STILL MISSING: rostered (not minors), no contract in the app or on the sheet", report["still_missing"],
            lambda r: "{:<26} {:<24} {}".format(r["name"], r["team"] or "", r["roster_status"]))
    return "\n".join(lines)


CSV_COLUMNS = ["category", "name", "team", "roster_status", "reasons", "app", "app_type", "app_source",
               "sheet", "sheet_type", "sheet_length_known", "sheet_owner", "sheet_row", "sheet_name", "fantrax_id",
               "sheet_note"]


def csv_rows(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Everything that needs a person's attention, one row per player, for a spreadsheet."""
    out = []
    for category in ("review", "type_only", "roster_only", "unmatched_signed", "app_contracts_not_on_sheet",
                     "still_missing"):
        for r in report[category]:
            out.append({
                "category": category, "name": r.get("name") or r.get("sheet_name"), "team": r.get("team"),
                "roster_status": r.get("roster_status"),
                "reasons": "; ".join(r.get("reasons") or ([r["problem"]] if r.get("problem") else [])),
                "app": _money_list(r["app"]) if "app" in r else "", "app_type": r.get("app_contract_type"),
                "app_source": r.get("app_source"),
                "sheet": _money_list(r["sheet"]) if "sheet" in r else "", "sheet_type": r.get("sheet_type"),
                "sheet_length_known": r.get("sheet_length_known"), "sheet_owner": r.get("sheet_owner"),
                "sheet_row": r.get("sheet_row"), "sheet_name": r.get("sheet_name"),
                "fantrax_id": r.get("fantrax_id") or ",".join(r.get("candidates") or []),
                "sheet_note": r.get("sheet_note"),
            })
    return out
