"""Sampled-time direct-sun exposure prediction for FR-01."""

from __future__ import annotations

from datetime import datetime
from math import isfinite

from .geometry import (
    ANGULAR_EPSILON,
    DISTANCE_EPSILON_M,
    RayHit,
    WindowIntersectionKind,
    dot,
    first_room_boundary,
    intersect_cuboid,
    intersect_window,
    point_strictly_inside_cuboid,
)
from .models import (
    Blocker,
    CandidatePath,
    Cuboid,
    ExposureReason,
    PredictionOptions,
    ProtectedRegion,
    RegionPrediction,
    RegionTimeResult,
    Room,
    SampleResult,
    Site,
    SolarPosition,
    Vector3,
    Window,
)
from .solar import calculate_solar_positions, make_prediction_grid


def _blocker(hit: RayHit) -> Blocker:
    return Blocker(hit.kind, hit.object_id, hit.hit_m, hit.distance_m)


def _candidate_path(hit: RayHit, direction: Vector3) -> CandidatePath:
    return CandidatePath(
        window_id=hit.object_id,
        window_hit_m=hit.hit_m,
        sample_to_window_distance_m=hit.distance_m,
        reverse_direction_enu=direction,
        incoming_direction_enu=(-direction[0], -direction[1], -direction[2]),
    )


def _not_exposed(
    sample_index: int,
    point: Vector3,
    reason: ExposureReason,
    blocker: Blocker | None = None,
) -> SampleResult:
    return SampleResult(sample_index, point, False, reason, blocker=blocker)


def direct_irradiance_W_m2(
    sample: SampleResult,
    receiving_normal: Vector3,
    sun_direction_enu: Vector3,
    dni_W_m2: float | None,
) -> float | None:
    """Project request-scoped DNI onto a geometrically evaluated sample.

    A geometrically blocked sample is physically zero even when DNI is unknown;
    a clear path with unknown DNI remains unknown.
    """
    if not sample.exposed:
        return 0.0
    if dni_W_m2 is None:
        return None
    if not isfinite(dni_W_m2) or dni_W_m2 < 0:
        raise ValueError("dni_W_m2 must be finite and nonnegative")
    return dni_W_m2 * max(0.0, dot(receiving_normal, sun_direction_enu))


def _candidate_windows(
    point: Vector3, direction: Vector3, windows: tuple[Window, ...]
) -> tuple[list[RayHit], bool]:
    hits: list[RayHit] = []
    ambiguous = False
    for window in windows:
        result = intersect_window(point, direction, window)
        if result.kind is WindowIntersectionKind.HIT:
            assert result.hit is not None
            hits.append(result.hit)
        elif result.kind is WindowIntersectionKind.AMBIGUOUS:
            ambiguous = True
    return sorted(hits, key=lambda hit: (hit.distance_m, hit.object_id)), ambiguous


def evaluate_sample(
    sample_index: int,
    point: Vector3,
    region: ProtectedRegion,
    solar_position: SolarPosition,
    room: Room,
    windows: tuple[Window, ...],
    occluders: tuple[Cuboid, ...],
) -> SampleResult:
    """Classify one sample at one time using a ray toward the sun."""

    direction = solar_position.sun_direction_enu
    if solar_position.elevation_deg <= 0.0:
        return _not_exposed(sample_index, point, ExposureReason.SUN_BELOW_HORIZON)
    if dot(region.receiving_normal, direction) <= ANGULAR_EPSILON:
        return _not_exposed(sample_index, point, ExposureReason.BACK_FACING)
    inside = next(
        (cuboid for cuboid in occluders if point_strictly_inside_cuboid(point, cuboid)),
        None,
    )
    if inside is not None:
        raise ValueError(
            f"sample {sample_index} of {region.region_id} is inside occluder {inside.occluder_id}"
        )
    candidate_hits, window_ambiguous = _candidate_windows(point, direction, windows)
    if not candidate_hits:
        if not windows:
            return _not_exposed(sample_index, point, ExposureReason.NO_WINDOW)
        reason = (
            ExposureReason.BOUNDARY_AMBIGUOUS if window_ambiguous else ExposureReason.WINDOW_MISSED
        )
        return _not_exposed(sample_index, point, reason)

    boundary = first_room_boundary(point, direction, room)
    if boundary is None:
        return _not_exposed(sample_index, point, ExposureReason.ROOM_BLOCKED)
    if boundary.kind == "ambiguous":
        return _not_exposed(sample_index, point, ExposureReason.BOUNDARY_AMBIGUOUS)
    wall_candidates = [
        hit
        for hit in candidate_hits
        if abs(hit.distance_m - boundary.distance_m) <= DISTANCE_EPSILON_M
    ]
    if not wall_candidates:
        return _not_exposed(
            sample_index,
            point,
            ExposureReason.ROOM_BLOCKED,
            _blocker(boundary),
        )
    window_hit = wall_candidates[0]
    blockers = [
        hit
        for cuboid in occluders
        if (hit := intersect_cuboid(point, direction, cuboid)) is not None
        and hit.distance_m < window_hit.distance_m - DISTANCE_EPSILON_M
    ]
    if blockers:
        nearest = min(blockers, key=lambda hit: (hit.distance_m, hit.object_id))
        return _not_exposed(
            sample_index,
            point,
            ExposureReason.OCCLUDER_BLOCKED,
            _blocker(nearest),
        )
    return SampleResult(
        sample_index,
        point,
        True,
        ExposureReason.CLEAR_PATH,
        _candidate_path(window_hit, direction),
    )


def predict_region_exposure(
    room: Room,
    windows: tuple[Window, ...],
    occluders: tuple[Cuboid, ...],
    region: ProtectedRegion,
    solar_positions: tuple[SolarPosition, ...],
) -> RegionPrediction:
    """Aggregate sample results; any exposed sample exposes its region."""

    if region.room_id != room.room_id:
        raise ValueError("protected region must reference the supplied room")
    if any(window.room_id != room.room_id for window in windows):
        raise ValueError("window must reference the supplied room")
    if any(cuboid.room_id != room.room_id for cuboid in occluders):
        raise ValueError("occluder must reference the supplied room")
    timeline = tuple(
        RegionTimeResult(
            timestamp=position.timestamp,
            exposed=any(sample.exposed for sample in samples),
            samples=samples,
        )
        for position in solar_positions
        for samples in (
            tuple(
                evaluate_sample(
                    sample_index,
                    point,
                    region,
                    position,
                    room,
                    windows,
                    occluders,
                )
                for sample_index, point in enumerate(region.sample_points_m)
            ),
        )
    )
    first = next((entry.timestamp for entry in timeline if entry.exposed), None)
    return RegionPrediction(
        region_id=region.region_id,
        first_predicted_exposure_time=first,
        exposed_at_start=bool(timeline and timeline[0].exposed),
        timeline=timeline,
    )


def predict_future_exposure(
    site: Site,
    room: Room,
    windows: tuple[Window, ...],
    occluders: tuple[Cuboid, ...],
    region: ProtectedRegion,
    start_time: datetime,
    options: PredictionOptions | None = None,
) -> RegionPrediction:
    """Use Stage 1's UTC grid and pvlib positions for one region prediction."""

    positions = calculate_solar_positions(site, make_prediction_grid(start_time, options))
    return predict_region_exposure(room, windows, occluders, region, positions)
