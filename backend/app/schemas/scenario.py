"""Response schema for the precomputed scenario bundle endpoint."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.adaptive_control import AdaptiveShadingPlanResponse
from app.schemas.shading_plan import ShadingPlan
from app.schemas.sunpath import SunpathPredictionResponse


class SceneScenarioResponse(BaseModel):
    """Bundled FR-01 sunpath, FR-02 shading plan, and FR-04 adaptive preview snapshot."""

    scene_id: str
    scene_revision: int
    snapshot_key: str
    time_bucket: str
    weather_fingerprint: str
    computed_at: str
    sunpath_prediction: SunpathPredictionResponse
    shading_plan: ShadingPlan | None = None
    adaptive_plan: AdaptiveShadingPlanResponse | None = None
