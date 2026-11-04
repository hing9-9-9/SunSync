"""FR-05 REST API endpoints for smart film commands, schedule playback, and marker pairing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine
from sqlmodel import Session

from app.db import get_session
from app.models.scene import SceneRecord
from app.schemas.film import (
    FilmApplicationLogListResponse,
    MarkerModeRequest,
    MarkerPairingRequest,
    MarkerPairingResponse,
    SceneFilmStateResponse,
    ScheduleApplyRequest,
    ScheduleApplyResponse,
    WindowFilmCommandRequest,
    WindowFilmStateResponse,
)
from app.schemas.scene import Scene
from app.schemas.shading_plan import ShadingPlan
from app.services.film import (
    apply_film_commands,
    apply_schedule_to_scene,
    get_film_logs,
    get_scene_film_state,
    normalize_iso_timestamp,
    pair_marker_for_scene,
    set_marker_mode,
)
from app.services.shading import shading_plan_for_scene

router = APIRouter(tags=["film"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get(
    "/scenes/{scene_id}/film-state",
    response_model=SceneFilmStateResponse,
)
def read_film_state(
    scene_id: str,
    session: SessionDependency,
    window_id: str | None = None,
    at: str | None = None,
) -> SceneFilmStateResponse:
    """Retrieve or evaluate virtual film state(s) at current time or simulation clock time."""
    windows = get_scene_film_state(session=session, scene_id=scene_id, window_id=window_id, at=at)
    evaluated_at = (
        normalize_iso_timestamp(at)
        if at is not None
        else normalize_iso_timestamp(datetime.now(UTC))
    )
    return SceneFilmStateResponse(
        scene_id=scene_id,
        evaluated_at=evaluated_at,
        windows=windows,
    )


@router.post(
    "/scenes/{scene_id}/film-commands",
    response_model=WindowFilmStateResponse,
)
def send_film_commands(
    scene_id: str,
    request: WindowFilmCommandRequest,
    session: SessionDependency,
) -> WindowFilmStateResponse:
    """Apply manual cell commands atomically to a virtual film window."""
    return apply_film_commands(session=session, scene_id=scene_id, request=request)


@router.post(
    "/scenes/{scene_id}/film-marker-mode",
    response_model=WindowFilmStateResponse,
)
def toggle_marker_mode(
    scene_id: str,
    request: MarkerModeRequest,
    session: SessionDependency,
) -> WindowFilmStateResponse:
    """FR-05.2: Enable or disable 6x6 ArUco marker display, restoring prior cell opacities on disable."""
    return set_marker_mode(session=session, scene_id=scene_id, request=request)


@router.post(
    "/scenes/{scene_id}/film-schedule-apply",
    response_model=ScheduleApplyResponse,
)
def apply_schedule(
    scene_id: str,
    request: ScheduleApplyRequest,
    session: SessionDependency,
) -> ScheduleApplyResponse:
    """Apply an FR-02 shading plan or plan request to the scene films with simulation clock support."""
    plan: ShadingPlan
    if request.plan is not None:
        plan = request.plan
    elif request.plan_request is not None:
        scene_record = session.get(SceneRecord, scene_id)
        if scene_record is None:
            raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")
        scene = Scene.model_validate_json(scene_record.scene_json)
        nowcast_engine = session.bind if isinstance(session.bind, Engine) else None
        plan = shading_plan_for_scene(
            scene,
            request.plan_request,
            request_received_at=datetime.now(UTC),
            nowcast_engine=nowcast_engine,
        )
    else:
        raise HTTPException(status_code=422, detail="Either plan or plan_request must be provided")

    return apply_schedule_to_scene(
        session=session,
        scene_id=scene_id,
        plan=plan,
        at=request.at,
        plan_id=request.plan_id,
    )


@router.get(
    "/scenes/{scene_id}/film-logs",
    response_model=FilmApplicationLogListResponse,
)
def read_film_logs(
    scene_id: str,
    session: SessionDependency,
    window_id: str | None = None,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
) -> FilmApplicationLogListResponse:
    """Query chronological application transition logs."""
    return get_film_logs(
        session=session,
        scene_id=scene_id,
        window_id=window_id,
        status=status,
        since=since,
        until=until,
        limit=limit,
    )


@router.post(
    "/scenes/{scene_id}/marker-pairing",
    response_model=MarkerPairingResponse,
)
def pair_marker(
    scene_id: str,
    request: MarkerPairingRequest,
    session: SessionDependency,
) -> MarkerPairingResponse:
    """Auto-bind PPT ArUco marker, clamp furniture into room boundaries, and activate marker display."""
    return pair_marker_for_scene(session=session, scene_id=scene_id, request=request)
