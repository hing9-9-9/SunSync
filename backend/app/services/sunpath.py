"""Adapt a validated FR-06 Scene snapshot to the pure FR-01 engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Literal, cast

from sqlalchemy import Engine

from app.nowcast.selection import select_and_record
from app.schemas.scene import Scene
from app.schemas.sunpath import (
    BlockerResponse,
    CandidatePathResponse,
    ExposureIntervalResponse,
    FallbackReason,
    IrradianceResponse,
    RegionPredictionResponse,
    RegionTimeResponse,
    SampleResultResponse,
    SolarPositionResponse,
    SourceSnapshotResponse,
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
from engine.fr01.predictor import direct_irradiance_W_m2, predict_region_exposure
from engine.fr01.solar import calculate_solar_positions, make_prediction_grid
from engine.fr01.tiling import tile_centres

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


@dataclass(frozen=True, slots=True)
class DniValue:
    value: float | None
    source: Literal["scene_weather", "open_meteo", "pvlib_clear_sky", "unknown"]
    fallback_used: bool
    fallback_reason: str | None
    valid_time: datetime | None
    fetched_at: datetime | None
    nowcast_run_id: str | None = None
    prediction_method: str = "stage1_weather_selection"
    source_snapshot_id: str | None = None
    source_provider: str | None = None
    interval_start: datetime | None = None
    interval_end: datetime | None = None
    clear_sky_dni: float | None = None
    provenance: dict[str, object] | None = None


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


def _sample_response(
    sample: object, direct_irradiance_W_m2: float | None = None
) -> SampleResultResponse:
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
        direct_irradiance_W_m2=direct_irradiance_W_m2,
    )


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("timestamp must be an ISO 8601 timestamp") from error
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    return value.astimezone(UTC)


def _valid_dni(value: object) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError("DNI must be a finite nonnegative number")
    return float(value)


def _provenance_float(provenance: dict[str, object] | None, key: str) -> float | None:
    """Expose optional provider metadata without coercing booleans or strings."""

    value = provenance.get(key) if provenance else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _provenance_str(provenance: dict[str, object] | None, key: str) -> str | None:
    value = provenance.get(key) if provenance else None
    return value if isinstance(value, str) else None


def _scene_dni(
    scene: Scene, timestamp: datetime, received_at: datetime
) -> tuple[float | None, datetime | None, datetime | None] | str:
    """Return a Scene weather value, or the reason it cannot be trusted."""
    weather = scene.weather
    if weather is None:
        return "scene_weather_missing"
    try:
        if weather.unit != "W/m2" or weather.location_source != "scene.site":
            return "scene_weather_invalid"
        if weather.fetched_at is None:
            return "scene_weather_invalid"
        fetched_at = _utc(weather.fetched_at)
        if fetched_at > received_at:
            return "scene_weather_invalid"
        if (received_at - fetched_at).total_seconds() > weather.max_age_s:
            return "scene_weather_stale"
        target = _utc(timestamp)
        records = list(weather.records)
        if weather.temporal_meaning == "preceding_hour_mean":
            matches = [
                record
                for record in records
                if record.interval_start is not None
                and record.interval_end is not None
                and _utc(record.interval_start) <= target < _utc(record.interval_end)
            ]
            if len(matches) != 1:
                return "scene_weather_out_of_coverage"
            record = matches[0]
            value = _valid_dni(record.dni_W_m2)
            if value is None:
                return "scene_weather_invalid"
            return value, _utc(record.valid_time), fetched_at
        exact = [record for record in records if _utc(record.valid_time) == target]
        if exact:
            value = _valid_dni(exact[0].dni_W_m2)
            if value is None:
                return "scene_weather_invalid"
            return value, _utc(exact[0].valid_time), fetched_at
        if weather.interpolation != "linear_estimate":
            return "scene_weather_out_of_coverage"
        before = [record for record in records if _utc(record.valid_time) < target]
        after = [record for record in records if _utc(record.valid_time) > target]
        if not before or not after:
            return "scene_weather_out_of_coverage"
        left, right = before[-1], after[0]
        left_value, right_value = _valid_dni(left.dni_W_m2), _valid_dni(right.dni_W_m2)
        if left_value is None or right_value is None:
            return "scene_weather_invalid"
        span = (_utc(right.valid_time) - _utc(left.valid_time)).total_seconds()
        if span <= 0:
            return "scene_weather_invalid"
        fraction = (target - _utc(left.valid_time)).total_seconds() / span
        return left_value + fraction * (right_value - left_value), target, fetched_at
    except (AttributeError, TypeError, ValueError):
        return "scene_weather_invalid"


def _clear_sky_dni(site: Site, timestamp: datetime) -> float:
    try:
        import pandas as pd
        from pvlib.location import Location

        value = (
            Location(site.latitude_deg, site.longitude_deg, tz="UTC")
            .get_clearsky(pd.DatetimeIndex([_utc(timestamp)]))["dni"]
            .iloc[0]
        )
        return _valid_dni(float(value)) or 0.0
    except Exception as error:
        raise ValueError("clear_sky_error") from error


def predict_scene(
    scene: Scene,
    request: SunpathPredictionRequest,
    request_received_at: datetime | None = None,
    nowcast_engine: Engine | None = None,
) -> SunpathPredictionResponse:
    """Calculate a non-persistent prediction from one validated Scene snapshot."""

    geometry = adapt_scene(scene)
    regions = _selected_regions(geometry, request.protected_region_ids)
    start = _start_time(request.start_time)
    try:
        options = PredictionOptions(request.duration_minutes, request.interval_seconds)
        timestamps = make_prediction_grid(start, options)
    except ValueError as error:
        raise SunpathValidationError(str(error)) from error
    samples = sum(len(tile_centres(region)) for region in regions)
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
        received_at = _utc(request_received_at or datetime.now(UTC))
        # Scene weather remains the first-priority source.  Only unresolved
        # timestamps enter the durable Open-Meteo selector; API requests never
        # collect from a provider or start a worker.
        scene_values = [_scene_dni(scene, timestamp, received_at) for timestamp in timestamps]
        unresolved = [
            (index, reason) for index, reason in enumerate(scene_values) if isinstance(reason, str)
        ]
        if nowcast_engine is not None:
            run_id, selected = (
                select_and_record(
                    nowcast_engine,
                    geometry.site,
                    tuple(timestamps[index] for index, _ in unresolved),
                    received_at,
                )
                if unresolved
                else (None, [])
            )
            selected_by_index = dict(zip((index for index, _ in unresolved), selected, strict=True))
            dni_values = []
            for index, timestamp in enumerate(timestamps):
                scene_value = scene_values[index]
                if isinstance(scene_value, tuple):
                    value, valid_time, fetched_at = scene_value
                    dni_values.append(
                        DniValue(value, "scene_weather", False, None, valid_time, fetched_at)
                    )
                    continue
                item = selected_by_index[index]
                dni_values.append(
                    DniValue(
                        item.value,
                        cast(Literal["open_meteo", "pvlib_clear_sky", "unknown"], item.source),
                        item.fallback_used,
                        item.fallback_reason,
                        item.valid_time,
                        item.fetched_at,
                        run_id,
                        item.method,
                        item.snapshot_id,
                        item.provider,
                        item.interval_start,
                        item.interval_end,
                        item.clear_sky_dni,
                        item.provenance,
                    )
                )
        else:
            # Library callers without a database still do not perform network
            # I/O.  This is deliberately the same final fallback as a missing
            # durable snapshot, rather than the old request-time provider path.
            dni_values = []
            for index, timestamp in enumerate(timestamps):
                scene_value = scene_values[index]
                if isinstance(scene_value, tuple):
                    value, valid_time, fetched_at = scene_value
                    dni_values.append(
                        DniValue(value, "scene_weather", False, None, valid_time, fetched_at)
                    )
                    continue
                try:
                    clear = _clear_sky_dni(geometry.site, timestamp)
                    dni_values.append(
                        DniValue(
                            clear,
                            "pvlib_clear_sky",
                            True,
                            "open_meteo_out_of_coverage",
                            timestamp,
                            None,
                            prediction_method="clear_sky_fallback",
                            clear_sky_dni=clear,
                        )
                    )
                except ValueError:
                    dni_values.append(
                        DniValue(
                            None,
                            "unknown",
                            True,
                            "clear_sky_error",
                            None,
                            None,
                            prediction_method="clear_sky_fallback",
                        )
                    )
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
        result_version="0.3.0" if nowcast_engine is not None else "0.2.0",
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
        irradiance=[
            IrradianceResponse(
                timestamp=timestamp,
                baseline_dni_W_m2=dni.value,
                final_dni_W_m2=dni.value,
                source=dni.source,
                fallback_used=dni.fallback_used,
                fallback_reason=cast(FallbackReason | None, dni.fallback_reason),
                valid_time=dni.valid_time,
                interval_start=dni.interval_start,
                interval_end=dni.interval_end,
                request_latitude_deg=_provenance_float(dni.provenance, "request_latitude_deg"),
                request_longitude_deg=_provenance_float(dni.provenance, "request_longitude_deg"),
                grid_latitude_deg=_provenance_float(dni.provenance, "grid_latitude_deg"),
                grid_longitude_deg=_provenance_float(dni.provenance, "grid_longitude_deg"),
                grid_elevation_m=_provenance_float(dni.provenance, "grid_elevation_m"),
                model_selection="best_match"
                if dni.provenance and dni.provenance.get("model_selection") == "best_match"
                else None,
                resolved_model=_provenance_str(dni.provenance, "resolved_model"),
                fetched_at=dni.fetched_at,
                prediction_method=cast(
                    Literal[
                        "stage1_weather_selection", "deterministic_nowcast", "clear_sky_fallback"
                    ],
                    dni.prediction_method,
                ),
                nowcast_run_id=dni.nowcast_run_id,
                nowcast_horizon_seconds=max(0, int((timestamp - received_at).total_seconds()))
                if dni.nowcast_run_id
                else None,
                clear_sky_dni_W_m2=dni.clear_sky_dni,
                clear_sky_index=(
                    dni.value / dni.clear_sky_dni
                    if dni.value is not None and dni.clear_sky_dni not in (None, 0)
                    else None
                ),
                source_snapshots=[
                    SourceSnapshotResponse(
                        snapshot_id=dni.source_snapshot_id,
                        provider=dni.source_provider,
                        role="forecast",
                        weight=1.0,
                    )
                ]
                if dni.source_snapshot_id and dni.source_provider
                else [],
            )
            for timestamp, dni in zip(timestamps, dni_values, strict=True)
        ],
        regions=[
            RegionPredictionResponse(
                region_id=prediction.region_id,
                first_predicted_exposure_time=prediction.first_predicted_exposure_time,
                exposed_at_start=prediction.exposed_at_start,
                exposure_intervals=[
                    ExposureIntervalResponse(
                        start_time=interval.start_time,
                        end_time=interval.end_time,
                    )
                    for interval in prediction.exposure_intervals
                ],
                timeline=[
                    RegionTimeResponse(
                        timestamp=entry.timestamp,
                        status="exposed" if entry.exposed else "not_exposed",
                        exposed_sample_count=sum(sample.exposed for sample in entry.samples),
                        samples=[
                            _sample_response(
                                sample,
                                direct_irradiance_W_m2(
                                    sample,
                                    region.receiving_normal,
                                    solar_positions[index].sun_direction_enu,
                                    dni_values[index].value,
                                ),
                            )
                            for sample in entry.samples
                        ],
                    )
                    for index, entry in enumerate(prediction.timeline)
                ],
            )
            for region, prediction in zip(regions, predictions, strict=True)
        ],
        assumptions=[
            "Direct irradiance uses request-scoped DNI; window optics are not applied.",
            "Exposure is evaluated only at requested sampled timestamps and sample points.",
            "Exterior shading, reflections, and refraction are excluded.",
        ],
    )
