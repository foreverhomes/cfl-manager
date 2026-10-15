"""Offline tests for name matching, rate blending, lineups and need weights."""

import sqlite3
from pathlib import Path

import pytest

from cfl import analysis as A
from cfl.config import Settings
from cfl.db import connect, upsert
from cfl.sync import match_players, normalize_name, split_fantrax_name

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
