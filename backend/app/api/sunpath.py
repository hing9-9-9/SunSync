"""FR-01 synchronous prediction endpoint over stored FR-06 Scenes."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlmodel import Session

from app.db import get_session
from app.models.scene import SceneRecord
from app.schemas.scene import Scene
from app.schemas.sunpath import SunpathPredictionRequest, SunpathPredictionResponse
from app.services.sunpath import SunpathValidationError, predict_scene

router = APIRouter(tags=["sunpath"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post(
    "/scenes/{scene_id}/sunpath-predictions",
    response_model=SunpathPredictionResponse,
    status_code=status.HTTP_200_OK,
)
def create_sunpath_prediction(
    scene_id: str,
    request: SunpathPredictionRequest,
    session: SessionDependency,
) -> SunpathPredictionResponse:
    """Predict direct-sun sample exposure from one immutable stored Scene snapshot."""

    request_received_at = datetime.now(UTC)
    record = session.get(SceneRecord, scene_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found.")
    if (
        request.expected_scene_revision is not None
        and request.expected_scene_revision != record.revision
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Scene revision differs from expected_scene_revision.",
        )
    try:
        scene = Scene.model_validate_json(record.scene_json)
        return predict_scene(
            scene, request, request_received_at=request_received_at, nowcast_engine=session.get_bind()
        )
    except (SunpathValidationError, ValidationError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
