"""Offseason: every contract that runs out after a season, and whether its player becomes an RFA or a UFA.

Everything is relative to the league's current season, so the list moves on by itself each year. The
next offseason is the summer after the current season; later ones follow each season a contract can
still run. An offseason is named by its year, because free agency is decided on July 1 of that year
(analysis.contract_end).
"""

from datetime import date
from typing import Any, Dict, List, Optional

from . import analysis as A
from .sync import MAX_CONTRACT_YEARS

STATUS_ORDER = {"RFA": 0, "UFA": 1, None: 2}   # None: no birth date on file, so the status is unknown


def offseasons(ctx: A.Context) -> List[Dict[str, Any]]:
    """The offseasons a contract on file can end in: after this season, and after each later one."""
    season_year = ctx.league.get("season_year") or date.today().year
    return [{"year": season_year + i, "after_season": A.season_label(ctx, i - 1)}
            for i in range(1, MAX_CONTRACT_YEARS + 1)]


def expiring(ctx: A.Context, year: Optional[int] = None) -> Dict[str, Any]:
    """Rostered players whose contracts end in the offseason of `year` (default: the next one).

    RFAs come first, then UFAs, then players with no birth date; the biggest final-season salary
    leads each group. `no_contract` lists rostered players outside the minors with no contract (or
    no contract length) on file, who can't be placed in any offseason.
    """
    options = offseasons(ctx)
    chosen = next((o for o in options if o["year"] == (year or options[0]["year"])), None)
    if chosen is None:
        raise ValueError("year must be one of: " + ", ".join(str(o["year"]) for o in options))
    players, no_contract = [], []
    for p in ctx.players.values():
        if not p.team_id:
            continue
        end = A.contract_end(ctx, p)
        if end is None and p.roster_status != "MINORS":
            no_contract.append({"fantrax_id": p.fantrax_id, "name": p.name, "fantasy_team_id": p.team_id})
        elif end is not None and end["year"] == chosen["year"]:
            final = p.contract_salaries[-1] if p.contract_salaries else p.salary
            players.append(dict(A.player_summary(p, ctx), final_salary=final))
    players.sort(key=lambda x: (STATUS_ORDER[x["contract_end"]["status"]], -(x["final_salary"] or 0), x["name"]))
    no_contract.sort(key=lambda x: x["name"])
    return dict(chosen, offseasons=options, ufa_age=A.UFA_AGE, players=players, no_contract=no_contract)
