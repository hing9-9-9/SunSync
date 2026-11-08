"""First FR-01 P50 implementation: preprocessing, fitting, and leakage guards."""

from __future__ import annotations

import csv
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from app.nowcast.kma_linear_model import LinearQuantileRegressor, enforce_monotonic_quantiles
from app.nowcast.kma_ml import dsr_to_dni_physics
from app.nowcast.kma_model_dataset import (
    FINAL_TEST_START,
    KmaInputPoint,
    OpenMeteoPoint,
    assert_training_data_excludes_final_test,
    build_10min_dataset,
    build_model_dataset,
    load_kma_points,
    load_open_meteo_points,
    split_development_rows,
    validate_no_leakage,
)

from engine.fr01.models import Site

SITE = Site(37.5714, 126.9658, "Asia/Seoul")


def _kma_points() -> list[KmaInputPoint]:
    start = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
    return [
        KmaInputPoint(start + timedelta(minutes=10 * index), 450.0 + index, 30.0)
        for index in range(7)
    ]


def _open_meteo_points() -> list[OpenMeteoPoint]:
    start = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
    return [
        OpenMeteoPoint(
            snapshot_available_at=start - timedelta(minutes=5),
            issued_at=start - timedelta(hours=1),
            valid_time=start + timedelta(hours=index),
            dni_W_m2=500.0 + 20.0 * index,
            snapshot_id="snapshot-old",
        )
        for index in range(3)
    ]


def test_builder_uses_origin_as_input_and_future_dsr_only_as_target() -> None:
    kma = _kma_points()
    result = build_10min_dataset(kma, _open_meteo_points(), SITE)
    assert result.rows
    first = result.rows[0]
    assert first.prediction_origin_utc == "2026-10-19T00:00:00Z"
    assert first.input_kma_timestamp_utc == "2026-10-19T00:00:00Z"
    assert first.target_dsr_timestamp_utc == "2026-10-19T00:10:00Z"
    assert first.valid_time_utc == "2026-10-19T00:10:00Z"
    assert first.dsr_input_W_m2 == 450.0
    assert first.cloud_cover_input_pct == 30.0
    assert first.reference_type == "dsr_proxy"
    assert first.cloud_patch_used is False
    assert first.advection_impact == 0.0
    validate_no_leakage(result.rows)


def test_builder_rejects_future_snapshot_and_uses_latest_available_snapshot() -> None:
    kma = _kma_points()
    origin = kma[0].timestamp
    points = _open_meteo_points()
    points.extend(
        OpenMeteoPoint(
            snapshot_available_at=origin + timedelta(minutes=1),
            issued_at=origin,
            valid_time=origin + timedelta(hours=index),
            dni_W_m2=999.0,
            snapshot_id="snapshot-future",
        )
        for index in range(3)
    )
    result = build_10min_dataset(kma, points, SITE)
    assert result.rows[0].open_meteo_snapshot_available_at_utc == "2026-10-18T23:55:00Z"
    assert result.rows[0].open_meteo_dni_W_m2 != 999.0


def test_builder_maps_preceding_hour_mean_without_linear_interpolation() -> None:
    result = build_10min_dataset(_kma_points(), _open_meteo_points(), SITE)
    first = result.rows[0]
    assert first.valid_time_utc == "2026-10-19T00:10:00Z"
    assert first.open_meteo_dni_W_m2 == 520.0


@pytest.mark.parametrize("horizon_minutes", [10, 30, 60])
def test_builder_uses_requested_horizon_without_future_inputs(horizon_minutes: int) -> None:
    result = build_model_dataset(
        _kma_points(),
        _open_meteo_points(),
        SITE,
        horizon_minutes=horizon_minutes,
    )
    assert result.rows
    first = result.rows[0]
    expected_valid = datetime(2026, 10, 19, 0, 0, tzinfo=UTC) + timedelta(minutes=horizon_minutes)
    assert first.horizon_minutes == horizon_minutes
    assert first.valid_time_utc == expected_valid.isoformat().replace("+00:00", "Z")
    assert first.target_dsr_timestamp_utc == first.valid_time_utc
    assert first.input_kma_timestamp_utc == first.prediction_origin_utc
    validate_no_leakage(result.rows)


