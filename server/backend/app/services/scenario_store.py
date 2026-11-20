"""Event-driven and rolling time-bucket scenario precomputation & SQLite cache store."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import Engine, delete, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col

from app.models.scenario_snapshot import ScenarioSnapshotRecord
from app.models.scene import SceneRecord
from app.nowcast.repository import utc_z
from app.nowcast.selection import OPEN_METEO_FRESHNESS_SECONDS
from app.schemas.adaptive_control import (
    AdaptiveShadingPlanRequest,
    AdaptiveShadingPlanResponse,
)
from app.schemas.scenario import SceneScenarioResponse
from app.schemas.scene import Scene
from app.schemas.shading_plan import ShadingPlan, ShadingPlanRequest
from app.schemas.sunpath import SunpathPredictionRequest, SunpathPredictionResponse
from app.services import shading as shading_service
from app.services import sunpath as sunpath_service
from engine.fr02 import ExposedTile

DEFAULT_DAILY_DURATION_MINUTES = 1440
DEFAULT_DAILY_INTERVAL_SECONDS = 1800
_MAX_LOCK_ENTRIES = 512

_LOCK_GUARD = threading.Lock()
_SINGLE_FLIGHT_LOCKS: OrderedDict[str, threading.Lock] = OrderedDict()


def _single_flight_lock(key: str) -> threading.Lock:
    with _LOCK_GUARD:
        lock = _SINGLE_FLIGHT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SINGLE_FLIGHT_LOCKS[key] = lock
        else:
            _SINGLE_FLIGHT_LOCKS.move_to_end(key)
        while len(_SINGLE_FLIGHT_LOCKS) > _MAX_LOCK_ENTRIES:
            _SINGLE_FLIGHT_LOCKS.popitem(last=False)
        return lock


def engine_of(session: Session, explicit_engine: Engine | None = None) -> Engine:
    if explicit_engine is not None:
        return explicit_engine
    bind = session.get_bind()
    return bind if isinstance(bind, Engine) else bind.engine


def current_time_bucket(now: datetime | None = None) -> str:
    """Floor a timestamp to its 10-minute UTC rolling time bucket."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    floored = current.replace(minute=(current.minute // 10) * 10, second=0, microsecond=0)
    return floored.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_start_utc(start_time: str | datetime) -> str:
    """Parse an aware ISO-8601 timestamp into canonical UTC string form."""
    if isinstance(start_time, str):
        try:
            parsed = datetime.fromisoformat(start_time)
        except ValueError as error:
            raise sunpath_service.SunpathValidationError(
                "INVALID_PREDICTION_REQUEST",
                "예측 요청값을 확인해 주세요.",
                target_type="request",
            ) from error
    else:
        parsed = start_time
    if parsed.utcoffset() is None:
        raise sunpath_service.SunpathValidationError(
            "INVALID_PREDICTION_REQUEST",
            "예측 요청값을 확인해 주세요.",
            target_type="request",
        )
    utc_dt = parsed.astimezone(UTC)
    if utc_dt.microsecond == 0:
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def prediction_day_start_utc(scene: Scene, now: datetime | None = None) -> str:
    """Return midnight today in the scene's local timezone converted to canonical UTC ISO."""
    tz = ZoneInfo(scene.site.timezone)
    local_midnight = (
        (now or datetime.now(UTC)).astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    return normalize_start_utc(local_midnight)


def scenario_snapshot_key(
    scene_id: str,
    scene_revision: int,
    start_utc: str,
    duration_minutes: int,
    interval_seconds: int,
) -> str:
    return f"{scene_id}:{scene_revision}:{start_utc}:{duration_minutes}:{interval_seconds}"


def _scene_weather_hash(scene: Scene, now: datetime) -> str:
    weather = scene.weather
    if weather is None:
        return "none"
    fresh = False
    if weather.fetched_at is not None:
        try:
            fetched_at = datetime.fromisoformat(str(weather.fetched_at)).astimezone(UTC)
            age_s = (now - fetched_at).total_seconds()
            fresh = fetched_at <= now and age_s <= weather.max_age_s
        except ValueError:
            fresh = False
    payload = json.dumps(
        {"weather": weather.model_dump(mode="json"), "fresh": fresh},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compute_weather_fingerprint(engine: Engine | None, scene: Scene, now: datetime) -> str:
    """Build a deterministic digest of inline scene weather, Open-Meteo snapshots, and AI nowcast."""
    current = now.astimezone(UTC)
    now_iso = utc_z(current)
    weather_hash = _scene_weather_hash(scene, current)
    open_meteo_ids: list[str] = []
    active_ai_run_id: str | None = None
    site_key = f"{scene.site.latitude_deg:.6f},{scene.site.longitude_deg:.6f}"

    if engine is not None:
        try:
            with engine.connect() as conn:
                om_rows = conn.execute(
                    text(
                        """
                        SELECT snapshot_id, fetched_at, provenance_json
                        FROM weather_snapshot
                        WHERE provider = 'open_meteo'
                          AND quality_status = 'valid'
                        ORDER BY fetched_at DESC, snapshot_id ASC
                        LIMIT 20
                        """
                    ),
                ).fetchall()
                for snapshot_id, fetched_at_raw, provenance_raw in om_rows:
                    try:
                        fetched_dt = datetime.fromisoformat(str(fetched_at_raw)).astimezone(UTC)
                        provenance = json.loads(str(provenance_raw) or "null")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if (
                        isinstance(provenance, dict)
                        and provenance.get("spatial_provenance") == "provider_grid"
                        and 0.0
                        <= (current - fetched_dt).total_seconds()
                        <= OPEN_METEO_FRESHNESS_SECONDS
                    ):
                        open_meteo_ids.append(str(snapshot_id))

                ai_row = conn.execute(
                    text(
                        """
                        SELECT nr.run_id
                        FROM ai_nowcast_run nr
                        JOIN ai_nowcast_point np ON nr.run_id = np.run_id
                        WHERE nr.site_key = :site_key
                          AND nr.rollout_mode = 'active'
                          AND nr.expires_at >= :now
                          AND np.prediction_source = 'ai_model'
                          AND np.dni_p10_W_m2 IS NOT NULL
                          AND np.dni_p50_W_m2 IS NOT NULL
                          AND np.dni_p90_W_m2 IS NOT NULL
                        ORDER BY nr.created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"site_key": site_key, "now": now_iso},
                ).first()
                if ai_row is not None:
                    active_ai_run_id = str(ai_row[0])
        except SQLAlchemyError:
            pass

    digest_source = json.dumps(
        {
            "scene_weather": weather_hash,
            "open_meteo_ids": open_meteo_ids,
            "active_ai_run_id": active_ai_run_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]


def is_cache_bypassed() -> bool:
    """Bypass scenario persistence when tests monkeypatch core FR-01/FR-02 hooks or limits."""
    return (
        sunpath_service.is_prediction_hooked()
        or shading_service.predict_scene is not shading_service._ORIGINAL_PREDICT_SCENE
    )


def _save_snapshot(engine: Engine, record: ScenarioSnapshotRecord) -> None:
    with Session(engine) as write_session:
        write_session.merge(record)
        write_session.commit()


def _validate_request_bounds(
    scene: Scene,
    *,
    start_utc: str,
    duration_minutes: int,
    interval_seconds: int,
) -> sunpath_service.SceneRayTrace:
    return sunpath_service.trace_scene_geometry(
        scene,
        start_time=start_utc,
        duration_minutes=duration_minutes,
        interval_seconds=interval_seconds,
    )


def _compute_and_build_record(
    engine: Engine,
    scene: Scene,
    *,
    start_utc: str,
    duration_minutes: int,
    interval_seconds: int,
    fingerprint: str,
    received_at: datetime,
    require_shading: bool,
) -> tuple[ScenarioSnapshotRecord, sunpath_service.SceneRayTrace, list[sunpath_service.DniValue]]:
    trace = _validate_request_bounds(
        scene,
        start_utc=start_utc,
        duration_minutes=duration_minutes,
        interval_seconds=interval_seconds,
    )
    dni_values = sunpath_service.resolve_scene_dni(
        scene,
        trace.geometry,
        trace.timestamps,
        received_at,
        nowcast_engine=engine,
    )
    summary = sunpath_service.build_sunpath_prediction_response(
        scene,
        trace,
        dni_values,
        interval_seconds=interval_seconds,
        received_at=received_at,
        include_samples=False,
    )
    shading_json: str | None = None
    if require_shading:
        shading_plan = shading_service.shading_plan_from_trace(
            scene, trace, dni_values, interval_seconds=interval_seconds
        )
        shading_json = shading_plan.model_dump_json()
    elif scene.policy is not None:
        try:
            shading_plan = shading_service.shading_plan_from_trace(
                scene, trace, dni_values, interval_seconds=interval_seconds
            )
            shading_json = shading_plan.model_dump_json()
        except sunpath_service.SunpathValidationError:
            shading_json = None

    snapshot_key = scenario_snapshot_key(
        scene.scene_id, scene.revision, start_utc, duration_minutes, interval_seconds
    )
    record = ScenarioSnapshotRecord(
        snapshot_key=snapshot_key,
        scene_id=scene.scene_id,
        scene_revision=scene.revision,
        start_time=start_utc,
        duration_minutes=duration_minutes,
        interval_seconds=interval_seconds,
        time_bucket=current_time_bucket(received_at),
        weather_fingerprint=fingerprint,
        sunpath_summary_json=summary.model_dump_json(),
        shading_plan_json=shading_json,
        adaptive_plan_json=None,
        computed_at=utc_z(received_at),
    )
    return record, trace, dni_values


def _can_reuse_record(
    record: ScenarioSnapshotRecord | None,
    fingerprint: str,
    *,
    require_shading: bool,
) -> bool:
    if record is None or record.weather_fingerprint != fingerprint:
        return False
    return not (require_shading and record.shading_plan_json is None)


def get_or_compute_scenario(
    session: Session,
    scene: Scene,
    *,
    start_time: str,
    duration_minutes: int,
    interval_seconds: int,
    require_shading: bool = False,
    request_received_at: datetime | None = None,
    nowcast_engine: Engine | None = None,
) -> ScenarioSnapshotRecord:
    """Return a fresh persisted scenario snapshot, coalescing concurrent cold-cache requests."""

    received_at = (request_received_at or datetime.now(UTC)).astimezone(UTC)
    start_utc = normalize_start_utc(start_time)
    engine = engine_of(session, nowcast_engine)
    snapshot_key = scenario_snapshot_key(
        scene.scene_id, scene.revision, start_utc, duration_minutes, interval_seconds
    )
    fingerprint = compute_weather_fingerprint(engine, scene, received_at)

    existing = session.get(ScenarioSnapshotRecord, snapshot_key)
    if _can_reuse_record(existing, fingerprint, require_shading=require_shading):
        assert existing is not None
        return existing

    lock = _single_flight_lock(snapshot_key)
    with lock:
        with Session(engine) as check_session:
            fresh = check_session.get(ScenarioSnapshotRecord, snapshot_key)
            if _can_reuse_record(fresh, fingerprint, require_shading=require_shading):
                assert fresh is not None
                return fresh

        record, _trace, _dni = _compute_and_build_record(
            engine,
            scene,
            start_utc=start_utc,
            duration_minutes=duration_minutes,
            interval_seconds=interval_seconds,
            fingerprint=fingerprint,
            received_at=received_at,
            require_shading=require_shading,
        )
        _save_snapshot(engine, record)
        return record


def get_or_compute_sunpath_summary(
    session: Session,
    scene: Scene,
    request: SunpathPredictionRequest,
    *,
    request_received_at: datetime | None = None,
    nowcast_engine: Engine | None = None,
) -> SunpathPredictionResponse:
    """Read or compute a summary FR-01 prediction backed by ScenarioSnapshotRecord."""

    if (
        request.detail_level != "summary"
        or request.protected_region_ids is not None
        or is_cache_bypassed()
    ):
        return sunpath_service.predict_scene(
            scene,
            request,
            request_received_at=request_received_at,
            nowcast_engine=nowcast_engine,
        )

    record = get_or_compute_scenario(
        session,
        scene,
        start_time=request.start_time,
        duration_minutes=request.duration_minutes,
        interval_seconds=request.interval_seconds,
        require_shading=False,
        request_received_at=request_received_at,
        nowcast_engine=nowcast_engine,
    )
    return SunpathPredictionResponse.model_validate_json(record.sunpath_summary_json)


def get_or_compute_shading_plan(
    session: Session,
    scene: Scene,
    request: ShadingPlanRequest,
    *,
    request_received_at: datetime | None = None,
    nowcast_engine: Engine | None = None,
) -> ShadingPlan:
    """Read or compute an FR-02 ShadingPlan backed by ScenarioSnapshotRecord."""

    if is_cache_bypassed():
        return shading_service.shading_plan_for_scene(
            scene,
            request,
            request_received_at=request_received_at,
            nowcast_engine=nowcast_engine,
        )

    record = get_or_compute_scenario(
        session,
        scene,
        start_time=request.start_time,
        duration_minutes=request.duration_minutes,
        interval_seconds=request.interval_seconds,
        require_shading=True,
        request_received_at=request_received_at,
        nowcast_engine=nowcast_engine,
    )
    assert record.shading_plan_json is not None
    return ShadingPlan.model_validate_json(record.shading_plan_json)


def get_baseline_and_tiles(
    session: Session,
    scene: Scene,
    *,
    start_time: str,
    duration_minutes: int,
    interval_seconds: int,
    request_received_at: datetime | None = None,
    nowcast_engine: Engine | None = None,
) -> tuple[ShadingPlan, list[ExposedTile]]:
    """Obtain both baseline FR-02 plan and exposed tiles while reusing geometry and snapshot caches."""

    received_at = (request_received_at or datetime.now(UTC)).astimezone(UTC)
    start_utc = normalize_start_utc(start_time)
    engine = engine_of(session, nowcast_engine)

    record = get_or_compute_scenario(
        session,
        scene,
        start_time=start_utc,
        duration_minutes=duration_minutes,
        interval_seconds=interval_seconds,
        require_shading=True,
        request_received_at=received_at,
        nowcast_engine=engine,
    )
    assert record.shading_plan_json is not None
    baseline_plan = ShadingPlan.model_validate_json(record.shading_plan_json)
    trace = sunpath_service.trace_scene_geometry(
        scene,
        start_time=start_utc,
        duration_minutes=duration_minutes,
        interval_seconds=interval_seconds,
    )
    dni_values = sunpath_service.resolve_scene_dni(
        scene,
        trace.geometry,
        trace.timestamps,
        received_at,
        nowcast_engine=engine,
    )
    return baseline_plan, sunpath_service.extract_exposed_tiles(trace, dni_values)


def get_or_compute_adaptive_preview(
    session: Session,
    scene: Scene,
    request: AdaptiveShadingPlanRequest,
    compute_fn: Callable[[ShadingPlan, list[ExposedTile]], AdaptiveShadingPlanResponse],
    *,
    request_received_at: datetime | None = None,
    nowcast_engine: Engine | None = None,
) -> AdaptiveShadingPlanResponse:
    """Read or compute and persist the default FR-04 adaptive preview in ScenarioSnapshotRecord."""

    received_at = (request_received_at or datetime.now(UTC)).astimezone(UTC)
    start_utc = normalize_start_utc(request.start_time)
    engine = engine_of(session, nowcast_engine)
    record = get_or_compute_scenario(
        session,
        scene,
        start_time=start_utc,
        duration_minutes=request.duration_minutes,
        interval_seconds=request.interval_seconds,
        require_shading=True,
        request_received_at=received_at,
        nowcast_engine=engine,
    )
    if record.adaptive_plan_json is not None:
        return AdaptiveShadingPlanResponse.model_validate_json(record.adaptive_plan_json)

    lock = _single_flight_lock(f"adaptive:{record.snapshot_key}")
    with lock:
        with Session(engine) as check_session:
            fresh = check_session.get(ScenarioSnapshotRecord, record.snapshot_key)
            if (
                fresh is not None
                and fresh.weather_fingerprint == record.weather_fingerprint
                and fresh.adaptive_plan_json is not None
            ):
                return AdaptiveShadingPlanResponse.model_validate_json(fresh.adaptive_plan_json)

        baseline_plan, tiles = get_baseline_and_tiles(
            session,
            scene,
            start_time=start_utc,
            duration_minutes=request.duration_minutes,
            interval_seconds=request.interval_seconds,
            request_received_at=received_at,
            nowcast_engine=engine,
        )
        adaptive_resp = compute_fn(baseline_plan, tiles)
        updated = ScenarioSnapshotRecord(
            snapshot_key=record.snapshot_key,
            scene_id=record.scene_id,
            scene_revision=record.scene_revision,
            start_time=record.start_time,
            duration_minutes=record.duration_minutes,
            interval_seconds=record.interval_seconds,
            time_bucket=record.time_bucket,
            weather_fingerprint=record.weather_fingerprint,
            sunpath_summary_json=record.sunpath_summary_json,
            shading_plan_json=record.shading_plan_json,
            adaptive_plan_json=adaptive_resp.model_dump_json(),
            computed_at=utc_z(received_at),
        )
        _save_snapshot(engine, updated)
        return adaptive_resp


def get_or_compute_scenario_bundle(
    session: Session,
    scene: Scene,
    *,
    start_time: str | None = None,
    duration_minutes: int = DEFAULT_DAILY_DURATION_MINUTES,
    interval_seconds: int = DEFAULT_DAILY_INTERVAL_SECONDS,
    include_adaptive: bool = True,
    request_received_at: datetime | None = None,
    nowcast_engine: Engine | None = None,
) -> SceneScenarioResponse:
    """Return bundled sunpath, shading, and adaptive scenarios for the requested or daily window."""

    received_at = (request_received_at or datetime.now(UTC)).astimezone(UTC)
    resolved_start = (
        normalize_start_utc(start_time)
        if start_time is not None
        else prediction_day_start_utc(scene, received_at)
    )
    engine = engine_of(session, nowcast_engine)
    record = get_or_compute_scenario(
        session,
        scene,
        start_time=resolved_start,
        duration_minutes=duration_minutes,
        interval_seconds=interval_seconds,
        require_shading=False,
        request_received_at=received_at,
        nowcast_engine=engine,
    )
    adaptive_plan: AdaptiveShadingPlanResponse | None = None
    if include_adaptive and scene.windows and record.shading_plan_json is not None:
        if record.adaptive_plan_json is not None:
            adaptive_plan = AdaptiveShadingPlanResponse.model_validate_json(
                record.adaptive_plan_json
            )
        else:
            from app.services.adaptive_control import compute_adaptive_shading_plan

            adaptive_req = AdaptiveShadingPlanRequest(
                start_time=resolved_start,
                duration_minutes=duration_minutes,
                interval_seconds=interval_seconds,
            )
            adaptive_plan = compute_adaptive_shading_plan(
                session=session,
                scene_id=scene.scene_id,
                request=adaptive_req,
                nowcast_engine=engine,
                request_received_at=received_at,
            )
            with Session(engine) as check_session:
                refreshed = check_session.get(ScenarioSnapshotRecord, record.snapshot_key)
                if refreshed is not None:
                    record = refreshed

    return SceneScenarioResponse(
        scene_id=scene.scene_id,
        scene_revision=scene.revision,
        snapshot_key=record.snapshot_key,
        time_bucket=record.time_bucket,
        weather_fingerprint=record.weather_fingerprint,
        computed_at=record.computed_at,
        sunpath_prediction=SunpathPredictionResponse.model_validate_json(
            record.sunpath_summary_json
        ),
        shading_plan=(
            ShadingPlan.model_validate_json(record.shading_plan_json)
            if record.shading_plan_json is not None
            else None
        ),
        adaptive_plan=adaptive_plan,
    )


def invalidate_scene_scenarios(
    session: Session,
    scene_id: str,
    *,
    keep_revision: int | None = None,
) -> None:
    """Invalidate stale scenario snapshots after a local scene update or deletion."""

    statement: Any = delete(ScenarioSnapshotRecord).where(
        col(ScenarioSnapshotRecord.scene_id) == scene_id
    )
    if keep_revision is not None:
        statement = statement.where(col(ScenarioSnapshotRecord.scene_revision) != keep_revision)
    session.exec(statement)


def precompute_scene_daily_scenario(
    engine: Engine,
    scene_id: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Precompute and persist today's 24-hour scenario bundle for a single stored Scene."""

    current = (now or datetime.now(UTC)).astimezone(UTC)
    with Session(engine) as session:
        row = session.get(SceneRecord, scene_id)
        if row is None:
            return False
        scene = Scene.model_validate_json(row.scene_json)
        if not scene.protected_regions:
            return False
        invalidate_scene_scenarios(session, scene.scene_id, keep_revision=scene.revision)
        session.commit()
        get_or_compute_scenario_bundle(
            session,
            scene,
            include_adaptive=bool(scene.windows and scene.policy is not None),
            request_received_at=current,
            nowcast_engine=engine,
        )
        return True


def run_rolling_scenario_update(
    engine: Engine,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Sequentially roll daily scenarios across all stored Scenes for the current time bucket."""

    current = (now or datetime.now(UTC)).astimezone(UTC)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT scene_id FROM scenes ORDER BY scene_id")).all()
    total = updated = failed = 0
    for (scene_id,) in rows:
        total += 1
        try:
            if precompute_scene_daily_scenario(engine, str(scene_id), now=current):
                updated += 1
        except (sunpath_service.SunpathValidationError, ValidationError, ValueError, OSError):
            failed += 1
    return {"scenes": total, "updated": updated, "failed": failed}
