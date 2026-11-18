"""FR-02 synchronous shading plan endpoint over stored FR-06 Scenes."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlmodel import Session

from app.db import get_session
from app.models.scene import SceneRecord
from app.schemas.scenario import SceneScenarioResponse
from app.schemas.scene import Scene
from app.schemas.shading_plan import ShadingPlan, ShadingPlanRequest
from app.schemas.sunpath import SunpathErrorDetail
from app.services.scenario_store import (
    DEFAULT_DAILY_DURATION_MINUTES,
    DEFAULT_DAILY_INTERVAL_SECONDS,
    get_or_compute_scenario_bundle,
    get_or_compute_shading_plan,
)
from app.services.sunpath import SunpathValidationError

router = APIRouter(tags=["shading"])
SessionDependency = Annotated[Session, Depends(get_session)]


def _load_validated_scene(
    session: Session, scene_id: str, expected_scene_revision: int | None = None
) -> tuple[Scene, Engine]:
    record = session.get(SceneRecord, scene_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found.")
    if expected_scene_revision is not None and expected_scene_revision != record.revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Scene revision differs from expected_scene_revision.",
        )
    try:
        scene = Scene.model_validate_json(record.scene_json)
    except ValidationError as error:
        detail = SunpathErrorDetail(
            code="INVALID_SCENE_GEOMETRY",
            message="저장된 공간 정보를 계산할 수 없습니다.",
            target_type="scene",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=detail.model_dump(mode="json"),
        ) from error
    bind = session.get_bind()
    nowcast_engine = bind if isinstance(bind, Engine) else bind.engine
    return scene, nowcast_engine


@router.post(
    "/scenes/{scene_id}/shading-plans",
    response_model=ShadingPlan,
    status_code=status.HTTP_200_OK,
)
def create_shading_plan(
    scene_id: str,
    request: ShadingPlanRequest,
    session: SessionDependency,
) -> ShadingPlan:
    """Plan the film cells of one immutable stored Scene snapshot."""

    request_received_at = datetime.now(UTC)
    scene, nowcast_engine = _load_validated_scene(
        session, scene_id, request.expected_scene_revision
    )
    try:
        return get_or_compute_shading_plan(
            session,
            scene,
            request,
            request_received_at=request_received_at,
            nowcast_engine=nowcast_engine,
        )
    except SunpathValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error.detail.model_dump(mode="json"),
        ) from error


@router.get(
    "/scenes/{scene_id}/scenario",
    response_model=SceneScenarioResponse,
    status_code=status.HTTP_200_OK,
)
def read_scene_scenario(
    scene_id: str,
    session: SessionDependency,
    start_time: str | None = None,
    duration_minutes: Annotated[int, Query(ge=1, le=1440)] = DEFAULT_DAILY_DURATION_MINUTES,
    interval_seconds: Annotated[int, Query(ge=1, le=3600)] = DEFAULT_DAILY_INTERVAL_SECONDS,
) -> SceneScenarioResponse:
    """Read (or lazily populate once) the precomputed scenario bundle for one stored Scene."""

    request_received_at = datetime.now(UTC)
    scene, nowcast_engine = _load_validated_scene(session, scene_id)
    try:
        return get_or_compute_scenario_bundle(
            session,
            scene,
            start_time=start_time,
            duration_minutes=duration_minutes,
            interval_seconds=interval_seconds,
            include_adaptive=True,
            request_received_at=request_received_at,
            nowcast_engine=nowcast_engine,
        )
    except SunpathValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error.detail.model_dump(mode="json"),
        ) from error