def test_builder_rejects_unsupported_horizon() -> None:
    with pytest.raises(ValueError, match="horizon_minutes must be one of"):
        build_model_dataset(
            _kma_points(),
            _open_meteo_points(),
            SITE,
            horizon_minutes=20,
        )


def test_leakage_validator_fails_on_future_kma_input() -> None:
    row = build_10min_dataset(_kma_points(), _open_meteo_points(), SITE).rows[0]
    leaked = replace(row, input_kma_timestamp_utc=row.valid_time_utc)
    with pytest.raises(ValueError, match="future KMA input"):
        validate_no_leakage([leaked])


def test_leakage_validator_fails_on_future_open_meteo_snapshot() -> None:
    row = build_10min_dataset(_kma_points(), _open_meteo_points(), SITE).rows[0]
    leaked = replace(row, open_meteo_snapshot_available_at_utc=row.valid_time_utc)
    with pytest.raises(ValueError, match="future Open-Meteo snapshot"):
        validate_no_leakage([leaked])


def test_loader_requires_explicit_dsr_unit_assumption(tmp_path: Path) -> None:
    path = tmp_path / "kma.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "timestamp_utc",
                "dsr_raw",
                "cloud_cover_pct",
                "dsr_unit",
                "unit_confirmed",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "timestamp_utc": "2026-10-19T00:00:00Z",
                "dsr_raw": "400",
                "cloud_cover_pct": "30",
                "dsr_unit": "unknown",
                "unit_confirmed": "false",
            }
        )
    with pytest.raises(ValueError, match="unit is not confirmed"):
        load_kma_points(path)
    assert load_kma_points(path, assume_dsr_w_m2=True)[0].dsr_W_m2 == 400.0


def test_open_meteo_loader_enforces_seven_hour_availability_floor(tmp_path: Path) -> None:
    path = tmp_path / "open_meteo.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "snapshot_id",
                "snapshot_available_at_utc",
                "issued_at_utc",
                "valid_time_utc",
                "open_meteo_dni_W_m2",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "snapshot_id": "run-00",
                "snapshot_available_at_utc": "2026-10-19T06:00:00Z",
                "issued_at_utc": "2026-10-19T00:00:00Z",
                "valid_time_utc": "2026-10-19T08:00:00Z",
                "open_meteo_dni_W_m2": "500",
            }
        )
    point = load_open_meteo_points(path)[0]
    assert point.snapshot_available_at == datetime(2026, 10, 19, 7, 0, tzinfo=UTC)


def test_dsr_conversion_uses_pvlib_erbs_result() -> None:
    timestamp = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)
    kt, kd, dni = dsr_to_dni_physics(
        dsr_W_m2=500.0,
        clear_sky_dni=900.0,
        solar_zenith_deg=50.0,
        timestamp=timestamp,
    )
    assert kt == pytest.approx(0.5647385388863991)
    assert kd == pytest.approx(259.02401167 / 500.0)
    assert dni == pytest.approx(374.89208675)


def test_p50_model_fits_linear_data() -> None:
    rng = np.random.default_rng(42)
    x = np.column_stack(
        (
            np.ones(100),
            rng.uniform(0.1, 0.9, 100),
            rng.uniform(0.1, 0.9, 100),
            rng.uniform(0.0, 1.0, 100),
            np.zeros(100),
        )
    )
    y = np.clip(0.05 + 0.55 * x[:, 1] + 0.25 * x[:, 2] + 0.10 * x[:, 3], 0, 1.1)
    model = LinearQuantileRegressor(0.5)
    result = model.fit(x, y)
    prediction = model.predict_factors(x)
    assert result.weights.shape == (5,)
    assert np.mean(np.abs(prediction - y)) < 0.03


