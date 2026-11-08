"""Sampled-time Stage 2 predictor tests without HTTP or database dependencies."""

from datetime import UTC, datetime, timedelta
from math import sqrt

import pytest

from engine.fr01.models import (
    Cuboid,
    ExposureReason,
    ProtectedRegion,
    RegionTimeResult,
    Room,
    SolarPosition,
    Window,
)
from engine.fr01.predictor import (
    direct_irradiance_W_m2,
    evaluate_sample,
    exposure_intervals,
    predict_region_exposure,
)

SUN_SOUTH = (0.0, -1 / sqrt(2), 1 / sqrt(2))
SUN_NORTH = (0.0, 1 / sqrt(2), 1 / sqrt(2))


def room() -> Room:
    return Room("room", ((0, 0, 0), (6, 0, 0), (6, 4, 0), (0, 4, 0)), 3)


def window() -> Window:
    return Window(
        "south_window",
        "room",
        ((5, 0, 1), (1, 0, 1), (1, 0, 2.8), (5, 0, 2.8)),
        (0, 1, 0),
        (-1, 0, 0),
        (0, 0, 1),
    )


def region(samples=(), normal=(0, -1, 0)) -> ProtectedRegion:
    return ProtectedRegion(
        "region",
        "room",
        ((2.975, 2, 0.475), (3.025, 2, 0.475), (3.025, 2, 0.525), (2.975, 2, 0.525)),
        normal,
    )


def position(index: int, elevation: float, direction) -> SolarPosition:
    return SolarPosition(
        datetime(2026, 6, 1, 3, tzinfo=UTC) + timedelta(minutes=5 * index),
        elevation,
        180.0 if direction[1] < 0 else 0.0,
        direction,
    )


def test_evaluate_sample_returns_window_path_and_diagnostics():
    result = evaluate_sample(
        0, (3, 2, 0.5), region(), position(0, 45, SUN_SOUTH), room(), (window(),), ()
    )
    assert result.exposed
    assert result.reason is ExposureReason.CLEAR_PATH
    assert result.candidate_path is not None
    assert result.candidate_path.window_id == "south_window"
    assert result.candidate_path.window_hit_m == pytest.approx((3, 0, 2.5))
    assert result.candidate_path.incoming_direction_enu == pytest.approx(
        (0, 1 / sqrt(2), -1 / sqrt(2))
    )

    night = evaluate_sample(
        0, (3, 2, 0.5), region(), position(1, 0, SUN_SOUTH), room(), (window(),), ()
    )
    assert night.reason is ExposureReason.SUN_BELOW_HORIZON
    back = evaluate_sample(
        0, (3, 2, 0.5), region(), position(1, 45, SUN_NORTH), room(), (window(),), ()
    )
    assert back.reason is ExposureReason.BACK_FACING


def test_direct_irradiance_distinguishes_blocked_from_unknown_dni():
    clear = evaluate_sample(
        0, (3, 2, 0.5), region(), position(0, 45, SUN_SOUTH), room(), (window(),), ()
    )
    blocked = evaluate_sample(
        0, (3, 2, 0.5), region(), position(0, 0, SUN_SOUTH), room(), (window(),), ()
    )
    assert direct_irradiance_W_m2(clear, region().receiving_normal, SUN_SOUTH, None) is None
    assert direct_irradiance_W_m2(blocked, region().receiving_normal, SUN_SOUTH, None) == 0
    assert direct_irradiance_W_m2(
        clear, region().receiving_normal, SUN_SOUTH, 500
    ) == pytest.approx(500 / sqrt(2))


def test_occluder_blocks_only_its_sample_and_nearest_blocker_is_reported():
    blocking = Cuboid("near", "room", (3, 1, 0), (0.5, 0.5, 2), 0)
    farther = Cuboid("far", "room", (3, 0.3, 0), (0.5, 0.3, 2), 0)
    target = region(((3, 2, 0.5), (4.5, 2, 0.5)))
    prediction = predict_region_exposure(
        room(), (window(),), (farther, blocking), target, (position(0, 45, SUN_SOUTH),)
    )
    entry = prediction.timeline[0]
    assert not entry.exposed
    assert entry.samples[0].reason is ExposureReason.OCCLUDER_BLOCKED
    assert entry.samples[0].blocker is not None
    assert entry.samples[0].blocker.object_id == "near"


def test_predictor_timeline_first_sampled_exposure_and_multiple_periods():
    positions = (
        position(0, 45, SUN_NORTH),
        position(1, 45, SUN_SOUTH),
        position(2, 45, SUN_NORTH),
        position(3, 45, SUN_SOUTH),
    )
    prediction = predict_region_exposure(room(), (window(),), (), region(), positions)
    assert [entry.exposed for entry in prediction.timeline] == [
        False,
        True,
        False,
        True,
    ]
    assert not prediction.exposed_at_start
    assert prediction.first_predicted_exposure_time == positions[1].timestamp
    assert [(item.start_time, item.end_time) for item in prediction.exposure_intervals] == [
        (positions[1].timestamp, positions[1].timestamp),
        (positions[3].timestamp, positions[3].timestamp),
    ]


def test_exposure_intervals_covers_empty_edge_and_contiguous_grid_runs():
    timestamps = tuple(position(index, 45, SUN_SOUTH).timestamp for index in range(5))

    def timeline(exposed: tuple[bool, ...]) -> tuple[RegionTimeResult, ...]:
        return tuple(
            RegionTimeResult(timestamp, is_exposed, ())
            for timestamp, is_exposed in zip(timestamps, exposed, strict=True)
        )

    assert exposure_intervals(timeline((False, False, False, False, False))) == ()
    assert [
        (item.start_time, item.end_time)
        for item in exposure_intervals(timeline((True, True, True, False, False)))
    ] == [(timestamps[0], timestamps[2])]
    assert [
        (item.start_time, item.end_time)
        for item in exposure_intervals(timeline((False, False, True, True, True)))
    ] == [(timestamps[2], timestamps[4])]


def test_predictor_true_from_start_never_exposed_and_rejects_interior_sample():
    true_from_start = predict_region_exposure(
        room(), (window(),), (), region(), (position(0, 45, SUN_SOUTH),)
    )
    assert true_from_start.exposed_at_start
    assert true_from_start.first_predicted_exposure_time == true_from_start.timeline[0].timestamp
    never = predict_region_exposure(room(), (), (), region(), (position(0, 45, SUN_SOUTH),))
    assert not never.timeline[0].exposed
    assert never.timeline[0].samples[0].reason is ExposureReason.NO_WINDOW
    box = Cuboid("box", "room", (3, 2, 0), (1, 1, 2), 0)
    from engine.fr01.predictor import SampleInsideOccluderError

    with pytest.raises(SampleInsideOccluderError, match="inside occluder box") as caught:
        predict_region_exposure(
            room(),
            (window(),),
            (box,),
            region(((3, 2, 1),)),
            (position(0, 45, SUN_SOUTH),),
        )
    assert caught.value.sample_index == 0
    assert caught.value.region_id == "region"
    assert caught.value.occluder_id == "box"
