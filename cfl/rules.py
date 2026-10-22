"""League rule settings: each number that a league vote could change, with today's value as its default.

A change takes effect from a league year. league_rules keeps every change, so earlier years keep the
values they had. See "Configurable rules" in docs/offseason-plan.md.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict

from .db import Connection, rows


@dataclass(frozen=True)
class Rule:
    key: str
    default: Any
    label: str
    cite: str   # the docs/rules.md section it comes from


RULES = {r.key: r for r in [
    Rule("contracts.salary_cap", None, "Salary cap (unset: Fantrax's, else $220)", "Salaries"),
    Rule("contracts.year_bank", 50, "Contract-year bank", "Contracts"),
    Rule("contracts.max_years", 5, "Longest contract, in seasons", "Contracts"),
    Rule("contracts.ufa_age", 27, "UFA age", "UFA"),
    Rule("contracts.ufa_day", "07-01", "The day the UFA age is measured on", "UFA"),
    Rule("contracts.elc_salary", 3.0, "ELC salary", "Prospects"),
    Rule("contracts.elc_max_years", 3, "Longest ELC, in seasons", "Prospects"),
    Rule("contracts.free_release_max", 3.0, "Highest salary released for free", "Buyouts and Releases"),
    Rule("contracts.buyout_share", 0.5, "Share of the salary a buyout costs, rounded down", "Buyouts and Releases"),
    Rule("draft.rounds", 5, "Entry draft rounds", "Entry Draft"),
    Rule("draft.drafts_shown", 3, "Drafts shown and tradeable", "Entry Draft"),
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
