"""Read-only API for worker-produced frozen FR-01 operational nowcasts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import Engine
from sqlmodel import Session

from app.db import get_session
from app.models.scene import SceneRecord
from app.nowcast.kma_operational_repository import OperationalNowcastRepository
from app.schemas.nowcast_ai import OperationalNowcastPoint, OperationalNowcastResponse

router = APIRouter(tags=["nowcast-ai"])
SessionDependency = Annotated[Session, Depends(get_session)]


def _engine(session: Session) -> Engine:
    bind = session.get_bind()
    return bind if isinstance(bind, Engine) else bind.engine


def _response(document: dict[str, Any]) -> OperationalNowcastResponse:
    run = document["run"]
    points = [
        OperationalNowcastPoint(
            horizon_minutes=point["horizon_minutes"],
            valid_time=point["valid_time"],
            prediction_source=point["prediction_source"],
            dni_W_m2=point["dni_W_m2"],
            p10_dni_W_m2=point["dni_p10_W_m2"],
            p50_dni_W_m2=point["dni_p50_W_m2"],
            p90_dni_W_m2=point["dni_p90_W_m2"],
            open_meteo_dni_W_m2=point["open_meteo_dni_W_m2"],
            fallback_used=bool(point["fallback_used"]),
            fallback_reason=point["fallback_reason"],
            quality_flags=json.loads(point["quality_flags_json"]),
            kma_snapshot_id=point["kma_snapshot_id"],
            kma_observed_at=point["kma_observed_at"],
            open_meteo_snapshot_id=point["open_meteo_snapshot_id"],
            open_meteo_fetched_at=point["open_meteo_fetched_at"],
        )
        for point in document["points"]
    ]
    return OperationalNowcastResponse(
        run_id=run["run_id"],
        scene_id=run["scene_id"],
        prediction_origin=run["prediction_origin"],
        created_at=run["created_at"],
        model_version=run["model_version"],
        model_manifest_sha256=run["model_manifest_sha256"],
        rollout_mode=run["rollout_mode"],
        status=run["status"],
        result_hash=run["result_hash"],
        points=points,
    )


def _latest(scene_id: str, session: Session) -> OperationalNowcastResponse:
    if session.get(SceneRecord, scene_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found.")
    document = OperationalNowcastRepository(_engine(session)).latest_run(
        scene_id, datetime.now(UTC)
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Operational nowcast is not ready or is stale.",
        )
    return _response(document)


@router.get(
    "/scenes/{scene_id}/ai-nowcast/latest",
    response_model=OperationalNowcastResponse,
    status_code=status.HTTP_200_OK,
)
def get_latest_ai_nowcast(
    scene_id: str,
    session: SessionDependency,
) -> OperationalNowcastResponse:
    """Return the latest unexpired worker-produced result; never call a provider."""

    return _latest(scene_id, session)


@router.get(
    "/scenes/{scene_id}/nowcast-comparison",
    response_model=OperationalNowcastResponse,
    status_code=status.HTTP_200_OK,
    deprecated=True,
)
def get_nowcast_comparison_compatibility(
    scene_id: str,
    session: SessionDependency,
    response: Response,
) -> OperationalNowcastResponse:
    """Temporary compatibility alias while the frontend adopts the latest endpoint."""

    response.headers["Deprecation"] = "true"
    response.headers["Link"] = (
        f'</api/scenes/{scene_id}/ai-nowcast/latest>; rel="successor-version"'
    )
    return _latest(scene_id, session)
