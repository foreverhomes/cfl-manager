"""Offline tests for name matching, rate blending, lineups and need weights."""

import sqlite3
from pathlib import Path

import pytest

from cfl import analysis as A
from cfl.config import Settings
from cfl.db import connect, upsert
from cfl.sync import match_players, match_sheet_names, normalize_name, parse_salary_sheet, split_fantrax_name

LEAGUE = "L1"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.db")
    upsert(c, "league", [{"id": LEAGUE, "name": "Test", "season_year": 2026, "scoring_type": "x",
                          "start_date": None, "end_date": None, "draft_type": None,
                          "roster_info": '{"positionConstraints": {"C": {"maxActive": 1}, "D": {"maxActive": 1},'
                                         ' "F": {"maxActive": 1}, "G": {"maxActive": 1}}}',
                          "playoffs": None, "draft_settings": None, "synced_at": None}], ["id"])
    c.commit()
    return c


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
        A.evaluate_trade(ctx, "t1", give=[], get=["b1"])                    # partner required


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
    assert cap["future"][0] == {"season": "2027/28", "committed": 20 + 8 + 2, "space": 20}   # IR returns
    assert A.cap_summary(ctx, "t2") is None
    assert [A.release_cost(p) for p in players] == [10, 5, 4, 0, 0]   # $3 or less is a free release


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
