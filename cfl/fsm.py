"""State machines: rows that move through a small, fixed set of states, one checked move at a time.

The offseason is built from them (see docs/offseason-plan.md): the league's phase, each RFA's rights,
each offer sheet, the entry draft and its picks. A machine is a table of transitions, each saying
which states it leaves from, where it goes, and what must be true first. Every machine's rows live
in a table of their own, keyed by league_id and whatever else identifies one, with a `state` column.

fire() is the one place a row changes state, so every move is checked and logged the same way. Its
concurrency control is a conditional UPDATE: a row moves only if it's still in the state the move
was checked against, so two people acting at once can't both win. That works the same on SQLite
and Postgres, with no locks.
"""

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence

from .db import Connection, row, transaction
from .sync import now_iso

Row = Dict[str, Any]
# What's stopping a move now, in words for the person trying it. Empty means nothing is.
Guard = Callable[[Connection, Row, Any], List[str]]
# A move's other writes, in its transaction. What it returns is logged as the move's detail.
Effects = Callable[[Connection, Row], Optional[Dict[str, Any]]]


class Conflict(Exception):
    """The move isn't allowed from the row's state, or someone moved the row first (HTTP 409)."""


class Blocked(Exception):
    """The move's guard refused it (HTTP 400). `reasons` says why."""

    def __init__(self, reasons: List[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _words(name: str) -> str:
    return name.replace("_", " ")


def _no_guard(conn: Connection, current: Row, ctx: Any) -> List[str]:
    return []


@dataclass(frozen=True)
class Transition:
    source: FrozenSet[str]
    dest: str
    guard: Guard = _no_guard


class Machine:
    def __init__(self, name: str, table: str, key_columns: Sequence[str], transitions: Dict[str, Transition]):
        if "league_id" not in key_columns:
            raise ValueError("A machine's rows are keyed by league_id, among others")
        self.name, self.table, self.key_columns, self.transitions = name, table, tuple(key_columns), transitions
        self.states = frozenset().union(*(t.source | {t.dest} for t in transitions.values()))

    def available(self, conn: Connection, current: Row, ctx: Any = None) -> Dict[str, List[str]]:
        """Each trigger allowed from the row's state, with what's stopping it now (for the UI's buttons)."""
        return {trigger: t.guard(conn, current, ctx) for trigger, t in self.transitions.items()
                if current["state"] in t.source}

    def fire(self, conn: Connection, key: Dict[str, Any], trigger: str, actor: str, ctx: Any = None,
             effects: Optional[Effects] = None, force_reason: Optional[str] = None) -> str:
        """Move one row by `trigger` and return its new state. In one transaction, it:

        1. refuses a trigger that isn't allowed from the row's state (Conflict);
        2. runs the guard, and refuses if it gives reasons (Blocked) unless forced with a reason;
        3. moves the row only if it's still in the state it was read in. If it isn't, someone else
           moved it first (Conflict);
        4. runs `effects`, such as releasing players or moving picks. They mustn't commit, so that
           if they fail the move is undone too;
        5. logs the move in machine_events.

        `ctx` is passed to the guard as is: whatever this machine's guards need from the caller.
        The caller decides who may fire a trigger, and lets only the admin force one.
        """
        if set(key) != set(self.key_columns):
            raise ValueError("A {} is identified by {}".format(self.name, ", ".join(self.key_columns)))
        t = self.transitions.get(trigger)
        if t is None:
            raise ValueError("A {} has no move called {}".format(self.name, trigger))
        where = " AND ".join("{} = ?".format(c) for c in self.key_columns)
        params = [key[c] for c in self.key_columns]
        entity = ":".join(str(key[c]) for c in self.key_columns if c != "league_id") or key["league_id"]
        with transaction(conn):
            current = row(conn, "SELECT * FROM {} WHERE {}".format(self.table, where), params)
            if current is None:
                raise LookupError("No {} {}".format(_words(self.name), entity))
            if current["state"] not in t.source:
                raise Conflict("Can't {} this {}: it's {}".format(_words(trigger), _words(self.name),
                                                                    _words(current["state"])))
            reasons = t.guard(conn, current, ctx)
            forced = (force_reason or "").strip() or None
            if reasons and not forced:
                raise Blocked(reasons)
            cur = conn.execute("UPDATE {} SET state = ? WHERE {} AND state = ?".format(self.table, where),
                               [t.dest] + params + [current["state"]])
            if cur.rowcount != 1:
                raise Conflict("Someone else changed this {} first. Reload and try again.".format(_words(self.name)))
            detail = dict((effects(conn, current) if effects else None) or {})
            if reasons:
                detail["overrode"] = reasons
            conn.execute("INSERT INTO machine_events (league_id, machine, entity_id, trigger, from_state, to_state, "
                         "actor, forced_reason, detail, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (key["league_id"], self.name, entity, trigger, current["state"], t.dest, actor,
                          forced if reasons else None, json.dumps(detail) if detail else None, now_iso()))
        return t.dest
