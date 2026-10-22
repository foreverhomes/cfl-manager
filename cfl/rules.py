"""League rule settings: each number that a league vote could change, with today's value as its default.

A change takes effect from a league year. league_rules keeps every change, so earlier years keep the
values they had. See "Configurable rules" in docs/offseason-plan.md.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, Optional, Tuple

from .db import Connection, row, rows, transaction
from .sync import SHEET_SEASONS, league_season, now_iso


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _month_day(value: Any) -> bool:
    if not (isinstance(value, str) and re.fullmatch(r"\d\d-\d\d", value)):
        return False
    try:
        date(2001, int(value[:2]), int(value[3:]))   # 2001 isn't a leap year, so the day is in every year
    except ValueError:
        return False
    return True


# What a value of each kind must be: a test, and the words for it.
KINDS: Dict[str, Tuple[Callable[[Any], bool], str]] = {
    "money": (lambda v: _number(v) and v >= 0, "an amount of $0 or more"),
    "count": (lambda v: type(v) is int and v >= 1, "a whole number of 1 or more"),
    "share": (lambda v: _number(v) and 0 < v <= 1, "a share above 0, up to 1"),
    "month_day": (_month_day, "a day of the year, as MM-DD"),
}


@dataclass(frozen=True)
class Rule:
    key: str
    default: Any
    kind: str    # a key of KINDS
    label: str
    cite: str    # the docs/rules.md section it comes from
    check: Callable[[Any], Optional[str]] = lambda value: None   # this rule's own limit: what a value must be


RULES = {r.key: r for r in [
    Rule("contracts.salary_cap", None, "money", "Salary cap", "Salaries",   # None: Fantrax's, else $220
         lambda v: None if v > 0 else "more than $0"),
    Rule("contracts.year_bank", 50, "count", "Contract-year bank", "Contracts"),
    Rule("contracts.max_years", 5, "count", "Longest contract, in seasons", "Contracts",
         lambda v: None if v <= SHEET_SEASONS else "at most {}, the sheets' season columns".format(SHEET_SEASONS)),
    Rule("contracts.ufa_age", 27, "count", "UFA age", "UFA"),
    Rule("contracts.ufa_day", "07-01", "month_day", "The day the UFA age is measured on", "UFA"),
    Rule("contracts.elc_salary", 3.0, "money", "ELC salary", "Prospects"),
    Rule("contracts.elc_max_years", 3, "count", "Longest ELC, in seasons", "Prospects"),
    Rule("contracts.free_release_max", 3.0, "money", "Highest salary released for free", "Buyouts and Releases"),
    Rule("contracts.buyout_share", 0.5, "share", "Share of the salary a buyout costs, rounded down",
         "Buyouts and Releases"),
    Rule("draft.rounds", 5, "count", "Entry draft rounds", "Entry Draft"),
    Rule("draft.drafts_shown", 3, "count", "Drafts shown and tradeable", "Entry Draft"),
]}


def defaults() -> Dict[str, Any]:
    return {key: r.default for key, r in RULES.items()}


def load(conn: Connection, league_id: str, year: int) -> Dict[str, Any]:
    """Every rule's value for a league year: its most recent change from that year or earlier, else its default."""
    out = defaults()
    for c in rows(conn, "SELECT key, value FROM league_rules WHERE league_id=? AND from_year<=? ORDER BY id",
                  (league_id, year)):
        if c["key"] in RULES:   # a rule removed from the code can still have saved changes
            out[c["key"]] = RULES[c["key"]].default if c["value"] is None else json.loads(c["value"])
    return out


def _conflict(rules: Dict[str, Any]) -> Optional[str]:
    """What's wrong with one league year's rules taken together, if anything."""
    if rules["contracts.elc_max_years"] > rules["contracts.max_years"]:
        return "an ELC can't be longer than the longest contract, {} seasons".format(rules["contracts.max_years"])
    return None


def change(conn: Connection, league_id: str, key: str, value: Any, from_year: int, actor: str,
           note: Optional[str] = None) -> None:
    """Save a change to a rule from a league year on. A value of None puts the default back.

    Refuses an unknown rule, a year before this league year, a value that doesn't fit the rule, and a
    value that conflicts with another rule in any year from from_year on: later saved changes count too.
    """
    rule = RULES.get(key)
    if rule is None:
        raise LookupError("No rule called {}".format(key))
    season = league_season(conn, league_id)
    if season and from_year < season:
        raise ValueError("A change can't start before {}, this league year".format(season))
    if value is not None:
        test, expected = KINDS[rule.kind]
        problem = rule.check(value) if test(value) else expected
        if problem:
            raise ValueError("{}: must be {}".format(rule.label, problem))
    with transaction(conn):
        conn.execute("INSERT INTO league_rules (league_id, key, from_year, value, note, changed_by, changed_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (league_id, key, from_year, None if value is None else json.dumps(value),
                      (note or "").strip() or None, actor, now_iso()))
        last = row(conn, "SELECT MAX(from_year) AS y FROM league_rules WHERE league_id=?", (league_id,))["y"]
        for year in range(from_year, last + 1):
            problem = _conflict(load(conn, league_id, year))
            if problem:
                raise ValueError("From {}: {}".format(year, problem))   # the transaction undoes the insert