def test_raw_prediction_is_preserved_before_physical_clipping() -> None:
    model = LinearQuantileRegressor(0.5)
    model.weights_ = np.asarray([-0.2, 0.0, 0.0, 0.0, 0.0])
    x = np.asarray([[1.0, 0.0, 0.0, 0.0, 0.0]])
    assert model.predict_raw_factors(x)[0] == pytest.approx(-0.2)
    assert model.predict_factors(x)[0] == pytest.approx(0.0)


def test_custom_weight_bounds_are_validated_and_used() -> None:
    bounds = ((-0.75, 0.75), (0.0, 1.5), (0.0, 1.5), (-0.5, 0.5), (0.0, 0.0))
    model = LinearQuantileRegressor(0.9, bounds=bounds)
    assert model.bounds == bounds
    with pytest.raises(ValueError, match="five valid"):
        LinearQuantileRegressor(0.9, bounds=((-0.5, 0.5),))


def test_quantile_postprocessing_enforces_monotonic_order() -> None:
    raw_p10 = np.asarray([0.4, -0.1, 1.2])
    raw_p50 = np.asarray([0.3, 0.5, 0.9])
    raw_p90 = np.asarray([0.2, 0.4, 1.0])
    p10, p50, p90 = enforce_monotonic_quantiles(raw_p10, raw_p50, raw_p90)
    assert np.all((0.0 <= p10) & (p10 <= p50))
    assert np.all(p50 <= p90)
    assert np.all(p90 <= 1.05)


def test_frozen_final_test_period_is_rejected_from_training() -> None:
    with pytest.raises(ValueError, match="overlaps frozen final test set"):
        assert_training_data_excludes_final_test([KmaInputPoint(FINAL_TEST_START, 400.0, 30.0)])


@pytest.mark.parametrize("horizon_minutes,boundary_minute", [(10, 50), (30, 30), (60, 0)])
def test_fixed_split_excludes_target_on_next_split_boundary(
    horizon_minutes: int, boundary_minute: int
) -> None:
    base = build_10min_dataset(_kma_points(), _open_meteo_points(), SITE).rows[0]
    rows = []
    start = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    for day in range(14):
        origin = start + timedelta(days=day)
        valid = origin + timedelta(minutes=horizon_minutes)
        rows.append(
            replace(
                base,
                horizon_minutes=horizon_minutes,
                prediction_origin_utc=origin.isoformat().replace("+00:00", "Z"),
                input_kma_timestamp_utc=origin.isoformat().replace("+00:00", "Z"),
                valid_time_utc=valid.isoformat().replace("+00:00", "Z"),
                target_dsr_timestamp_utc=valid.isoformat().replace("+00:00", "Z"),
                open_meteo_valid_time_utc=valid.isoformat().replace("+00:00", "Z"),
            )
        )
    boundary_origin = datetime(2026, 10, 28, 23, boundary_minute, tzinfo=UTC)
    boundary_valid = boundary_origin + timedelta(minutes=horizon_minutes)
    rows.append(
        replace(
            base,
            horizon_minutes=horizon_minutes,
            prediction_origin_utc=boundary_origin.isoformat().replace("+00:00", "Z"),
            input_kma_timestamp_utc=boundary_origin.isoformat().replace("+00:00", "Z"),
            valid_time_utc=boundary_valid.isoformat().replace("+00:00", "Z"),
            target_dsr_timestamp_utc=boundary_valid.isoformat().replace("+00:00", "Z"),
            open_meteo_valid_time_utc=boundary_valid.isoformat().replace("+00:00", "Z"),
        )
    )
    splits = split_development_rows(tuple(rows))
    assert all(
        row.prediction_origin_utc != boundary_origin.isoformat().replace("+00:00", "Z")
        for row in splits.train
    )
    assert splits.boundaries["split_embargo_rule"] == "valid_time < next_split_start"
