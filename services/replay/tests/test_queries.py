"""Unit tests for the replay query builders — no live DB required.

Covers the nm->metre conversion and the positional-param construction for the
track + proximity SQL. The SQL strings themselves are asserted to carry the
load-bearing PostGIS clauses (geography cast + ST_DWithin) so a refactor that
silently drops the true-metres semantics fails here.
"""
from __future__ import annotations

import pytest

from replay.queries import (
    METERS_PER_NM,
    PROXIMITY_SQL,
    TRACK_SQL,
    nm_to_meters,
    proximity_params,
    track_params,
)


def test_nm_to_meters_exact():
    # 1 nm is defined as exactly 1852 m.
    assert nm_to_meters(1.0) == 1852.0
    assert nm_to_meters(0.5) == 926.0
    assert nm_to_meters(0.0) == 0.0
    assert METERS_PER_NM == 1852.0


def test_nm_to_meters_rejects_negative():
    with pytest.raises(ValueError):
        nm_to_meters(-1.0)


def test_proximity_params_order_and_window():
    # window_min is the FULL window -> half-window seconds is window_min*60/2.
    mmsi, ts, half_window_s, radius_m = proximity_params(
        mmsi=636091234, ts=1_700_000_000.0, radius_nm=0.5, window_min=30.0
    )
    assert mmsi == 636091234
    assert ts == 1_700_000_000.0
    assert half_window_s == pytest.approx(900.0)   # 30 min full -> ±15 min
    assert radius_m == pytest.approx(926.0)         # 0.5 nm -> metres


def test_track_params_order_and_validation():
    assert track_params(123, 10.0, 20.0) == (123, 10.0, 20.0)
    with pytest.raises(ValueError):
        track_params(123, 20.0, 10.0)   # to < from


def test_proximity_sql_uses_geography_dwithin():
    # The true-metres semantics MUST survive: geography cast + ST_DWithin.
    assert "ST_DWithin" in PROXIMITY_SQL
    assert "::geography" in PROXIMITY_SQL
    # Parameterised, never string-interpolated.
    for p in ("$1", "$2", "$3", "$4"):
        assert p in PROXIMITY_SQL


def test_track_sql_orders_ascending_and_is_parameterised():
    assert "ORDER BY ts ASC" in TRACK_SQL
    assert "WHERE mmsi = $1" in TRACK_SQL
    assert "to_timestamp($2)" in TRACK_SQL and "to_timestamp($3)" in TRACK_SQL
