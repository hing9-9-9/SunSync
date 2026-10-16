"""Adapt a validated FR-06 Scene snapshot to the pure FR-01 engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from app.schemas.scene import Scene
from app.schemas.sunpath import (
    BlockerResponse,
    CandidatePathResponse,
    RegionPredictionResponse,
    RegionTimeResponse,
    SampleResultResponse,
    SolarPositionResponse,
    SunpathPredictionRequest,
    SunpathPredictionResponse,
)
from engine.fr01.models import (
    Cuboid,
    PredictionOptions,
    ProtectedRegion,
    Room,
    Site,
    Vector3,
    Window,
)
from engine.fr01.predictor import predict_region_exposure
from engine.fr01.solar import calculate_solar_positions, make_prediction_grid

MAX_SELECTED_SAMPLES = 10_000
MAX_INTERSECTION_CHECKS = 5_000_000


class SunpathValidationError(ValueError):
    """A request or stored Scene cannot be evaluated by the FR-01 MVP."""


@dataclass(frozen=True, slots=True)
class SceneGeometry:
    site: Site
    room: Room
    windows: tuple[Window, ...]
    occluders: tuple[Cuboid, ...]
    protected_regions: tuple[ProtectedRegion, ...]


def _vector(value: tuple[float, float, float]) -> Vector3:
    return cast(Vector3, value)


def adapt_scene(scene: Scene) -> SceneGeometry:
    """Copy final, validated FR-06 ENU geometry into immutable engine DTOs."""

    try:
        return SceneGeometry(
            site=Site(scene.site.latitude_deg, scene.site.longitude_deg, scene.site.timezone),
            room=Room(
                scene.room.room_id,
                tuple(_vector(vertex) for vertex in scene.room.floor_vertices_m),
                scene.room.height_m,
            ),
            windows=tuple(
                Window(
                    window.window_id,
                    window.room_id,
                    cast(
                        tuple[Vector3, Vector3, Vector3, Vector3],
                        tuple(_vector(vertex) for vertex in window.vertices_m),
                    ),
                    _vector(window.inward_normal),
                    _vector(window.u_axis),
                    _vector(window.v_axis),
                )
                for window in scene.windows
            ),
            occluders=tuple(
                Cuboid(
                    occluder.occluder_id,
                    occluder.room_id,
                    _vector(occluder.base_center_m),
                    _vector(occluder.dimensions_m),
                    occluder.yaw_deg_ccw_from_east,
                )
                for occluder in scene.occluders
            ),
            protected_regions=tuple(
                ProtectedRegion(
                    region.region_id,
                    region.room_id,
                    tuple(_vector(vertex) for vertex in region.vertices_m),
                    _vector(region.receiving_normal),
                    tuple(_vector(point) for point in region.sample_points_m),
                    region.host_occluder_id,
                )
                for region in scene.protected_regions
            ),
        )
    except ValueError as error:
        raise SunpathValidationError(f"scene geometry is not calculation-ready: {error}") from error


def _start_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise SunpathValidationError("start_time must be an ISO 8601 timestamp") from error
    if result.utcoffset() is None:
        raise SunpathValidationError("start_time must include a timezone offset")
    return result


def _selected_regions(
    geometry: SceneGeometry, requested_ids: list[str] | None
) -> tuple[ProtectedRegion, ...]:
    if requested_ids is None:
        result = geometry.protected_regions
    else:
        requested = set(requested_ids)
        present = {region.region_id for region in geometry.protected_regions}
        missing = requested - present
        if missing:
            raise SunpathValidationError(
                f"protected_region_ids contains unknown ID: {min(missing)}"
            )
        result = tuple(
            region for region in geometry.protected_regions if region.region_id in requested
        )
    if not result:
        raise SunpathValidationError("scene has no protected regions to predict")
    return result


def _sample_response(sample: object) -> SampleResultResponse:
    from engine.fr01.models import SampleResult

    assert isinstance(sample, SampleResult)
    path = sample.candidate_path
    blocker = sample.blocker
    return SampleResultResponse(
        sample_index=sample.sample_index,
        point_m=sample.point_m,
        status="exposed" if sample.exposed else "not_exposed",
        reason=sample.reason.value,
        candidate_path=(
            CandidatePathResponse(
                window_id=path.window_id,
                window_hit_m=path.window_hit_m,
                sample_to_window_distance_m=path.sample_to_window_distance_m,
                reverse_direction_enu=path.reverse_direction_enu,
                incoming_direction_enu=path.incoming_direction_enu,
            )
            if path is not None
            else None
        ),
        blocker=(
            BlockerResponse(
                kind=blocker.kind,
                object_id=blocker.object_id,
                hit_m=blocker.hit_m,
                distance_m=blocker.distance_m,
            )
            if blocker is not None
            else None
        ),
    )


def predict_scene(scene: Scene, request: SunpathPredictionRequest) -> SunpathPredictionResponse:
    """Calculate a non-persistent prediction from one validated Scene snapshot."""

    geometry = adapt_scene(scene)
    regions = _selected_regions(geometry, request.protected_region_ids)
    start = _start_time(request.start_time)
    try:
        options = PredictionOptions(request.duration_minutes, request.interval_seconds)
        timestamps = make_prediction_grid(start, options)
    except ValueError as error:
        raise SunpathValidationError(str(error)) from error
    samples = sum(len(region.sample_points_m) for region in regions)
    if samples > MAX_SELECTED_SAMPLES:
        raise SunpathValidationError("selected regions exceed the sample-point limit")
    work = (
        len(timestamps)
        * samples
        * (
            len(geometry.windows)
            + len(geometry.occluders)
            + len(geometry.room.floor_vertices_m)
            + 2
        )
    )
    if work > MAX_INTERSECTION_CHECKS:
        raise SunpathValidationError("prediction exceeds the intersection-check limit")
    try:
        solar_positions = calculate_solar_positions(geometry.site, timestamps)
        predictions = tuple(
            predict_region_exposure(
                geometry.room,
                geometry.windows,
                geometry.occluders,
                region,
                solar_positions,
            )
            for region in regions
        )
    except ValueError as error:
        raise SunpathValidationError(str(error)) from error
    return SunpathPredictionResponse(
        scene_id=scene.scene_id,
        scene_revision=scene.revision,
        start_time=timestamps[0],
        end_time=timestamps[-1],
        interval_seconds=request.interval_seconds,
        timestamps=list(timestamps),
        solar_positions=[
            SolarPositionResponse(
                timestamp=position.timestamp,
                elevation_deg=position.elevation_deg,
                azimuth_deg_true=position.azimuth_deg_true,
                sun_direction_enu=position.sun_direction_enu,
            )
            for position in solar_positions
        ],
        regions=[
            RegionPredictionResponse(
                region_id=prediction.region_id,
                first_predicted_exposure_time=prediction.first_predicted_exposure_time,
                exposed_at_start=prediction.exposed_at_start,
                timeline=[
                    RegionTimeResponse(
                        timestamp=entry.timestamp,
                        status="exposed" if entry.exposed else "not_exposed",
                        exposed_sample_count=sum(sample.exposed for sample in entry.samples),
                        samples=[_sample_response(sample) for sample in entry.samples],
                    )
                    for entry in prediction.timeline
                ],
            )
            for prediction in predictions
        ],
        assumptions=[
            "Geometric direct-path prediction only; DNI, weather, and optics are excluded.",
            "Exposure is evaluated only at requested sampled timestamps and sample points.",
            "Exterior shading, reflections, and refraction are excluded.",
        ],
    )
