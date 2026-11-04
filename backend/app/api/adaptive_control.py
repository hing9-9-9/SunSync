"""FastAPI router for FR-04 Uncertainty-Aware Adaptive Smart Film Control & ThinQ ON Bridge."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.db import get_session
from app.schemas.adaptive_control import (
    AdaptiveShadingPlanRequest,
    AdaptiveShadingPlanResponse,
    ThinQHubSyncRequest,
    ThinQHubSyncResponse,
)
from app.services.adaptive_control import compute_adaptive_shading_plan, sync_to_thinq_hub

router = APIRouter(tags=["adaptive-control"])

SessionDependency = Annotated[Session, Depends(get_session)]


@router.post(
    "/scenes/{scene_id}/adaptive-shading-plan",
    response_model=AdaptiveShadingPlanResponse,
    status_code=status.HTTP_200_OK,
    summary="Compute uncertainty-aware adaptive smart film shading plan",
)
def get_adaptive_shading_plan(
    scene_id: str,
    request: AdaptiveShadingPlanRequest,
    session: SessionDependency,
) -> AdaptiveShadingPlanResponse:
    """Compute an uncertainty-aware, flicker-reduced shading plan using multi-objective DP and FR-04 rules."""
    return compute_adaptive_shading_plan(session=session, scene_id=scene_id, request=request)


@router.post(
    "/scenes/{scene_id}/thinq-hub-sync",
    response_model=ThinQHubSyncResponse,
    status_code=status.HTTP_200_OK,
    summary="Synchronize continuous dimming state with LG ThinQ ON home hub",
)
def sync_thinq_hub(
    scene_id: str,
    request: ThinQHubSyncRequest,
    session: SessionDependency,
) -> ThinQHubSyncResponse:
    """Apply adaptive multi-level / continuous dimming state and push payload to LG ThinQ ON hub."""
    return sync_to_thinq_hub(session=session, scene_id=scene_id, request=request)
