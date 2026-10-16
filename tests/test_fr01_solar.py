"""Stage 1 regression tests for the pure-Python FR-01 solar module."""

from datetime import UTC, datetime, timedelta, timezone
from math import isclose, sqrt

import pytest

from engine.fr01 import (
    DEFAULT_DURATION_MINUTES,
    DEFAULT_INTERVAL_SECONDS,
    PredictionOptions,
    Site,
    calculate_solar_positions,
    make_prediction_grid,
    sun_direction_enu,
)


@pytest.mark.parametrize(
    ("elevation", "azimuth", "expected"),
    [
        (0, 0, (0, 1, 0)),
        (0, 90, (1, 0, 0)),
        (0, 180, (0, -1, 0)),
        (0, 270, (-1, 0, 0)),
        (90, 0, (0, 0, 1)),
    ],
)
def test_sun_direction_enu_cardinal_directions(elevation, azimuth, expected):
    result = sun_direction_enu(elevation, azimuth)
    assert result == pytest.approx(expected, abs=1e-12)
    assert sqrt(sum(component * component for component in result)) == pytest.approx(1.0)


def test_sun_direction_supports_night_and_horizon():
    assert sun_direction_enu(-30, 180) == pytest.approx((0, -sqrt(3) / 2, -0.5))
    assert sun_direction_enu(0, 45)[2] == pytest.approx(0.0)


def test_default_grid_has_25_utc_timestamps():
    start = datetime(2026, 10, 16, 12, tzinfo=timezone(timedelta(hours=9)))
    grid = make_prediction_grid(start)
    assert len(grid) == 25
    assert grid[0] == datetime(2026, 10, 16, 3, tzinfo=UTC)
    assert grid[-1] == datetime(2026, 10, 16, 5, tzinfo=UTC)
    assert grid[1] - grid[0] == timedelta(seconds=DEFAULT_INTERVAL_SECONDS)
    assert DEFAULT_DURATION_MINUTES == 120


def test_grid_includes_non_aligned_end_once_across_date_boundary():
    start = datetime(2026, 12, 31, 23, 58, tzinfo=UTC)
    grid = make_prediction_grid(start, PredictionOptions(duration_minutes=7, interval_seconds=300))
    assert grid == (
        start,
        start + timedelta(minutes=5),
        start + timedelta(minutes=7),
    )
    assert grid.count(start + timedelta(minutes=7)) == 1


@pytest.mark.parametrize(
    "start",
    [datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)],
)
def test_grid_rejects_naive_timestamps(start):
    with pytest.raises(ValueError, match="timezone offset"):
        make_prediction_grid(start)


@pytest.mark.parametrize(
    "options",
    [
        PredictionOptions(duration_minutes=1440, interval_seconds=1),
    ],
)
def test_grid_rejects_timestamp_count_over_limit(options):
    with pytest.raises(ValueError, match="timestamp limit"):
        make_prediction_grid(datetime(2026, 1, 1, tzinfo=UTC), options)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration_minutes": 0},
        {"duration_minutes": 1441},
        {"duration_minutes": True},
        {"interval_seconds": 0},
        {"interval_seconds": 3601},
        {"interval_seconds": 1.5},
    ],
)
def test_prediction_options_reject_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        PredictionOptions(**kwargs)


def test_equal_instants_with_different_offsets_produce_equal_positions():
    site = Site(latitude_deg=37.5, longitude_deg=127.0, timezone="Asia/Seoul")
    utc_time = datetime(2026, 6, 21, 3, tzinfo=UTC)
    seoul_time = datetime(2026, 6, 21, 12, tzinfo=timezone(timedelta(hours=9)))
    first = calculate_solar_positions(site, [utc_time])[0]
    second = calculate_solar_positions(site, [seoul_time])[0]
    assert first.timestamp == second.timestamp == utc_time
    assert first.elevation_deg == pytest.approx(second.elevation_deg, abs=1e-12)
    assert first.azimuth_deg_true == pytest.approx(second.azimuth_deg_true, abs=1e-12)
    assert first.sun_direction_enu == pytest.approx(second.sun_direction_enu, abs=1e-12)


def test_published_noaa_reference_fixture_for_solar_position():
    """NOAA calculator example: 2010-01-01 12:00 UTC, Greenwich, sea level.

    The expected geometric elevation and azimuth values were transcribed from
    NOAA's Solar Calculator (2024-02 access) and rounded to 0.1 degrees.
    pvlib uses NREL SPA, so this deliberately allows the source's rounding and
    model differences. Source: https://gml.noaa.gov/grad/solcalc/
    """

    site = Site(latitude_deg=51.4779, longitude_deg=0.0, timezone="Europe/London")
    position = calculate_solar_positions(site, [datetime(2010, 1, 1, 12, tzinfo=UTC)])[0]
    assert isclose(position.elevation_deg, 15.4, abs_tol=0.3)
    assert isclose(position.azimuth_deg_true, 179.1, abs_tol=0.5)
