"""Offline tests for name matching, rate blending, lineups and need weights.

They use SQLite. Set CFL_TEST_DATABASE_URL to a Postgres database to run them there instead;
it is wiped before every test.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest

from cfl import analysis as A
from cfl import db
from cfl.config import Settings
from cfl.db import Connection, connect, upsert
from cfl.sync import match_players, match_sheet_names, normalize_name, parse_salary_sheet, split_fantrax_name

LEAGUE = "L1"
TEST_POSTGRES = os.environ.get("CFL_TEST_DATABASE_URL")


@pytest.fixture(autouse=True)
def _app_database(monkeypatch):
    """The database the API opens: the test Postgres, or none (SQLite), never a DATABASE_URL from .env.
    Logins are off unless a test turns them on (see _logins)."""
    monkeypatch.setenv("DATABASE_URL", TEST_POSTGRES or "")
    for name in ("GOOGLE_CLIENT_ID", "CFL_SESSION_SECRET", "CFL_ADMIN_EMAILS", "VERCEL"):
        monkeypatch.setenv(name, "")


def _empty_postgres(url: str) -> None:
    import psycopg
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    db._schema_ready.discard(url)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Connection]:
    if TEST_POSTGRES:
        _empty_postgres(TEST_POSTGRES)
        c = connect(TEST_POSTGRES)
    else:
        c = connect(tmp_path / "t.db")
    upsert(c, "league", [{"id": LEAGUE, "name": "Test", "season_year": 2026, "scoring_type": "x",
                          "start_date": None, "end_date": None, "draft_type": None,
                          "roster_info": '{"positionConstraints": {"C": {"maxActive": 1}, "D": {"maxActive": 1},'
                                         ' "F": {"maxActive": 1}, "G": {"maxActive": 1}}}',
                          "playoffs": None, "draft_settings": None, "synced_at": None}], ["id"])
    c.commit()
    yield c
    c.close()


def test_name_helpers():
    assert split_fantrax_name("Stützle, Tim") == ("Tim", "Stützle", "Tim Stützle")
    assert normalize_name("Tim Stützle") == normalize_name("Tim Stutzle") == "timstutzle"
    assert normalize_name("J.T. Miller") == "jtmiller"


def _player(c, fid, name, team, pos):
    first, last, full = split_fantrax_name(name)
    upsert(c, "players", [{"fantrax_id": fid, "name": full, "first_name": first, "last_name": last,
                           "nhl_team": team, "position": pos, "rotowire_id": None, "sportradar_id": None,
                           "adp": None}], ["fantrax_id"])


def _skater(c, nhl_id, name, team, pos, season=20262027, gp=10, **stats):
    rec = {"nhl_id": nhl_id, "season_id": season, "full_name": name, "last_name": name.split()[-1],
           "team_abbrevs": team, "position_code": pos, "gp": gp, "synced_at": None}
    for col in A.SKATER_COLUMNS:
        rec[col] = stats.get(col, 0)
    rec["toi_per_game"] = 1000.0
    upsert(c, "skater_stats", [rec], ["nhl_id", "season_id"])


def test_match_players_disambiguates_same_name_and_nicknames(conn):
    # Two Elias Petterssons on the same team, split by position.
    _player(conn, "f1", "Pettersson, Elias", "VAN", "C")
    _player(conn, "f2", "Pettersson, Elias", "VAN", "D")
    _skater(conn, 1, "Elias Pettersson", "VAN", "C")
    _skater(conn, 2, "Elias Pettersson", "VAN", "D")
    # Nickname: Fantrax "Matt" vs NHL "Matthew", same team/position.
    _player(conn, "f3", "Benning, Matt", "TOR", "D")
    _skater(conn, 3, "Matthew Benning", "TOR", "D")
    # Same last name + team but different position: must NOT match.
    _player(conn, "f4", "White, Colin", "NJD", "RW")
    _skater(conn, 4, "Colton White", "NJD", "D")
    conn.commit()

    match_players(conn, LEAGUE)
    got = {r[0]: r[1] for r in conn.execute("SELECT fantrax_id, nhl_id FROM players")}
    assert got == {"f1": 1, "f2": 2, "f3": 3, "f4": None}


def test_blend_weights_current_season_and_shrinks():
    # 2 goals in 4 games this season, 10 in 80 last season at half weight, 5 games of 0.2 G/gp shrink.
    rate = A._blend(2, 4, 10, 80, 0.5, 5, 0.2)
    assert rate == pytest.approx((2 + 5 + 1) / (4 + 40 + 5))
    # No data at all -> population rate.
    assert A._blend(0, 0, 0, 0, 0.5, 5, 0.3) == pytest.approx(0.3)


def _ctx_with(players, cats=("G", "Hit"), slots=None):
    categories = [A.Category(c, c, "SKATER", 1.0, False) for c in cats]
    return A.Context(LEAGUE, {}, categories, {"t1": "One", "t2": "Two", "t3": "Three"}, 1,
                     {p.fantrax_id: p for p in players}, slots or {"C": 1, "D": 1, "F": 1, "G": 0},
                     20262027, 20252026, [])


def _p(fid, eligible, group, value, team="t1", status="ACTIVE"):
    p = A.Player(fid, fid, None, eligible[0], eligible, group, 1, gp_cur=10, team_id=team, roster_status=status)
    p.impact = {"G": value, "Hit": 0.0}
    p.z = {"G": value, "Hit": 0.0}
    p.rates = {col: 0.0 for col in A.SKATER_COLUMNS}
    return p


def test_best_lineup_respects_slots_and_skips_minors():
    players = [
        _p("c1", ["C", "F"], "F", 5),
        _p("c2", ["C", "F"], "F", 4),      # C slot taken -> goes to F flex
        _p("c3", ["C", "F"], "F", 3),      # no slot left
        _p("d1", ["D"], "D", 1),
        _p("d2", ["D"], "D", 9, status="MINORS"),
    ]
    ctx = _ctx_with(players)
    assert [p.fantrax_id for p in A.best_lineup(ctx, "t1")] == ["c1", "c2", "d1"]


def test_marginal_need_weights_favor_close_categories():
    ctx = _ctx_with([])
    # Team t1 dominates G (far ahead) but is level with the field in Hit.
    profiles = {
        "t1": {"categories": {"G": {"z": 3.0, "rank": 1}, "Hit": {"z": 0.0, "rank": 2}}},
        "t2": {"categories": {"G": {"z": -1.5, "rank": 2}, "Hit": {"z": 0.1, "rank": 1}}},
        "t3": {"categories": {"G": {"z": -1.5, "rank": 3}, "Hit": {"z": -0.1, "rank": 3}}},
    }
    w = A.need_weights(ctx, "t1", profiles)
    assert w["Hit"] > w["G"]
    assert sum(w.values()) / len(w) == pytest.approx(1.0)
    assert A.need_weights(ctx, "t1", profiles, punt=["Hit"])["Hit"] == 0.0
    weak = A.need_weights(ctx, "t1", profiles, strategy="weakness")
    assert weak["Hit"] > weak["G"]


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("FANTRAX_LEAGUE_ID", raising=False)
    from cfl.config import get_settings
    s = get_settings()
    assert isinstance(s, Settings) and s.league_id


def test_trade_targets_from_one_team():
    players = [
        _p("mine", ["C", "F"], "F", 1, team="t1"),
        _p("two_a", ["C", "F"], "F", 5, team="t2"),
        _p("two_kid", ["C", "F"], "F", 9, team="t2", status="MINORS"),
        _p("three_a", ["C", "F"], "F", 8, team="t3"),
        _p("free", ["C", "F"], "F", 7, team=None, status=None),
    ]
    ctx = _ctx_with(players)
    assert ctx.resolve_team("tw") == "t2" and ctx.resolve_team("t") is None  # "t" matches all three

    res = A.targets(None, ctx, "t1", source="trade", from_team="t2", min_games=0)
    assert [t["fantrax_id"] for t in res["targets"]] == ["two_a"]
    assert res["from_team"] == {"team_id": "t2", "team_name": "Two"}
    res = A.targets(None, ctx, "t1", source="trade", from_team="t2", min_games=0, include_minors=True)
    assert [t["fantrax_id"] for t in res["targets"]] == ["two_kid", "two_a"]

    with pytest.raises(ValueError):
        A.targets(None, ctx, "t1", source="fa", from_team="t2")
    with pytest.raises(ValueError):
        A.targets(None, ctx, "t1", source="trade", from_team="t1")


def test_comparable_band():
    assert A.comparable_band(40, 0.25) == (32, 50)
    assert A.comparable_band(1, 0.25) == (1, 4)       # elite players still get a ±3 window
    assert A.comparable_band(200, 0.25) == (160, 250)


def test_trade_ranks_put_goalies_on_the_skater_scale():
    # 4 skater slots : 1 goalie slot -> the #1 goalie lines up with the #4 skater.
    players = [_p("s{}".format(i), ["C", "F"], "F", 10 - i) for i in range(1, 7)]
    goalie = _p("g1", ["G"], "G", 3)
    goalie.z = {"G": 3, "Hit": 0}
    ctx = _ctx_with(players + [goalie], slots={"C": 2, "D": 2, "G": 1})
    ranks = ctx.trade_ranks()
    assert ranks["s1"] == 1 and ranks["s4"] == 4 and ranks["g1"] == 5 and ranks["s5"] == 6


def test_trade_targets_for_an_offered_player_stay_in_value_band():
    players = [
        _p("mine", ["C", "F"], "F", 5, team="t1"),
        _p("a", ["C", "F"], "F", 4, team="t1"),
        _p("close", ["C", "F"], "F", 5.5, team="t2"),
        _p("star", ["C", "F"], "F", 100, team="t2"),
    ] + [_p("fill{}".format(v), ["C", "F"], "F", v, team="t3") for v in range(90, 10, -10)]
    for p in players:
        p.rates["goals"] = p.z["G"] / 10   # production tracks value
    ctx = _ctx_with(players)
    # star #1, fillers #2-#9, close #10, mine #11 -> comparable ranks #8-#14.
    res = A.targets(None, ctx, "t1", source="trade", offer="mine", min_games=0)
    got = {t["fantrax_id"]: t for t in res["targets"]}
    assert set(got) == {"fill30", "fill20", "close"}
    assert res["offer"]["trade_rank"] == 11 and res["offer"]["comparable_trade_ranks"] == [8, 14]

    # Two lineup spots per team (C + F flex). close starts for t2, so t2 loses out by swapping him
    # for "mine"; fill30 is bench depth on t3, so t3 neither gains nor loses.
    assert got["fill30"]["your_gain"] > got["close"]["your_gain"] > 0
    assert got["close"]["their_gain"] < 0 and not got["close"]["win_win"]
    assert got["fill30"]["their_gain"] == pytest.approx(0, abs=1e-9)
    assert [t["fantrax_id"] for t in res["targets"]][0] == "fill30"
    assert ctx.players["close"].team_id == "t2" and ctx.players["mine"].team_id == "t1"   # restored

    assert ctx.resolve_player("min", "t1") == "mine" and ctx.resolve_player("close", "t1") is None
    with pytest.raises(ValueError):
        A.targets(None, ctx, "t1", source="trade", offer="close")   # not on your roster


def test_daily_lineups_give_depth_goalies_the_off_nights():
    g = []
    for fid, team, value in (("g1", "BOS", 3), ("g2", "CAR", 2), ("g3", "SEA", 1)):
        p = _p(fid, ["G"], "G", value)
        p.nhl_team, p.start_share = team, 0.5
        p.rates = {"wins": 0.25}
        g.append(p)
    ctx = _ctx_with(g, cats=(), slots={"G": 2})
    ctx.categories = [A.Category("W", "W", "GOALIE", 1.0, False)]
    # 14-day window, 4 game days: all three play once, then SEA plays alone on three nights.
    ctx.schedule = [frozenset({"BOS", "CAR", "SEA"}), frozenset({"SEA"}), frozenset({"SEA"}), frozenset({"SEA"})]
    ctx.horizon_days = 14
    games = A.lineup_games(ctx, "t1")
    assert games["g1"] == pytest.approx(0.5) and games["g2"] == pytest.approx(0.5)   # 1 game / 2 weeks
    assert games["g3"] == pytest.approx(1.5)   # benched the night all three play, starts the other 3
    assert A._team_totals(ctx, games)["W"] == pytest.approx((0.5 + 0.5 + 1.5) * 0.25)


def _trade_ctx():
    """Two teams at a 3-player cap; production tracks value so trades move the needle."""
    players = [
        _p("a1", ["C", "F"], "F", 9, team="t1"), _p("a2", ["C", "F"], "F", 6, team="t1"),
        _p("a3", ["D"], "D", 2, team="t1"),
        _p("b1", ["C", "F"], "F", 8, team="t2"), _p("b2", ["D"], "D", 5, team="t2"),
        _p("b3", ["D"], "D", 1, team="t2"),
        _p("c1", ["C", "F"], "F", 7, team="t3"),
    ]
    for p in players:
        p.rates["goals"] = p.z["G"] / 10
    ctx = _ctx_with(players)
    ctx.roster_cap = 3
    return ctx


def test_market_values_are_additive_value_over_replacement():
    ctx = _trade_ctx()
    mv, ranks = ctx.market_values(), ctx.trade_ranks()
    # Ordered like trade rank, never negative, and the replacement level (3 teams x 3 skater slots
    # = rank 9, past the end of this tiny pool, so the worst player) is worth zero.
    ordered = sorted(mv, key=ranks.get)
    assert [round(mv[f], 6) for f in ordered] == sorted((round(v, 6) for v in mv.values()), reverse=True)
    assert mv["b3"] == 0 and mv["a1"] == pytest.approx(9 - 1)
    # A package is worth the single player with at most its summed value.
    assert ctx.equivalent_rank(mv["b2"] + mv["a3"]) == ranks["a2"]   # 4 + 1 = 5 = a2; c1 (6) is too much
    assert ctx.equivalent_rank(0) == ranks["b3"]


def test_evaluate_trade_two_for_one_drops_to_the_cap():
    ctx = _trade_ctx()
    res = A.evaluate_trade(ctx, "t1", give=["a3"], other_team_id="t2", get=["b1", "b2"])
    me, them = res["sides"]
    # t1 sends 1 and receives 2 -> 4 players, over the cap of 3: it drops its weakest (not a newcomer).
    assert me["roster"] == {"before": 3, "after": 3, "cap": 3}
    assert [p["fantrax_id"] for p in me["auto_dropped"]] == ["a2"]
    assert them["roster"]["after"] == 2 and not them["auto_dropped"]
    assert me["expected_category_wins"]["delta"] > 0 > them["expected_category_wins"]["delta"]
    assert res["market"]["you_get"] > res["market"]["you_give"]
    assert {p.fantrax_id for p in ctx.roster("t1")} == {"a1", "a2", "a3"}   # rosters restored

    # No partner: just the cost of losing a player.
    solo = A.evaluate_trade(ctx, "t1", give=["a1"])
    assert len(solo["sides"]) == 1 and solo["sides"][0]["expected_category_wins"]["delta"] < 0

    with pytest.raises(ValueError):
        A.evaluate_trade(ctx, "t1", give=["b1"], other_team_id="t2")        # not your player
    with pytest.raises(ValueError):
        A.evaluate_trade(ctx, "t1", give=[], get=["b1"])                    # b1 is on t2: pick t2 as partner


def _waiver_ctx():
    """_trade_ctx plus two free agents: a forward and a defenseman."""
    ctx = _trade_ctx()
    for p in (_p("fa_f", ["C", "F"], "F", 7, team=None, status=None), _p("fa_d", ["D"], "D", 4, team=None, status=None)):
        p.rates["goals"] = p.z["G"] / 10
        ctx.players[p.fantrax_id] = p
    return ctx


def test_fa_targets_simulate_each_pickup_with_its_best_drop():
    ctx = _waiver_ctx()
    # t1 is full (cap 3) with one C, one F flex and one D slot: a1 C, a2 F, a3 D.
    res = A.targets(None, ctx, "t1", source="fa", min_games=0)
    got = {t["fantrax_id"]: t for t in res["targets"]}
    # The defenseman replaces the weak D (a3). The forward would leave the D slot empty if a3 went,
    # so his best drop is a2, the forward he pushes out of the lineup.
    assert [d["fantrax_id"] for d in got["fa_d"]["drops"]] == ["a3"]
    assert [d["fantrax_id"] for d in got["fa_f"]["drops"]] == ["a2"]
    assert got["fa_d"]["your_gain"] > got["fa_f"]["your_gain"] > 0
    assert [t["fantrax_id"] for t in res["targets"]] == ["fa_d", "fa_f"]
    assert got["fa_d"]["market_value_net"] == pytest.approx(
        ctx.market_values()["fa_d"] - ctx.market_values()["a3"], abs=0.01)
    assert res["roster"] == {"count": 3, "cap": 3} and res["drop"] is None
    assert ctx.players["fa_f"].team_id is None and ctx.players["fa_f"].roster_status is None   # restored

    # A chosen drop applies to every pickup: losing the only D costs the forward pickup.
    res = A.targets(None, ctx, "t1", source="fa", min_games=0, drop="a3")
    got = {t["fantrax_id"]: t for t in res["targets"]}
    assert [d["fantrax_id"] for d in got["fa_f"]["drops"]] == ["a3"] and got["fa_f"]["your_gain"] < 0
    assert res["drop"]["name"] == "a3"

    # An open roster spot: nobody has to go.
    ctx.roster_cap = 4
    res = A.targets(None, ctx, "t1", source="fa", min_games=0)
    assert all(t["drops"] == [] for t in res["targets"])

    with pytest.raises(ValueError):
        A.targets(None, ctx, "t1", source="trade", drop="a3")
    with pytest.raises(ValueError):
        A.targets(None, ctx, "t1", source="fa", drop="b1")       # not on your roster


def test_evaluate_trade_prices_a_waiver_pickup():
    ctx = _waiver_ctx()
    for fid, salary in (("a1", 30), ("a2", 6), ("a3", 6)):
        _with_contract(ctx.players[fid], [salary])
    ctx.league = {"season_year": 2026}
    ctx.contract_teams, ctx.salary_caps = {"t1"}, {"t1": 50}

    res = A.evaluate_trade(ctx, "t1", give=["a3"], get=["fa_d"])
    assert len(res["sides"]) == 1
    me = res["sides"][0]
    assert me["roster"] == {"before": 3, "after": 3, "cap": 3} and not me["auto_dropped"]
    assert me["expected_category_wins"]["delta"] > 0
    assert res["market"]["you_get"] == pytest.approx(ctx.market_values()["fa_d"], abs=0.01)
    # a3 is released ($6: a $3 buyout); the pickup has no contract yet.
    assert me["cap"]["release_buyouts"] == 3 and me["cap"]["unknown_incoming"] == ["fa_d"]
    assert me["cap"]["space_after"] == 50 - 30 - 6 - 3

    # A pickup with a contract set counts against the cap.
    _with_contract(ctx.players["fa_d"], [2])
    res = A.evaluate_trade(ctx, "t1", give=["a3"], get=["fa_d"])
    assert res["sides"][0]["cap"]["space_after"] == 50 - 30 - 6 - 3 - 2
    assert ctx.players["fa_d"].roster_status is None

    # No drop on a full roster: the pickup counts, so the weakest player is cut.
    res = A.evaluate_trade(ctx, "t1", give=[], get=["fa_d"])
    assert [p["fantrax_id"] for p in res["sides"][0]["auto_dropped"]] == ["a3"]
    assert res["sides"][0]["roster"]["after"] == 3


def test_parse_salary_sheet_handles_sections_totals_and_mislabeled_seasons():
    rows = [
        ["Player", "Pos", "2026/2027", "2027/2028", "2027/2028", "2028/2029"],   # 3rd season mislabeled
        ["", "", "", "", "", ""],
        ["Morgan Geekie", "", "5", "5", "5", ""],
        ["Alex Ovechkin", "", "$4", "", "", ""],
        ["BUYOUTS", "", "", "", "", ""],
        ["Old Guy", "", "3", "3", "", ""],
        ["IR Cap relief", "", "-8", "", "", ""],
        ["", "", "189", "135", "77", "53"],                                        # totals: no name
        ["Injured Reserve", "", "", "", "", ""],
        ["Hurt Guy", "", "8", "8", "", ""],
        ["Minor League", "", "", "", "", ""],
    ]
    parsed = parse_salary_sheet(rows, 2026)
    assert parsed["layout"] == "seasons"
    got = {c["sheet_name"]: c for c in parsed["contracts"]}
    assert set(got) == {"Morgan Geekie", "Alex Ovechkin", "Old Guy", "Hurt Guy"}
    assert got["Morgan Geekie"]["salaries"] == [5, 5, 5] and got["Morgan Geekie"]["years_remaining"] == 3
    assert got["Alex Ovechkin"]["salary"] == 4 and got["Alex Ovechkin"]["years_remaining"] == 1
    assert got["Old Guy"]["section"] == "BUYOUT" and got["Hurt Guy"]["section"] == "IR"
    # A sheet that still shows last season starts reading at the current one.
    assert parse_salary_sheet([["Player", "2025/26", "2026/27"], ["X", "9", "7"]], 2026)["contracts"][0]["salaries"] == [7]


def test_parse_salary_sheet_other_gm_layouts():
    # Two-digit seasons, UFA/RFA markers, later seasons in unlabeled columns, "Last, First" names,
    # prospects and totals rows, and a repeated player.
    rows = [
        ["PLAYER", "TEAM", "2025-2026", "UFA", "2026-27"],
        ["Makar, Cale", "COL", "34", "34", "34", "UFA"],
        ["Rossi, Marco", "MIN", "3", "3", "6", "6", "6", "UFA"],
        ["Nikishin, Alexander", "CAR", "", "", "3", "3"],
        ["Nikishin, Alexander", "CAR", "", "", "3", "RFA"],
        ["TOTAL", "", "", "", "43"],
        ["PROSPECTS", "", "", "", ""],
        ["Tyson Foerster - PHI", "RW", "166", "23", ""],
    ]
    parsed = parse_salary_sheet(rows, 2026)
    got = {c["sheet_name"]: c["salaries"] for c in parsed["contracts"]}
    assert got == {"Cale Makar": [34], "Marco Rossi": [6, 6, 6], "Alexander Nikishin": [3, 3]}
    assert parsed["duplicates"] == ["Alexander Nikishin"]

    # No season columns: contract price x contract length (years left).
    flat = parse_salary_sheet([["PLAYER", "TEAM", "CONTRACT PRICE $", "CONTRACT LENGTH"],
                               ["Larkin, Dylan", "DET", "$14", "3"], ["CAP PUNISHMENT", "", "", ""],
                               ["Cut Guy", "", "$2", "1"]], 2026)
    assert flat["layout"] == "salary_years"
    assert [(c["sheet_name"], c["salaries"], c["section"]) for c in flat["contracts"]] == [
        ("Dylan Larkin", [14, 14, 14], "ROSTER"), ("Cut Guy", [2], "BUYOUT")]

    # Explicit column hints (letters) for sheets whose header doesn't line up with the data.
    pairs = [["PLAYER", "AGE"], ["McDavid, Connor", "29", "41", "2", "55", "4"]]
    hinted = parse_salary_sheet(pairs, 2026, salary_column="E", years_column="F")
    assert hinted["contracts"][0]["salaries"] == [55, 55, 55, 55]


def test_match_sheet_names_fuzzy_on_roster_exact_elsewhere():
    roster = [("a", "Juraj Slafkovsky"), ("b", "Shayne Gostisbehere"), ("c", "Shane Pinto")]
    pool = roster + [("d", "Connor McDavid"), ("e", "Sebastian Aho"), ("f", "Sebastian Aho")]
    got = match_sheet_names(["Juraj Slavkofsky", "Shane Gostibehere", "Shane Pinto", "Connor McDavid",
                             "Sebastian Aho", "Somebody Else"], roster, pool)
    assert got["Juraj Slavkofsky"][0] == "a" and "fuzzy" in got["Juraj Slavkofsky"][1]
    assert got["Shane Gostibehere"][0] == "b"
    assert got["Shane Pinto"] == ("c", "roster")
    assert got["Connor McDavid"] == ("d", "league")
    assert "Sebastian Aho" not in got and "Somebody Else" not in got   # ambiguous / unknown


def _with_contract(p, salaries):
    p.salary, p.contract_years, p.contract_salaries = salaries[0], len(salaries), list(salaries)
    return p


def test_cap_summary_follows_league_rules():
    players = [
        _with_contract(_p("a", ["C", "F"], "F", 5), [20, 20]),
        _with_contract(_p("b", ["D"], "D", 4, status="RESERVE"), [10]),
        _with_contract(_p("ir", ["C", "F"], "F", 3, status="INJURED_RESERVE"), [8, 8]),
        _with_contract(_p("kid", ["C", "F"], "F", 1, status="MINORS"), [3, 3]),
        _p("new", ["C", "F"], "F", 1),                                   # no salary on the sheet
    ]
    ctx = _ctx_with(players)
    ctx.league = {"season_year": 2026}
    ctx.contract_teams, ctx.salary_caps, ctx.buyouts = {"t1"}, {"t1": 50}, {"t1": [[2, 2]]}
    cap = A.cap_summary(ctx, "t1")
    assert cap["committed"] == 20 + 10 + 2          # IR and minors don't count; the buyout does
    assert cap["space"] == 18 and cap["ir_relief"] == 8 and cap["minors_relief"] == 3
    assert cap["unknown_salary"] == ["new"]
    assert cap["contract_years"] == 2 + 1 and cap["contract_year_bank"] == 50   # minors and IR excluded
    assert cap["future"][0] == {"season": "2027/28", "committed": 20 + 8 + 2, "space": 20,   # IR returns
                                "rfa_holds": 0, "rfa_players": []}     # no birth dates, so no RFAs known
    assert A.cap_summary(ctx, "t2") is None
    assert [A.release_cost(p) for p in players] == [10, 5, 4, 0, 0]   # $3 or less is a free release


def test_cap_summary_carries_rfas_forward_as_holds():
    def signed(fid, salaries, born, status="ACTIVE", contract_type="STANDARD"):
        p = _with_contract(_p(fid, ["C", "F"], "F", 1, status=status), salaries)
        p.birth_date, p.contract_type = born, contract_type
        return p

    players = [
        signed("rfa", [5, 7], "2005-01-01"),                         # 23 on July 1, 2028: RFA
        signed("ufa", [10], "1990-01-01"),                           # 37 on July 1, 2027: UFA
        signed("now", [4], "2004-03-01"),                            # 23 on July 1, 2027: RFA
        signed("ir", [6], "2003-01-01", status="INJURED_RESERVE"),   # IR counts in later seasons
        signed("kid", [1], "2006-01-01", status="MINORS"),           # minors don't
        signed("pickup", [2], "2005-01-01", contract_type="IN_SEASON_UFA"),   # always a UFA
        signed("unknown", [3], None),                                # no birth date, status unknown
    ]
    ctx = _ctx_with(players)
    ctx.league = {"season_year": 2026}
    ctx.contract_teams, ctx.salary_caps = {"t1"}, {"t1": 100}
    future = A.cap_summary(ctx, "t1")["future"]

    first = future[0]
    assert (first["season"], first["committed"], first["space"]) == ("2027/28", 7, 93)   # holds stay out
    assert first["rfa_holds"] == 6 + 4
    assert first["rfa_players"] == [
        {"fantrax_id": "ir", "name": "ir", "salary": 6, "after_season": "2026/27"},
        {"fantrax_id": "now", "name": "now", "salary": 4, "after_season": "2026/27"}]
    # From the season after his last one, each RFA carries over at his final-season salary.
    for f in future[1:]:
        assert (f["committed"], f["space"], f["rfa_holds"]) == (0, 100, 7 + 6 + 4)
        assert [x["fantrax_id"] for x in f["rfa_players"]] == ["rfa", "ir", "now"]
    assert future[1]["rfa_players"][0]["after_season"] == "2027/28"


def test_evaluate_trade_cap_effect_includes_drop_buyouts_and_flags_unknown_salaries():
    ctx = _trade_ctx()
    for fid, salary in (("a1", 30), ("a2", 6), ("a3", 2)):
        _with_contract(ctx.players[fid], [salary])
    ctx.league = {"season_year": 2026}
    ctx.contract_teams, ctx.salary_caps = {"t1"}, {"t1": 40}
    res = A.evaluate_trade(ctx, "t1", give=["a3"], other_team_id="t2", get=["b1", "b2"])
    me, them = res["sides"]
    assert [p["fantrax_id"] for p in me["auto_dropped"]] == ["a2"]
    cap = me["cap"]
    # a2 ($6) is cut to make room: a $3 buyout. a3's $2 leaves; t2's players have no known salary.
    assert (cap["space_before"], cap["space_after"]) == (2, 40 - 30 - 3)
    assert cap["salary_out"] == 2 and cap["drop_buyouts"] == 3
    assert cap["unknown_incoming"] == ["b1", "b2"] and not cap["over_cap"]
    assert them["cap"] is None


def test_contract_edits_override_the_sheet_and_follow_league_rules(conn, tmp_path):
    from cfl import contracts as C
    conn.execute("INSERT INTO teams (id, league_id, name) VALUES ('t1', ?, 'One')", (LEAGUE,))
    _player(conn, "f1", "Daccord, Joey", "SEA", "G")
    _player(conn, "f2", "Snuggerud, Jimmy", "STL", "RW")
    for fid, pos in (("f1", "G"), ("f2", "RW,F")):
        conn.execute("INSERT INTO league_players VALUES (?, ?, ?, 'T')", (LEAGUE, fid, pos))
        conn.execute("INSERT INTO roster_items VALUES (?, 1, 't1', ?, 'F', 'ACTIVE')", (LEAGUE, fid))
    conn.execute("INSERT INTO contracts (league_id, team_id, sheet_name, player_id, section, salary, "
                 "years_remaining, salaries) VALUES (?, 't1', 'Joey Daccord', 'f1', 'ROSTER', 3, 5, '[3,3,3,3,3]')",
                 (LEAGUE,))
    conn.commit()
    settings = Settings(LEAGUE, None, tmp_path / "unused.db", 0.5, 5, tmp_path / "none.json")

    ctx = A.load_context(conn, settings)
    assert (ctx.players["f1"].contract_source, ctx.players["f1"].contract_years) == ("sheet", 5)
    assert ctx.players["f2"].salary is None
    assert A.cap_summary(ctx, "t1")["unknown_salary"] == ["Jimmy Snuggerud"]

    with pytest.raises(ValueError):
        C.save_override(conn, LEAGUE, "f2", [3, 3, 3, 3], "ELC")          # ELCs run at most 3 seasons
    with pytest.raises(ValueError):
        C.save_override(conn, LEAGUE, "f2", [4, 4], "ELC")                # ... at $3
    with pytest.raises(ValueError):
        C.save_override(conn, LEAGUE, "f2", [3, None, 3])                 # no gaps
    with pytest.raises(ValueError):
        C.save_override(conn, LEAGUE, "f2", [5, 5], "IN_SEASON_UFA")      # pickups get 1 year

    C.save_override(conn, LEAGUE, "f2", [3, 3], "ELC", "year 2 of ELC")
    C.save_override(conn, LEAGUE, "f1", [3], "IN_SEASON_UFA")
    ctx = A.load_context(conn, settings)
    p2 = ctx.players["f2"]
    assert (p2.salary, p2.contract_years, p2.contract_type, p2.contract_source) == (3, 2, "ELC", "override")
    cap = A.cap_summary(ctx, "t1")
    assert cap["committed"] == 6 and cap["unknown_salary"] == []
    assert cap["contract_years"] == 2                                     # in-season UFA deals don't use the bank
    detail = C.contract_detail(conn, ctx, "f1")
    assert detail["sheet"]["salaries"] == [3, 3, 3, 3, 3] and detail["override"]["contract_type"] == "IN_SEASON_UFA"

    assert C.delete_override(conn, LEAGUE, "f1")
    ctx = A.load_context(conn, settings)
    assert (ctx.players["f1"].contract_source, ctx.players["f1"].contract_years) == ("sheet", 5)


def test_contract_edit_api_sets_years_and_keeps_the_salary(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from cfl.api import app
    conn.execute("INSERT INTO teams (id, league_id, name) VALUES ('t1', ?, 'One')", (LEAGUE,))
    _player(conn, "f1", "Daccord, Joey", "SEA", "G")
    _player(conn, "f2", "Snuggerud, Jimmy", "STL", "RW")
    for fid in ("f1", "f2"):
        conn.execute("INSERT INTO league_players VALUES (?, ?, 'F', 'T')", (LEAGUE, fid))
        conn.execute("INSERT INTO roster_items VALUES (?, 1, 't1', ?, 'F', 'ACTIVE')", (LEAGUE, fid))
    conn.execute("INSERT INTO contracts (league_id, team_id, sheet_name, player_id, section, salary, "
                 "years_remaining, salaries) VALUES (?, 't1', 'Joey Daccord', 'f1', 'ROSTER', 8, 5, '[8,8,8,8,8]')",
                 (LEAGUE,))
    conn.commit()
    monkeypatch.setenv("CFL_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("FANTRAX_LEAGUE_ID", LEAGUE)
    client = TestClient(app)

    res = client.put("/players/f1/contract", json={"years": 2})
    assert res.status_code == 200 and res.json()["effective"]["salaries"] == [8, 8]   # sheet salary kept
    assert client.put("/players/f2/contract", json={"years": 2}).status_code == 400  # no salary on file
    res = client.put("/players/f2/contract", json={"years": 2, "contract_type": "ELC"})
    assert res.json()["effective"]["salaries"] == [3, 3]                              # ELC is $3
    assert client.put("/players/f1/contract", json={"years": 0}).status_code == 400
    assert client.delete("/players/f1/contract").json()["effective"]["salaries"] == [8] * 5


def test_players_list_has_the_salary_for_each_contract_season(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from cfl.api import app
    conn.execute("INSERT INTO teams (id, league_id, name) VALUES ('t1', ?, 'One')", (LEAGUE,))
    _player(conn, "f1", "Daccord, Joey", "SEA", "G")
    _player(conn, "f2", "Snuggerud, Jimmy", "STL", "RW")
    for fid in ("f1", "f2"):
        conn.execute("INSERT INTO league_players VALUES (?, ?, 'F', 'T')", (LEAGUE, fid))
        conn.execute("INSERT INTO roster_items VALUES (?, 1, 't1', ?, 'F', 'ACTIVE')", (LEAGUE, fid))
    conn.execute("INSERT INTO contracts (league_id, team_id, sheet_name, player_id, section, salary, "
                 "years_remaining, salaries) VALUES (?, 't1', 'Joey Daccord', 'f1', 'ROSTER', 8, 3, '[8,9,10]')",
                 (LEAGUE,))
    conn.commit()
    monkeypatch.setenv("CFL_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("FANTRAX_LEAGUE_ID", LEAGUE)

    res = TestClient(app).get("/players", params={"team_id": "t1"})

    assert {p["fantrax_id"]: p["contract_salaries"] for p in res.json()["players"]} == {
        "f1": [8, 9, 10], "f2": []}
    cap = res.json()["cap"]                                     # the Players & cap tab's cap card
    assert (cap["committed"], cap["unknown_salary"]) == (8, ["Jimmy Snuggerud"])
    assert TestClient(app).get("/players", params={"q": "Dacc"}).json()["cap"] is None


def test_dashboard_overview_has_this_weeks_matchup_season_records_and_nhl_stats(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from cfl.api import app
    for i, (short, name, grp, lower) in enumerate((("A", "Assists", "SKATER", 0), ("GAA", "GAA", "GOALIE", 1),
                                                   ("G", "Goals", "SKATER", 0), ("W", "Wins", "GOALIE", 0))):
        conn.execute("INSERT INTO scoring_categories (league_id, short_name, name, grp, lower_is_better, sort_order) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (LEAGUE, short, name, grp, lower, i))
    for tid, name in (("t1", "One"), ("t2", "Two"), ("t3", "Three")):
        conn.execute("INSERT INTO teams (id, league_id, name) VALUES (?, ?, ?)", (tid, LEAGUE, name))
    # Week 1 is over; week 2 is on now. t1 plays t2 both weeks; t3 has a bye in week 2.
    conn.execute("INSERT INTO periods VALUES (?, 'scoring', 1, '2020-01-01T13:00:00.0-0500', '2020-01-07T18:59:59.0-0500')",
                 (LEAGUE,))
    conn.execute("INSERT INTO periods VALUES (?, 'scoring', 2, '2020-01-08T13:00:00.0-0500', '2099-01-07T18:59:59.0-0500')",
                 (LEAGUE,))
    conn.execute("INSERT INTO matchups VALUES (?, 2, 0, 't1', 't2'), (?, 2, 1, 't3', NULL)", (LEAGUE, LEAGUE))
    scores = {1: {"t1": {"G": 5, "A": 1, "W": 2, "GAA": 2.0}, "t2": {"G": 2, "A": 3, "W": 1, "GAA": 3.0}},
              2: {"t1": {"G": 3, "A": 2, "W": 1, "GAA": 2.5}, "t2": {"G": 1, "A": 4, "W": 1, "GAA": 2.1}}}
    for period, teams in scores.items():
        for me, opp in (("t1", "t2"), ("t2", "t1")):
            outcomes = []
            for cat, v in teams[me].items():
                theirs = teams[opp][cat]
                better = v < theirs if cat == "GAA" else v > theirs
                outcomes.append("TIE" if v == theirs else "WIN" if better else "LOSS")
                conn.execute("INSERT INTO matchup_category_scores VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (LEAGUE, period, me, opp, cat, v, outcomes[-1]))
            conn.execute("INSERT INTO matchup_results VALUES (?, ?, ?, ?, NULL, ?, ?, ?, NULL)",
                         (LEAGUE, period, me, opp, *(outcomes.count(o) for o in ("WIN", "LOSS", "TIE"))))
    conn.execute("INSERT INTO sync_log (resource, started_at, finished_at, status) "
                 "VALUES ('matchups', '2026-10-19T14:00:00+00:00', '2026-10-19T14:00:05+00:00', 'ok')")
    for fid, name, pos, status, nhl_id in (("f1", "Connor, Kyle", "LW", "ACTIVE", 1), ("f2", "Daccord, Joey", "G", "RESERVE", 2),
                                           ("f3", "Kid, Some", "C", "MINORS", None)):
        _player(conn, fid, name, "WPG", pos)
        conn.execute("UPDATE players SET nhl_id=? WHERE fantrax_id=?", (nhl_id, fid))
        conn.execute("INSERT INTO league_players VALUES (?, ?, ?, 'T')", (LEAGUE, fid, pos))
        conn.execute("INSERT INTO roster_items VALUES (?, 1, 't1', ?, ?, ?)", (LEAGUE, fid, pos, status))
    _skater(conn, 1, "Kyle Connor", "WPG", "L", gp=7, goals=4, assists=6)
    _skater(conn, 1, "Kyle Connor", "WPG", "L", season=20252026, gp=82, goals=41, assists=56)   # last season: not shown
    upsert(conn, "goalie_stats", [{"nhl_id": 2, "season_id": 20262027, "gp": 3, "wins": 2, "goals_against": 6,
                                   "toi_seconds": 3 * 3600, "saves": 90, "shots_against": 96, "shutouts": 0}],
           ["nhl_id", "season_id"])
    conn.commit()
    monkeypatch.setenv("CFL_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("FANTRAX_LEAGUE_ID", LEAGUE)
    client = TestClient(app)

    res = client.get("/teams/t1/overview").json()

    week = res["week"]
    assert (week["period"], week["bye"], week["scores_as_of"]) == (2, False, "2026-10-19T14:00:05+00:00")
    assert [(m["team_id"], m["record"]) for m in week["matchups"]] == [("t2", [1, 2, 1])]
    assert week["matchups"][0]["categories"]["G"] == {"value": 3, "opponent_value": 1, "outcome": "WIN"}
    # Categories read like a stat line, each with its season record (both weeks) and league rank.
    assert [(c["category"], c["record"], c["rank"]) for c in res["categories"]] == [
        ("G", "2-0-0", 1), ("A", "0-2-0", 2), ("W", "1-0-1", 1), ("GAA", "1-1-0", 1)]
    assert {p["fantrax_id"]: p["season"] for p in res["roster"]} == {
        "f1": {"G": 4, "A": 6}, "f2": {"W": 2, "GAA": 2.0}, "f3": None}
    assert res["roster_spots"] == {"used": 2, "limit": None}   # the prospect doesn't take a spot

    bye = client.get("/teams/t3/overview").json()["week"]
    assert (bye["bye"], bye["matchups"]) == (True, [])


def _owned_league(conn, tmp_path):
    """t1 is app-owned per a salary_sheets.json; returns Settings pointing at it."""
    import json as _json
    conn.execute("INSERT INTO teams (id, league_id, name, salary_cap) VALUES ('t1', ?, 'One', 220)", (LEAGUE,))
    sheets = tmp_path / "salary_sheets.json"
    sheets.write_text(_json.dumps({"t1": {"url": "https://example.invalid/sheet", "owner": "app"}}))
    return Settings(LEAGUE, None, tmp_path / "t.db", 0.5, 5, sheets, "https://example.invalid/publish", "tok")


def test_app_owned_team_adopts_its_sheet_contracts_once(conn, tmp_path):
    from cfl.sync import sync_salaries
    settings = _owned_league(conn, tmp_path)
    _player(conn, "f1", "Geekie, Morgan", "BOS", "C")
    conn.execute("INSERT INTO contracts (league_id, team_id, sheet_name, player_id, section, salary, years_remaining,"
                 " salaries) VALUES (?, 't1', 'Morgan Geekie', 'f1', 'ROSTER', 5, 3, '[5,5,5]')", (LEAGUE,))
    conn.execute("INSERT INTO contracts (league_id, team_id, sheet_name, player_id, section, salary, years_remaining,"
                 " salaries) VALUES (?, 't1', 'Typo Name', NULL, 'ROSTER', 2, 1, '[2]')", (LEAGUE,))
    conn.commit()
    first = sync_salaries(conn, settings)["One"]
    assert first["adopted"] == 1 and first["unmatched_dropped"] == ["Typo Name"] and not first["sheet_read"]
    import json as _json
    assert _json.loads(conn.execute("SELECT salaries FROM contract_overrides WHERE player_id='f1'").fetchone()[0]) == [5, 5, 5]
    assert conn.execute("SELECT COUNT(*) FROM contracts WHERE team_id='t1'").fetchone()[0] == 0
    assert sync_salaries(conn, settings)["One"]["adopted"] == 0      # the sheet is never read again


def test_published_sheet_layout_and_formulas():
    from cfl.publish import build_roster_sheet
    players = [
        _with_contract(_p("Fwd A", ["C", "F"], "F", 5), [10, 10]),
        _with_contract(_p("Fwd Pickup", ["LW", "F"], "F", 1), [2]),
        _with_contract(_p("Def B", ["D"], "D", 3, status="RESERVE"), [6]),
        _with_contract(_p("Goalie C", ["G"], "G", 2), [8, 8, 8]),
        _with_contract(_p("Hurt D", ["RW", "F"], "F", 2, status="INJURED_RESERVE"), [4, 4]),
        _with_contract(_p("Kid E", ["C", "F"], "F", 1, status="MINORS"), [3, 3, 3]),
        _p("Kid F", ["D"], "D", 0, status="MINORS"),
    ]
    for p in players:
        if p.fantrax_id == "Goalie C":
            p.position, p.group = "G", "G"
    players[1].contract_type = "IN_SEASON_UFA"
    players[5].contract_type = "ELC"
    ctx = _ctx_with(players)
    ctx.league, ctx.salary_caps = {"season_year": 2026}, {"t1": 220}
    sheet = build_roster_sheet(ctx, "t1")
    v = sheet["values"]
    assert v[0][:3] == ["Player", "Pos", "'2026/2027"] and v[0][6] == "'2030/2031"
    names = [r[0] for r in v if r]
    assert names.index("Forwards") < names.index("Fwd A") < names.index("Defense") < names.index("Def B") \
        < names.index("Goalies") < names.index("Goalie C") < names.index("Committed") \
        < names.index("Injured Reserve") < names.index("Hurt D") < names.index("Minor League") < names.index("Kid E")
    row = {r[0]: (i + 1, r) for i, r in enumerate(v) if r}
    committed = row["Committed"][1]
    ir_row = row["Hurt D"][0]
    assert "{}".format(ir_row) not in committed[2]                       # IR relief this season...
    assert "D{}:D{}".format(ir_row, ir_row) in committed[3]              # ...but it counts next season
    years = row["Contract years"][1][2]
    for name in ("Fwd A", "Def B", "Goalie C"):
        assert "G{}".format(row[name][0]) in years                       # counted rows end in column G
    for name in ("Fwd Pickup", "Hurt D", "Kid E"):                       # in-season UFA, IR, minors: no bank years
        assert ":G{}".format(row[name][0]) not in years and "C{}:".format(row[name][0]) not in years
    assert row["Kid E"][1][-1] == "ELC" and row["Fwd A"][1][2:4] == [10, 10]
    assert row["Cap space"][1][2] == "=C{}-C{}".format(row["Salary cap"][0], row["Committed"][0])


def test_web_app_is_revalidated_on_every_load():
    from fastapi.testclient import TestClient
    from cfl.api import app
    client = TestClient(app)
    assert client.get("/").headers["cache-control"] == "no-cache"
    res = client.get("/static/app.js")
    assert res.status_code == 200 and res.headers["cache-control"] == "no-cache"
    unchanged = client.get("/static/app.js", headers={"If-None-Match": res.headers["etag"]})
    assert unchanged.status_code == 304 and unchanged.headers["cache-control"] == "no-cache"


def test_publish_posts_once_per_change(conn, tmp_path, monkeypatch):
    from cfl import publish as P
    settings = _owned_league(conn, tmp_path)
    _player(conn, "f1", "Geekie, Morgan", "BOS", "C")
    conn.execute("INSERT INTO league_players VALUES (?, 'f1', 'C,F', 'T')", (LEAGUE,))
    conn.execute("INSERT INTO roster_items VALUES (?, 1, 't1', 'f1', 'C', 'ACTIVE')", (LEAGUE,))
    conn.execute("INSERT INTO contract_overrides VALUES (?, 'f1', '[5,5,5]', 'STANDARD', NULL, 'x')", (LEAGUE,))
    conn.commit()
    sent = []

    class Reply:
        status_code = 200

        def __init__(self, n):
            self.n = n

        def json(self):
            return {"ok": True, "rows": self.n}

    monkeypatch.setattr(P.requests, "post", lambda url, json, timeout: sent.append((url, json)) or Reply(len(json["values"])))
    assert P.publish(conn, settings)["One"]["published"]
    url, payload = sent[0]
    assert url == "https://example.invalid/publish" and payload["token"] == "tok" and payload["tab"] == "roster"
    assert any(r and r[0] == "Morgan Geekie" and r[2:5] == [5, 5, 5] for r in payload["values"])
    assert not P.publish(conn, settings)["One"]["published"] and len(sent) == 1      # unchanged: nothing sent
    assert P.publish(conn, settings, force=True)["One"]["published"] and len(sent) == 2
    assert P.publish_status(conn, settings)["configured"]


def test_contract_end_status_follows_the_july_1_age_rule():
    ctx = _ctx_with([])
    ctx.league = {"season_year": 2026}
    p = _with_contract(_p("x", ["C", "F"], "F", 1), [5, 5])          # last season 2027/28 -> July 1, 2028
    for born, status, age in (("2001-07-01", "UFA", 27),            # turns 27 on July 1: UFA
                              ("2001-07-02", "RFA", 26),            # turns 27 the next day: RFA
                              ("1995-01-01", "UFA", 33)):
        p.birth_date = born
        end = A.contract_end(ctx, p)
        assert (end["status"], end["age_at_expiry"], end["year"], end["after_season"]) == (status, age, 2028, "2027/28")
    p.birth_date = None
    assert A.contract_end(ctx, p)["status"] is None                  # unknown until a birth date is known
    p.contract_type, p.birth_date = "IN_SEASON_UFA", "2005-01-01"
    assert A.contract_end(ctx, p)["status"] == "UFA"                 # pickups go back to the auction
    assert A.contract_end(ctx, _p("y", ["C", "F"], "F", 1)) is None   # no contract, nothing to expire


def test_offseason_lists_expiring_contracts_by_status_and_moves_on_with_the_season():
    from cfl import offseason as O

    def signed(fid, salaries, born, team="t1", status="ACTIVE", contract_type="STANDARD"):
        p = _with_contract(_p(fid, ["C", "F"], "F", 1, team=team, status=status), salaries)
        p.birth_date, p.contract_type = born, contract_type
        return p

    ctx = _ctx_with([
        signed("young", [4], "2004-03-01"),                                   # 23 on July 1, 2027: RFA
        signed("vet", [10], "1990-01-01", team="t2"),                         # 37: UFA
        signed("pickup", [2], "2005-01-01", contract_type="IN_SEASON_UFA"),   # always a UFA
        signed("cheap", [1], "1992-01-01"),                                   # UFA, smaller salary
        signed("nodob", [3], None),                                           # status unknown
        signed("kid", [1], "2007-01-01", status="MINORS"),                    # prospects are listed too
        signed("later", [5, 9], "2003-01-01"),                                # ends after 2027/28
        signed("dropped", [6], "2000-01-01", team=None),                      # on no roster: nobody's to keep
        _p("unsigned", ["C", "F"], "F", 1),                                   # rostered, no contract on file
        _p("prospect", ["C", "F"], "F", 1, status="MINORS"),                  # unsigned prospect: not missing
    ])
    ctx.league = {"season_year": 2026}

    res = O.expiring(ctx)
    assert (res["year"], res["after_season"], res["ufa_age"]) == (2027, "2026/27", 27)
    assert [o["year"] for o in res["offseasons"]] == [2027, 2028, 2029, 2030, 2031]
    # RFAs, then UFAs, then unknown; the biggest final-season salary first within each.
    assert [(p["fantrax_id"], p["contract_end"]["status"]) for p in res["players"]] == [
        ("young", "RFA"), ("kid", "RFA"), ("vet", "UFA"), ("pickup", "UFA"), ("cheap", "UFA"), ("nodob", None)]
    assert res["players"][0]["fantasy_team_id"] == "t1" and res["players"][0]["contract_end"]["age_at_expiry"] == 23
    assert [p["fantrax_id"] for p in res["no_contract"]] == ["unsigned"]

    later = O.expiring(ctx, 2028)
    assert [(p["fantrax_id"], p["final_salary"]) for p in later["players"]] == [("later", 9)]   # last season's salary
    assert later["after_season"] == "2027/28"
    with pytest.raises(ValueError):
        O.expiring(ctx, 2026)

    # A season on, the next offseason is the one after it, with no code or setting to change.
    ctx.league = {"season_year": 2027}
    assert (O.expiring(ctx)["year"], O.expiring(ctx)["after_season"]) == (2028, "2027/28")


def test_bios_sync_fills_birth_dates_and_keeps_manual_ones(conn):
    from cfl.sync import sync_bios
    conn.execute("INSERT INTO teams (id, league_id, name) VALUES ('t1', ?, 'One')", (LEAGUE,))
    _player(conn, "vet", "Karlsson, Erik", "PIT", "D")
    _player(conn, "kid", "Surin, Yegor", "NSH", "RW")              # NHL.com spells it "Egor"
    _player(conn, "set", "Hand, Entered", "TOR", "C")
    conn.execute("UPDATE players SET nhl_id=1 WHERE fantrax_id='vet'")
    conn.execute("UPDATE players SET nhl_id=3, birth_date='2000-01-01', birth_date_source='manual' WHERE fantrax_id='set'")
    for fid in ("vet", "kid", "set"):
        conn.execute("INSERT INTO roster_items VALUES (?, 1, 't1', ?, 'F', 'ACTIVE')", (LEAGUE, fid))
    conn.commit()

    class FakeNHL:
        def bios(self, kind, season):
            return [{"playerId": 1, "birthDate": "1990-05-31"}, {"playerId": 3, "birthDate": "1999-09-09"}] \
                if kind == "skater" else []

        def search_players(self, q):
            return [{"playerId": "8484993", "name": "Egor Surin", "positionCode": "C", "teamAbbrev": "NSH",
                     "active": True}] if q == "Surin" else []

        def player_landing(self, nhl_id):
            return {"birthDate": "2006-08-01"} if nhl_id == 8484993 else {}

    result = sync_bios(conn, FakeNHL(), LEAGUE)
    dates = dict(conn.execute("SELECT fantrax_id, birth_date FROM players").fetchall())
    assert dates == {"vet": "1990-05-31", "kid": "2006-08-01", "set": "2000-01-01"}   # manual date kept
    assert result["looked_up"] == 1 and result["still_missing"] == []


def test_watch_flags_ir_returns_and_missed_games():
    from cfl.watch import compare

    def snap(roster):
        return {"roster": roster, "ranks": {"G": {"proj": 3}}}

    prev = snap({
        "ir": {"name": "Hurt Guy", "nhl_team": "TOR", "status": "INJURED_RESERVE", "gp": 4, "group": "F"},
        "sk": {"name": "Sitting Guy", "nhl_team": "BOS", "status": "ACTIVE", "gp": 6, "group": "F", "missed": 1},
        "g": {"name": "Backup", "nhl_team": "BOS", "status": "RESERVE", "gp": 2, "group": "G"},
        "ok": {"name": "Healthy", "nhl_team": "BOS", "status": "ACTIVE", "gp": 6, "group": "D"},
        "gone": {"name": "Traded", "nhl_team": "MTL", "status": "ACTIVE", "gp": 6, "group": "F"},
    })
    cur = snap({
        "ir": {"name": "Hurt Guy", "nhl_team": "TOR", "status": "INJURED_RESERVE", "gp": 5, "group": "F"},
        "sk": {"name": "Sitting Guy", "nhl_team": "BOS", "status": "ACTIVE", "gp": 6, "group": "F"},
        "g": {"name": "Backup", "nhl_team": "BOS", "status": "RESERVE", "gp": 2, "group": "G"},
        "ok": {"name": "Healthy", "nhl_team": "BOS", "status": "RESERVE", "gp": 7, "group": "D"},
        "new": {"name": "Pickup", "nhl_team": "TOR", "status": "ACTIVE", "gp": 1, "group": "F"},
    })
    cur["ranks"]["G"]["proj"] = 5
    out = compare(prev, cur, {"BOS": 2, "TOR": 1})
    kinds = {(a["kind"], a["player"]) for a in out["alerts"]}
    assert kinds == {("activate", "Hurt Guy"), ("missing", "Sitting Guy")}   # backup goalie: 2 < 5
    assert cur["roster"]["sk"]["missed"] == 3 and cur["roster"]["ok"]["missed"] == 0
    assert "Healthy: ACTIVE -> RESERVE" in out["changes"]
    assert "Added Pickup (ACTIVE)" in out["changes"] and "Dropped or traded Traded" in out["changes"]
    assert out["rank_moves"] == [{"category": "G", "from": 3, "to": 5}]
    assert compare(None, snap({}), {})["alerts"] == []


def test_watch_lineup_check_finds_idle_starters_and_benched_players():
    from cfl.watch import lineup_check

    players = [
        _p("c1", ["C", "F"], "F", 5),                       # active, team idle today
        _p("c2", ["C", "F"], "F", 4, status="RESERVE"),     # benched, team plays
        _p("d1", ["D"], "D", 1),                            # active, plays
        _p("d2", ["D"], "D", 9, status="INJURED_RESERVE"),  # never a starter
    ]
    for p, team in zip(players, ("BOS", "TOR", "TOR", "TOR")):
        p.nhl_team = team
    ctx = _ctx_with(players)
    out = lineup_check(ctx, "t1", {"TOR"})
    assert (out["starts"], out["possible_starts"]) == (1, 2)
    assert out["start"] == ["c2"] and out["sit"] == ["c1"]
    players[1].roster_status = "ACTIVE"
    assert lineup_check(ctx, "t1", {"TOR"})["possible_starts"] == lineup_check(ctx, "t1", {"TOR"})["starts"] == 2
    assert lineup_check(ctx, "t1", set())["games_today"] is False


def _entry(team_id, salaries=(), contract_type="STANDARD"):
    return {"team_id": team_id, "salaries": list(salaries), "contract_type": contract_type}


def test_changes_diff_logs_players_added_dropped_and_traded():
    from cfl.changes import diff
    old = {"f1": _entry("t1"), "f2": _entry("t1"), "f3": _entry("t2")}
    new = {"f1": _entry("t1"), "f2": _entry("t2"), "f4": _entry("t1")}
    assert diff(old, new) == [
        {"player_id": "f2", "team_id": "t2", "kind": "traded", "old_value": "t1", "new_value": "t2"},
        {"player_id": "f3", "team_id": "t2", "kind": "dropped", "old_value": "t2", "new_value": None},
        {"player_id": "f4", "team_id": "t1", "kind": "added", "old_value": None, "new_value": "t1"},
    ]


@pytest.mark.parametrize("was, now, kind", [
    (_entry("t1", [5, 5]), _entry("t1", [7, 7]), "salary"),         # this season's salary
    (_entry("t1"), _entry("t1", [2]), "salary"),                    # a first contract
    (_entry("t1", [5, 5]), _entry("t1", [5, 5, 5]), "contract"),    # one more season
    (_entry("t1", [5, 5]), _entry("t1", [5, 7]), "contract"),       # a later season's salary
    (_entry("t1", [3]), _entry("t1", [3], "ELC"), "contract"),      # the type
])
def test_changes_diff_tells_a_salary_change_from_other_contract_changes(was, now, kind):
    from cfl.changes import diff
    assert diff({"f1": was}, {"f1": now}) == [{
        "player_id": "f1", "team_id": "t1", "kind": kind,
        "old_value": {"salaries": was["salaries"], "contract_type": was["contract_type"]},
        "new_value": {"salaries": now["salaries"], "contract_type": now["contract_type"]}}]


def test_changes_record_saves_a_baseline_then_logs_what_changed_since(conn, tmp_path):
    from cfl import changes
    from cfl.db import rows
    conn.execute("INSERT INTO teams (id, league_id, name) VALUES ('t1', ?, 'One')", (LEAGUE,))
    _player(conn, "f1", "Daccord, Joey", "SEA", "G")
    conn.execute("INSERT INTO league_players VALUES (?, 'f1', 'G', 'T')", (LEAGUE,))
    conn.execute("INSERT INTO roster_items VALUES (?, 1, 't1', 'f1', 'G', 'ACTIVE')", (LEAGUE,))
    conn.execute("INSERT INTO contracts (league_id, team_id, sheet_name, player_id, section, salary, "
                 "years_remaining, salaries) VALUES (?, 't1', 'Joey Daccord', 'f1', 'ROSTER', 8, 2, '[8,8]')",
                 (LEAGUE,))
    conn.commit()
    settings = Settings(LEAGUE, None, tmp_path / "unused.db", 0.5, 5, tmp_path / "none.json")

    assert changes.record(conn, A.load_context(conn, settings), "Sync") == {"baseline": True, "changes": 0}
    conn.execute("UPDATE contracts SET salary=9, salaries='[9,9]' WHERE player_id='f1'")   # the sheet changed
    conn.commit()
    assert changes.record(conn, A.load_context(conn, settings), "Sync") == {"baseline": False, "changes": 1}
    (logged,) = rows(conn, "SELECT * FROM player_changes")
    assert (logged["player_id"], logged["team_id"], logged["kind"], logged["source"]) == ("f1", "t1", "salary", "Sync")
    assert json.loads(logged["new_value"]) == {"salaries": [9, 9], "contract_type": "STANDARD"}


def test_every_sync_ends_by_recording_player_changes(conn, tmp_path, monkeypatch):
    import cfl.sync as sync
    monkeypatch.setattr(sync, "sync_standings", lambda conn, fx: {"teams": 0})
    settings = Settings(LEAGUE, None, tmp_path / "unused.db", 0.5, 5, tmp_path / "none.json")

    results = sync.sync_all(conn, settings, ["standings"])
    assert [(r["resource"], r["status"]) for r in results] == [("standings", "ok"), ("changes", "ok")]
    assert results[-1]["detail"] == {"baseline": True, "changes": 0}


def test_a_failed_sync_step_is_logged_and_the_next_one_still_runs(conn):
    from cfl.db import rows
    from cfl.sync import _logged
    failed = _logged(conn, "stats", lambda: conn.execute("SELECT * FROM no_such_table").fetchall())
    ok = _logged(conn, "match", lambda: {"matched": 0})
    assert (failed["status"], ok["status"]) == ("error", "ok")
    assert [r["status"] for r in rows(conn, "SELECT status FROM sync_log ORDER BY id")] == ["error", "ok"]


def test_copy_database_replaces_every_table_and_numbers_new_sync_runs_after_the_old(conn, tmp_path):
    from cfl.db import copy_database, rows
    from cfl.sync import _logged
    src = connect(tmp_path / "src.db")
    _player(src, "f1", "Geekie, Morgan", "BOS", "C")
    src.execute("INSERT INTO contract_overrides VALUES (?, 'f1', '[5,5]', 'STANDARD', 'kept', 'x')", (LEAGUE,))
    _logged(src, "rosters", lambda: {"roster_items": 1})
    src.commit()

    counts = copy_database(src, conn)
    assert (counts["players"], counts["contract_overrides"], counts["sync_log"]) == (1, 1, 1)
    assert counts["league"] == 0 and not rows(conn, "SELECT * FROM league")      # replaced, not merged
    assert rows(conn, "SELECT name, adp FROM players") == [{"name": "Morgan Geekie", "adp": None}]
    assert rows(conn, "SELECT note FROM contract_overrides") == [{"note": "kept"}]
    _logged(conn, "stats", lambda: {})
    assert [r["id"] for r in rows(conn, "SELECT id FROM sync_log ORDER BY id")] == [1, 2]


def test_salary_sheets_can_come_from_an_environment_variable(tmp_path, monkeypatch):
    from cfl.config import get_settings
    from cfl.sync import load_salary_sources
    monkeypatch.setenv("CFL_SALARY_SHEETS", str(tmp_path / "missing.json"))
    monkeypatch.setenv("CFL_SALARY_SHEETS_JSON", '{"me": "https://example.invalid/a", "_comment": "x"}')
    assert load_salary_sources(get_settings()) == {"me": {"url": "https://example.invalid/a"}}


def test_a_database_url_switches_to_postgres_and_is_shown_without_its_password(monkeypatch):
    from cfl.config import get_settings
    url = "postgresql://app:s3cret@ep-x-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", url)
    s = get_settings()
    assert s.database == url and db.is_postgres(s.database)
    assert s.database_label == "postgres://ep-x-pooler.us-east-1.aws.neon.tech/neondb"
    monkeypatch.setenv("DATABASE_URL", "")
    assert get_settings().database == get_settings().db_path


# --------------------------------------------------------------------------- logins


def _logins(monkeypatch, tmp_path):
    """Turn on Sign in with Google with a fake Google: credential "x" is x@example.com. boss@ is the admin."""
    from cfl import auth

    def fake_verify(credential, client_id):
        assert client_id == "client-1"
        if credential == "forged":
            raise ValueError("signature doesn't match")
        return {"email": "{}@Example.com".format(credential), "name": credential.title(), "email_verified": True}

    monkeypatch.setattr(auth, "verify_google_credential", fake_verify)
    for name, value in (("GOOGLE_CLIENT_ID", "client-1"), ("CFL_SESSION_SECRET", "test-secret"),
                        ("CFL_ADMIN_EMAILS", "boss@example.com"), ("FANTRAX_LEAGUE_ID", LEAGUE),
                        ("CFL_DB_PATH", str(tmp_path / "t.db")), ("CFL_SALARY_SHEETS", str(tmp_path / "none.json"))):
        monkeypatch.setenv(name, value)


def _two_teams(conn):
    """t1 has Daccord ($8, on t1's sheet); t2 has Snuggerud ($5, on t2's sheet)."""
    for tid, name, fid, player, nhl, pos, salary in (("t1", "One", "f1", "Daccord, Joey", "SEA", "G", 8),
                                                     ("t2", "Two", "f2", "Snuggerud, Jimmy", "STL", "RW", 5)):
        conn.execute("INSERT INTO teams (id, league_id, name) VALUES (?, ?, ?)", (tid, LEAGUE, name))
        _player(conn, fid, player, nhl, pos)
        conn.execute("INSERT INTO league_players VALUES (?, ?, 'F', 'T')", (LEAGUE, fid))
        conn.execute("INSERT INTO roster_items VALUES (?, 1, ?, ?, 'F', 'ACTIVE')", (LEAGUE, tid, fid))
        conn.execute("INSERT INTO contracts (league_id, team_id, sheet_name, player_id, section, salary, "
                     "years_remaining, salaries) VALUES (?, ?, ?, ?, 'ROSTER', ?, 3, ?)",
                     (LEAGUE, tid, player, fid, salary, "[{0},{0},{0}]".format(salary)))
    conn.commit()


def _signed_in(credential):
    from fastapi.testclient import TestClient
    from cfl.api import app
    client = TestClient(app)
    return client, client.post("/auth/google", json={"credential": credential})


def test_a_gm_signs_in_waits_for_approval_then_sees_every_team_and_edits_only_their_own(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from cfl.api import app
    _logins(monkeypatch, tmp_path)
    _two_teams(conn)
    nobody = TestClient(app)
    assert nobody.get("/teams").status_code == 401
    assert nobody.get("/").status_code == 200                                         # the page, to sign in from
    assert nobody.get("/auth/config").json() == {"enabled": True, "google_client_id": "client-1"}

    gm, res = _signed_in("gm")
    assert res.json()["status"] == "pending" and gm.get("/auth/me").json()["status"] == "pending"
    assert gm.get("/teams").status_code == 403                                        # not until approved

    boss, res = _signed_in("boss")
    assert (res.json()["email"], res.json()["is_admin"], res.json()["status"]) == ("boss@example.com", True, "active")
    waiting = boss.get("/admin/users").json()["users"][0]
    assert (waiting["email"], waiting["status"]) == ("gm@example.com", "pending")
    assert boss.put("/admin/users/gm@example.com", json={"team_id": "t9"}).status_code == 404   # no such team
    boss.put("/admin/users/gm@example.com", json={"team_id": "t1", "role": "gm", "status": "active"})

    assert gm.get("/health").json()["my_team_id"] == "t1"
    assert {t["id"]: t["is_me"] for t in gm.get("/teams").json()} == {"t1": 1, "t2": 0}
    assert gm.get("/teams/t2/contracts").status_code == 200                           # every team, read-only
    assert gm.get("/players/f1/contract").json()["can_edit"] is True
    assert gm.get("/players/f2/contract").json()["can_edit"] is False
    assert gm.put("/players/f1/contract", json={"years": 2}).json()["effective"]["salaries"] == [8, 8]
    assert gm.put("/players/f2/contract", json={"years": 2}).status_code == 403       # someone else's player
    assert gm.delete("/players/f2/contract").status_code == 403
    assert gm.put("/players/f2/birth-date", json={"birth_date": "2000-01-01"}).status_code == 403
    for path in ("/analysis/teams/me/targets", "/analysis/teams/me/trade-partners", "/admin/users", "/publish",
                 "/offseason"):
        assert gm.get(path).status_code == 403, path                                  # the trade builder is the admin's
    assert gm.post("/analysis/trade", json={"give": ["f1"]}).status_code == 403
    assert boss.get("/offseason").json()["year"] == 2027                              # the offseason list is too
    # f1's edit left 2 seasons: it ends after 2027/28. f2's 3-year sheet contract ends after 2028/29.
    assert [p["fantrax_id"] for p in boss.get("/offseason", params={"year": 2028}).json()["players"]] == ["f1"]
    assert [p["fantrax_id"] for p in boss.get("/offseason", params={"year": 2029}).json()["players"]] == ["f2"]
    assert boss.get("/offseason", params={"year": 2026}).status_code == 400
    assert boss.put("/players/f2/contract", json={"years": 1}).status_code == 200     # the admin edits anyone

    assert boss.delete("/admin/users/boss@example.com").status_code == 400            # can't remove yourself
    boss.delete("/admin/users/gm@example.com")
    assert gm.get("/teams").status_code == 401                                        # removed: signed out


def test_gms_refresh_rosters_and_stats_but_only_the_admin_runs_a_full_sync(conn, tmp_path, monkeypatch):
    import cfl.api as api
    _logins(monkeypatch, tmp_path)
    _two_teams(conn)
    ran = []
    monkeypatch.setattr(api, "sync_all", lambda conn, settings, resources: ran.append(resources) or [])
    boss, _ = _signed_in("boss")
    boss.post("/admin/users", json={"email": " GM@example.com ", "team_id": "t2"})   # added before they sign in
    gm, res = _signed_in("gm")
    assert (res.json()["status"], res.json()["team_id"]) == ("active", "t2")          # straight in

    quick = {"resources": ["rosters", "standings", "matchups", "stats", "match", "bios"]}
    assert gm.post("/sync", params=quick).status_code == 200
    assert gm.post("/sync").status_code == 403
    assert gm.post("/sync", params={"resources": ["salaries"]}).status_code == 403
    assert boss.post("/sync").status_code == 200
    assert ran == [quick["resources"], None]

    assert gm.post("/auth/logout").status_code == 200
    assert gm.get("/teams").status_code == 401


def _rosters_synced(conn, minutes_ago):
    started = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    conn.execute("INSERT INTO sync_log (resource, started_at, status) VALUES ('rosters', ?, 'ok')",
                 (started.isoformat(timespec="seconds"),))
    conn.commit()


def test_a_refresh_waits_ten_minutes_after_the_last_one_league_wide(conn, tmp_path, monkeypatch):
    import cfl.api as api
    _logins(monkeypatch, tmp_path)
    _two_teams(conn)
    monkeypatch.setattr(api, "sync_all", lambda conn, settings, resources: [])
    boss, _ = _signed_in("boss")
    boss.post("/admin/users", json={"email": "gm@example.com", "team_id": "t2"})
    gm, _ = _signed_in("gm")
    quick = {"resources": ["rosters", "standings", "matchups", "stats", "match", "bios"]}

    _rosters_synced(conn, minutes_ago=11)
    assert gm.post("/sync", params=quick).status_code == 200
    _rosters_synced(conn, minutes_ago=9)
    assert gm.post("/sync", params=quick).status_code == 429
    assert boss.post("/sync", params=quick).status_code == 429     # admins wait too
    assert boss.post("/sync").status_code == 200                   # a full sync never waits


def test_health_says_how_long_until_the_next_refresh(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from cfl.api import app
    monkeypatch.setenv("CFL_DB_PATH", str(tmp_path / "t.db"))
    client = TestClient(app)                                     # logins off: the local admin
    assert client.get("/health").json()["refresh_wait_seconds"] == 0
    _rosters_synced(conn, minutes_ago=9)
    assert 0 < client.get("/health").json()["refresh_wait_seconds"] <= 60


def test_sign_in_refuses_forged_tokens_and_cookies_and_writes_from_other_sites(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from cfl import auth
    from cfl.api import app
    _logins(monkeypatch, tmp_path)
    _two_teams(conn)
    _, res = _signed_in("forged")
    assert res.status_code == 401
    stranger = TestClient(app)
    for cookie in ("not-a-session", auth.make_session("boss@example.com", "someone-elses-secret")):
        stranger.cookies.set(auth.COOKIE, cookie)
        assert stranger.get("/teams").status_code == 401

    boss, _ = _signed_in("boss")
    elsewhere = {"Origin": "https://evil.example"}
    assert boss.put("/players/f1/contract", json={"years": 1}, headers=elsewhere).status_code == 403
    assert boss.post("/auth/google", json={"credential": "boss"}, headers=elsewhere).status_code == 403
    assert boss.put("/players/f1/contract", json={"years": 1}, headers={"Origin": "http://testserver"}).status_code == 200


def test_a_vercel_deployment_without_logins_refuses_every_request(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from cfl.api import app
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("CFL_DB_PATH", str(tmp_path / "never-opened" / "t.db"))
    client = TestClient(app)
    res = client.get("/teams")
    assert res.status_code == 503 and "GOOGLE_CLIENT_ID" in res.json()["detail"]
    assert client.get("/auth/me").status_code == 503
    assert client.get("/").status_code == 200                                          # the page still loads, to say so
    assert "Privacy" in client.get("/privacy").text                                    # public, for Google's setup
    assert not (tmp_path / "never-opened").exists()                                    # refused before the database


def test_on_the_site_teams_come_from_each_users_row_not_fantrax_my_team_id(conn, tmp_path, monkeypatch):
    _logins(monkeypatch, tmp_path)
    monkeypatch.setenv("FANTRAX_MY_TEAM_ID", "t2")                  # the CLI's default; the site ignores it
    _two_teams(conn)
    boss, res = _signed_in("boss")
    assert res.json()["team_id"] is None and boss.get("/health").json()["my_team_id"] is None
    assert boss.get("/admin/users").json()["users"][0]["team_id"] is None
    boss.put("/admin/users/boss@example.com", json={"team_id": "t1", "role": "admin", "status": "active"})
    assert boss.get("/health").json()["my_team_id"] == "t1"           # linked in the Admin tab
    assert {t["id"]: t["is_me"] for t in boss.get("/teams").json()} == {"t1": 1, "t2": 0}


def test_contract_edits_are_logged_with_who_made_them_and_listed_newest_first(conn, tmp_path, monkeypatch):
    from cfl import changes
    from cfl.config import get_settings
    _logins(monkeypatch, tmp_path)
    _two_teams(conn)
    changes.record(conn, A.load_context(conn, get_settings()), "Sync")    # the baseline
    boss, _ = _signed_in("boss")
    boss.post("/admin/users", json={"email": "gm@example.com", "team_id": "t2"})
    gm, _ = _signed_in("gm")

    boss.put("/players/f1/contract", json={"years": 2})
    gm.put("/players/f2/contract", json={"years": 1})
    boss.delete("/players/f1/contract")
    listed = gm.get("/changes").json()
    assert [(c["player_name"], c["team_name"], c["kind"], c["source"]) for c in listed] == [
        ("Joey Daccord", "One", "contract", "Boss"), ("Jimmy Snuggerud", "Two", "contract", "Gm"),
        ("Joey Daccord", "One", "contract", "Boss")]
    assert listed[0]["old_value"] == {"salaries": [8, 8], "contract_type": "STANDARD"}
    assert [c["player_name"] for c in gm.get("/changes", params={"team_id": "t2"}).json()] == ["Jimmy Snuggerud"]
    assert gm.get("/changes", params={"kind": "salary"}).json() == []


def test_the_me_salary_sheet_key_needs_fantrax_my_team_id(conn):
    from cfl.sync import _resolve_team
    conn.execute("INSERT INTO teams (id, league_id, name) VALUES ('t1', ?, 'Skullets')", (LEAGUE,))
    assert _resolve_team(conn, LEAGUE, "me", "t1") == "t1"
    assert _resolve_team(conn, LEAGUE, "Skull", None) == "t1"          # a name works without it
    with pytest.raises(ValueError, match="FANTRAX_MY_TEAM_ID"):
        _resolve_team(conn, LEAGUE, "me", None)


CONTRACT_SHEET = [
    ["", "", "", "", "", "", "Salary"],
    ["Player", "Team", "Pos", "Owner", "Birthday", "Type", "26-27", "27-28", "28-29", "Notes"],
    ["Matthew Tkachuk", "FLA", "F", "Adam", "", "UFA Draft", "$17", "$17", "UFA", "2026 auction"],
    ["Jake Guentzel", "TBL", "F", "Adam", "", "UFA Draft", "$11", "", "", "$11 x ? (length not posted)"],
    ["Matthew Savoie", "EDM", "F", "Adam", "2004-01-01", "ELC", "$3", "RFA", "", ""],     # Fantrax: Matt Savoie
    ["Joey Daccord", "SEA", "G", "Adam", "", "UFA Draft", "$3", "$3", "UFA", ""],           # app: 5 years
    ["Lane Hutson", "MTL", "D", "Adam", "", "ELC", "$3", "RFA", "", ""],                    # app: same, no type
    ["Andrei Vasilevsky", "TBL", "G", "Free agent", "1994-07-25", "Free Agent", "", "", "", "stale copy"],
    ["Andrei Vasilevskiy", "TBL", "G", "Adam", "1994-07-25", "UFA Draft", "$23", "UFA", "", ""],
    ["Josh Manson", "COL", "D", "Adam", "", "In-Season UFA", "$1", "UFA", "", ""],          # a free agent in Fantrax
    ["Ivan Demidov", "MTL", "F", "Adam", "", "ELC", "$5", "RFA", "", ""],                   # ELCs are $3
    ["Gavin McKenna", "TOR", "F", "Adam", "", "Prospect", "", "", "", "unsigned"],
]


def test_parse_contract_sheet_reads_seasons_until_the_expiry_marker():
    from cfl.reconcile import parse_contract_sheet
    entries = {e.name: e for e in parse_contract_sheet(CONTRACT_SHEET, 2026)}
    assert entries["Matthew Tkachuk"].salaries == [17, 17] and entries["Matthew Tkachuk"].length_known
    assert entries["Jake Guentzel"].salaries == [11] and not entries["Jake Guentzel"].length_known
    assert entries["Matthew Savoie"].contract_type == "ELC" and entries["Matthew Savoie"].row == 5
    assert entries["Josh Manson"].contract_type == "IN_SEASON_UFA"
    assert entries["Andrei Vasilevsky"].owner is None and entries["Gavin McKenna"].salaries == []
    with pytest.raises(ValueError, match="No 2030-31 season column"):
        parse_contract_sheet(CONTRACT_SHEET, 2030)


def test_reconcile_imports_missing_contracts_and_lists_the_rest(conn, tmp_path):
    from cfl import reconcile as R
    conn.execute("INSERT INTO teams (id, league_id, name) VALUES ('t1', ?, 'Daigle')", (LEAGUE,))
    players = [("f1", "Tkachuk, Matthew", "FLA", "LW"), ("f2", "Guentzel, Jake", "TBL", "C"),
               ("f3", "Savoie, Matt", "EDM", "C"), ("f4", "Daccord, Joey", "SEA", "G"),
               ("f5", "Hutson, Lane", "MTL", "D"), ("f6", "Vasilevskiy, Andrei", "TBL", "G"),
               ("f7", "Manson, Josh", "COL", "D"), ("f8", "Demidov, Ivan", "MTL", "RW"),
               ("f9", "McKenna, Gavin", "TOR", "C")]
    for fid, name, team, pos in players:
        _player(conn, fid, name, team, pos)
        conn.execute("INSERT INTO league_players VALUES (?, ?, ?, ?)", (LEAGUE, fid, pos, "FA" if fid == "f7" else "T"))
        if fid != "f7":
            conn.execute("INSERT INTO roster_items VALUES (?, 1, 't1', ?, 'F', ?)",
                         (LEAGUE, fid, "MINORS" if fid == "f9" else "ACTIVE"))
    conn.execute("UPDATE players SET birth_date='2004-01-01' WHERE fantrax_id='f3'")
    conn.execute("UPDATE players SET birth_date='1994-07-25' WHERE fantrax_id='f6'")
    for fid, name, salaries in (("f4", "Joey Daccord", [3] * 5), ("f5", "Lane Hutson", [3])):
        conn.execute("INSERT INTO contracts (league_id, team_id, sheet_name, player_id, section, salary, "
                     "years_remaining, salaries) VALUES (?, 't1', ?, ?, 'ROSTER', ?, ?, ?)",
                     (LEAGUE, name, fid, salaries[0], len(salaries), json.dumps(salaries)))
    conn.commit()
    settings = Settings(LEAGUE, None, tmp_path / "unused.db", 0.5, 5, tmp_path / "none.json")
    ctx = A.load_context(conn, settings)

    report = R.reconcile(conn, ctx, CONTRACT_SHEET)
    assert report["owner_teams"] == {"Adam": "Daigle"}
    assert [(r["name"], r["sheet"], r["match"]) for r in report["imports"]] == [
        ("Andrei Vasilevskiy", [23], "name"),          # the stale free-agent copy is set aside
        ("Jake Guentzel", [11], "name"),
        ("Matt Savoie", [3], "birthday"),
        ("Matthew Tkachuk", [17, 17], "name")]
    assert {r["name"]: r["reasons"] for r in report["review"]} == {
        "Joey Daccord": ["years"],
        "Ivan Demidov": ["sheet contract breaks a league rule: An ELC is $3 a season for at most 3 seasons"]}
    assert report["review"][0]["app_source"] == "Daigle salary sheet"
    assert [r["name"] for r in report["type_only"]] == ["Lane Hutson"]
    assert [(r["name"], r["reasons"]) for r in report["roster_only"]] == [
        ("Josh Manson", ["sheet has him on Daigle, Fantrax on no team"])]
    assert report["unmatched_signed"] == [] and report["agree"] == 0
    assert {r["category"] for r in R.csv_rows(report)} == {"review", "type_only", "roster_only"}

    assert R.import_contracts(conn, LEAGUE, report["imports"]) == 4
    ctx = A.load_context(conn, settings)
    g, s = ctx.players["f2"], ctx.players["f3"]
    assert (g.contract_salaries, g.contract_source, s.contract_type) == ([11], "override", "ELC")
    note = conn.execute("SELECT note FROM contract_overrides WHERE player_id='f2'").fetchone()[0]
    assert note.startswith("From the CFL Contracts sheet, row 4 (length not posted; 1 season assumed)")
    again = R.reconcile(conn, ctx, CONTRACT_SHEET)
    assert again["imports"] == [] and again["agree"] == 4 and len(again["review"]) == 2


# --------------------------------------------------------------------------- draft picks


def _pick(year, rnd, team):
    return {"draft_year": year, "round": rnd, "original_team_id": team}


def _owned(picks):
    return sorted((p["draft_year"], p["round"], p["original_team_id"]) for p in picks)


def test_every_team_starts_with_five_picks_in_each_upcoming_draft_and_trades_move_them(conn):
    from cfl import picks as PK
    teams = ["t1", "t2", "t3"]
    start = PK.all_picks(conn, LEAGUE, teams, 2026)
    assert PK.draft_years(2026) == [2027, 2028, 2029]
    assert len(start) == 3 * 5 * 3 and all(p["owner_team_id"] == p["original_team_id"] for p in start)

    deal = PK.move(conn, LEAGUE, teams, 2026, "trade",
                   [("t1", "t2", [_pick(2027, 1, "t1")]), ("t2", "t1", [_pick(2028, 2, "t2"), _pick(2027, 5, "t2")])],
                   "for a 2nd and a 5th", "Boss")
    owner = {(p["draft_year"], p["round"], p["original_team_id"]): p["owner_team_id"]
             for p in PK.all_picks(conn, LEAGUE, teams, 2026)}
    assert (owner[(2027, 1, "t1")], owner[(2028, 2, "t2")], owner[(2027, 5, "t2")]) == ("t2", "t1", "t1")
    PK.move(conn, LEAGUE, teams, 2026, "rfa", [("t2", "t3", [_pick(2027, 1, "t1")])], "Lucas Raymond", "Boss")
    assert PK.latest_moves(conn, LEAGUE)[(2027, 1, "t1")]["to_team_id"] == "t3"      # passed on: t3 has t1's 1st

    with pytest.raises(ValueError, match="doesn't own"):                             # t1 sent it away
        PK.move(conn, LEAGUE, teams, 2026, "trade", [("t1", "t2", [_pick(2027, 1, "t1")])], None, "Boss")
    with pytest.raises(ValueError, match="no 2031 round 1"):                         # outside the drafts in range
        PK.move(conn, LEAGUE, teams, 2026, "trade", [("t1", "t2", [_pick(2031, 1, "t1")])], None, "Boss")
    with pytest.raises(ValueError, match="one way"):
        PK.move(conn, LEAGUE, teams, 2026, "rfa", [("t1", "t2", [_pick(2028, 1, "t1")]),
                                                   ("t2", "t1", [_pick(2028, 1, "t2")])], None, "Boss")
    with pytest.raises(ValueError, match="moved again"):                             # t1's 1st went on to t3
        PK.undo(conn, LEAGUE, deal)
    assert PK.all_picks(conn, LEAGUE, teams, 2027)[0]["draft_year"] == 2028           # a new season, a new draft


def test_gms_send_their_own_picks_see_every_teams_and_undo_their_own_deals(conn, tmp_path, monkeypatch):
    _logins(monkeypatch, tmp_path)
    _two_teams(conn)
    boss, _ = _signed_in("boss")
    boss.post("/admin/users", json={"email": "gm@example.com", "team_id": "t1"})
    gm, _ = _signed_in("gm")
    mine = gm.get("/teams/me/picks").json()
    assert (mine["can_edit"], mine["draft_years"], len(mine["owned"]), mine["sent"]) == (True, [2027, 2028, 2029], 15, [])
    assert gm.get("/teams/t2/picks").json()["can_edit"] is False                     # every team's, read-only

    trade = {"kind": "trade", "team_id": "t1", "other_team_id": "t2", "send": [_pick(2027, 1, "t1")]}
    assert gm.post("/picks/deals", json={**trade, "team_id": "t2", "other_team_id": "t1"}).status_code == 403
    assert gm.post("/picks/deals", json={**trade, "receive": [_pick(2027, 2, "t2")]}).status_code == 403
    assert gm.post("/picks/deals", json={**trade, "kind": "rfa", "receive": [_pick(2027, 2, "t2")]}).status_code == 403
    assert gm.post("/picks/deals", json={**trade, "send": [_pick(2027, 1, "t2")]}).status_code == 400   # not t1's

    after = gm.post("/picks/deals", json={**trade, "note": "for Snuggerud"}).json()
    assert (len(after["owned"]), after["sent"][0]["owner_team_name"]) == (14, "Two")
    theirs = gm.get("/teams/t2/picks").json()
    got = [p for p in theirs["owned"] if p["original_team_id"] == "t1"]
    assert [(p["draft_year"], p["round"], p["last_move"]["from_team_name"], p["last_move"]["kind"]) for p in got] == [
        (2027, 1, "One", "trade")]
    deal = theirs["deals"][0]
    assert (deal["note"], deal["source"], deal["can_undo"]) == ("for Snuggerud", "Gm", True)

    boss.post("/picks/deals", json={"kind": "rfa", "team_id": "t2", "other_team_id": "t1",
                                    "send": [_pick(2028, 3, "t2")], "note": "Daccord"})       # the admin sends anyone's
    assert [d["can_undo"] for d in gm.get("/teams/me/picks").json()["deals"]] == [False, True]   # t2 sent the RFA comp
    picks = gm.get("/changes", params={"kind": "pick"}).json()
    assert [(c["pick"]["draft_year"], c["pick"]["round"], c["pick"]["move"], c["team_name"], c["source"]) for c in picks] == [
        (2028, 3, "rfa", "One", "Boss"), (2027, 1, "trade", "Two", "Gm")]
    assert gm.get("/changes", params={"kind": "salary"}).json() == []                 # picks only when asked for
    assert len(gm.get("/changes").json()) == 2

    assert gm.delete("/picks/deals/{}".format(picks[0]["pick"]["deal_id"])).status_code == 403   # t2 sent that one
    assert gm.delete("/picks/deals/{}".format(deal["deal_id"])).json() == {"ok": True, "picks": 1}
    assert len(gm.get("/teams/me/picks").json()["owned"]) == 16                       # own 15, plus t2's 3rd
    assert gm.delete("/picks/deals/{}".format(deal["deal_id"])).status_code == 404
