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


def _ctx_with(players, cats=("G", "Hit")):
    categories = [A.Category(c, c, "SKATER", 1.0, False) for c in cats]
    return A.Context(LEAGUE, {}, categories, {"t1": "One", "t2": "Two", "t3": "Three"}, 1,
                     {p.fantrax_id: p for p in players}, {"C": 1, "D": 1, "F": 1, "G": 0},
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
