"""FR-01.4 KMA AI × Open-Meteo Hybrid Nowcasting and Comparison API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Engine
from sqlmodel import Session

from app.db import get_session
from app.models.scene import SceneRecord
from app.nowcast.kma_ml import KmaQuantileNowcaster
from app.schemas.nowcast_ai import (
    AiNowcastRequest,
    CloudPatchSummary,
    KmaPointObservation,
    NowcastComparisonResponse,
    NowcastMode,
)
from app.schemas.scene import Scene
from engine.fr01.models import Site

router = APIRouter(tags=["nowcast-ai"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post(
    "/scenes/{scene_id}/ai-nowcast",
    response_model=NowcastComparisonResponse,
    status_code=status.HTTP_200_OK,
)
def create_ai_nowcast(
    scene_id: str,
    request: AiNowcastRequest,
    session: SessionDependency,
) -> NowcastComparisonResponse:
    """Run the KMA AI × Open-Meteo hybrid quantile nowcaster for a specific scene."""
    record = session.get(SceneRecord, scene_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scene not found.",
        )

    try:
        scene = Scene.model_validate_json(record.scene_json)
        site = Site(
            latitude_deg=scene.site.latitude_deg,
            longitude_deg=scene.site.longitude_deg,
            timezone=scene.site.timezone,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Scene geometry or site configuration is invalid: {exc}",
        ) from exc

    bind = session.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    nowcaster = KmaQuantileNowcaster(engine=engine)

    return nowcaster.predict(site=site, request=request, scene_id=scene_id)


@router.get(
    "/scenes/{scene_id}/nowcast-comparison",
    response_model=NowcastComparisonResponse,
    status_code=status.HTTP_200_OK,
)
def get_nowcast_comparison(
    scene_id: str,
    session: SessionDependency,
    start_time: str | None = None,
    duration_minutes: Annotated[int, Query(ge=5, le=1440)] = 240,
    interval_seconds: Annotated[int, Query(ge=60, le=3600)] = 900,
    mode: NowcastMode = "kma_ai_ensemble",
) -> NowcastComparisonResponse:
    """Fetch comparative nowcast timeline (KMA AI quantile bands vs Open-Meteo)."""
    record = session.get(SceneRecord, scene_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scene not found.",
        )

    try:
        scene = Scene.model_validate_json(record.scene_json)
        site = Site(
            latitude_deg=scene.site.latitude_deg,
            longitude_deg=scene.site.longitude_deg,
            timezone=scene.site.timezone,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Scene geometry or site configuration is invalid: {exc}",
        ) from exc

    # Determine timeline start
    if start_time is not None:
        effective_start = start_time
    elif scene.scenario and scene.scenario.start_time:
        effective_start = scene.scenario.start_time
    else:
        effective_start = "2026-10-14T02:00:00Z"

    # Synthesize realistic recent KMA surface observations around start time if not provided
    try:
        st_dt = datetime.fromisoformat(effective_start).astimezone(UTC)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid start_time format: {exc}",
        ) from exc

    kma_obs: list[KmaPointObservation] = []
    for minutes_back in [30, 20, 10]:
        t_obs = (st_dt - timedelta(minutes=minutes_back)).isoformat().replace("+00:00", "Z")
        # Realistic midday DSR with slight cloud variation
        kma_obs.append(
            KmaPointObservation(
                timestamp=t_obs,
                dsr_W_m2=680.0 - (minutes_back % 20) * 8.0,
                cloud_cover_pct=25.0 + (minutes_back % 15),
                temperature_c=21.5,
                humidity_pct=48.0,
            )
        )

    cloud_patch = CloudPatchSummary(
        patch_size_km=10.0,
        mean_cloud_fraction=0.28,
        upwind_cloud_gradient=0.08,
        motion_u_m_s=3.5,
        motion_v_m_s=-1.2,
        cloud_top_height_m=3200.0,
    )

    request = AiNowcastRequest(
        start_time=effective_start,
        duration_minutes=duration_minutes,
        interval_seconds=interval_seconds,
        mode=mode,
        kma_observations=kma_obs,
        cloud_patch=cloud_patch,
    )

    bind = session.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    nowcaster = KmaQuantileNowcaster(engine=engine)

    return nowcaster.predict(site=site, request=request, scene_id=scene_id, now=st_dt)
