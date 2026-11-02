"""FR-02 endpoint integration tests over real FR-06 Scene storage."""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.main import create_app
from app.schemas.shading_plan import ShadingPlan, ShadingPlanRequest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import create_engine

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"
STEP_SECONDS = 60


@pytest.fixture
def scene_payload() -> dict[str, Any]:
    return json.loads((EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8"))


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'shading.db'}", connect_args={"check_same_thread": False}
    )
    yield engine
    engine.dispose()


@pytest.fixture
def client(database_engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(database_engine)) as test_client:
        yield test_client


def create_scene(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/scenes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def request_body(**changes: object) -> dict[str, object]:
    # In the example Scene only region_B receives direct sun on this day, from about 04:48 UTC.
    result: dict[str, object] = {
        "start_time": "2026-10-14T04:30:00Z",
        "duration_minutes": 30,
        "interval_seconds": STEP_SECONDS,
    }
    result.update(changes)
    return result


def plan_url(scene: dict[str, Any]) -> str:
    return f"/api/scenes/{scene['scene_id']}/shading-plans"


def segments(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        segment
        for window in plan["windows"]
        for cell in window["cells"]
        for segment in cell["segments"]
    ]


def test_plan_is_a_valid_shading_plan_of_the_stored_scene(client, scene_payload):
    original = create_scene(client, scene_payload)
    response = client.post(plan_url(scene_payload), json=request_body(expected_scene_revision=2))
    assert response.status_code == 200, response.text
    plan = response.json()
    ShadingPlan.model_validate(plan)
    window = scene_payload["windows"][0]
    assert plan["scene_id"] == scene_payload["scene_id"]
    assert plan["scene_revision"] == 2
    assert [(w["window_id"], w["film_rows"], w["film_cols"]) for w in plan["windows"]] == [
        (window["window_id"], window.get("film_rows", 10), window.get("film_cols", 10))
    ]
    assert segments(plan)
    assert all(
        s["state"] == "opaque"
        and [(r["region_id"], r["grade"]) for r in s["reasons"]] == [("region_B", "B")]
        for s in segments(plan)
    )
    assert client.get(f"/api/scenes/{scene_payload['scene_id']}").json() == original


def test_plan_starts_one_step_before_the_first_exposure_of_fr01(client, scene_payload):
    create_scene(client, scene_payload)
    prediction = client.post(
        f"/api/scenes/{scene_payload['scene_id']}/sunpath-predictions", json=request_body()
    ).json()
    first = next(
        datetime.fromisoformat(region["first_predicted_exposure_time"])
        for region in prediction["regions"]
        if region["region_id"] == "region_B"
    )
    plan = client.post(plan_url(scene_payload), json=request_body()).json()
    earliest = min(datetime.fromisoformat(s["start_time"]) for s in segments(plan))
    assert earliest == first - timedelta(seconds=STEP_SECONDS)
    assert plan["start_time"] == "2026-10-14T04:30:00Z"
    assert plan["end_time"] == "2026-10-14T05:00:00Z"


def test_a_grade_a_region_is_shaded_whatever_the_intensity(client, scene_payload):
    for region in scene_payload["protected_regions"]:
        if region["region_id"] == "region_B":
            region["grade"] = "A"
    create_scene(client, scene_payload)
    plan = client.post(plan_url(scene_payload), json=request_body()).json()
    assert segments(plan)
    assert all(
        s["state"] == "opaque"
        and [(r["region_id"], r["grade"]) for r in s["reasons"]] == [("region_B", "A")]
        for s in segments(plan)
    )


def test_a_film_that_cannot_block_fully_cannot_meet_grade_a(client, scene_payload):
    for region in scene_payload["protected_regions"]:
        if region["region_id"] == "region_B":
            region["grade"] = "A"
    scene_payload["optical_profiles"][0]["film_min_direct_transmittance"] = 0.1
    create_scene(client, scene_payload)
    plan = client.post(plan_url(scene_payload), json=request_body()).json()
    assert segments(plan)
    assert {s["state"] for s in segments(plan)} == {"infeasible"}


def test_no_sun_on_the_protected_areas_gives_an_empty_plan(client, scene_payload):
    create_scene(client, scene_payload)
    plan = client.post(
        plan_url(scene_payload), json=request_body(start_time="2026-10-14T12:00:00Z")
    )
    assert plan.status_code == 200, plan.text
    assert segments(plan.json()) == []
    assert len(plan.json()["windows"]) == 1


def test_start_time_accepts_the_same_formats_as_fr01(client, scene_payload):
    create_scene(client, scene_payload)
    response = client.post(
        plan_url(scene_payload), json=request_body(start_time="2026-10-14T04:30+00:00")
    )
    assert response.status_code == 200, response.text
    assert response.json()["start_time"] == "2026-10-14T04:30:00Z"


def test_the_default_time_step_is_the_one_of_fr01():
    assert ShadingPlanRequest(start_time="2026-10-14T04:30:00Z").interval_seconds == 300


def test_unknown_scene_is_404(client):
    response = client.post("/api/scenes/scene_missing/shading-plans", json=request_body())
    assert response.status_code == 404


def test_revision_conflict_is_409(client, scene_payload):
    create_scene(client, scene_payload)
    response = client.post(plan_url(scene_payload), json=request_body(expected_scene_revision=99))
    assert response.status_code == 409


@pytest.mark.parametrize(
    "changes",
    [
        {"start_time": "2026-10-14T04:30:00"},
        {"interval_seconds": 0},
        {"interval_seconds": 3601},
        {"duration_minutes": 0},
        {"duration_minutes": 1441},
        {"protected_region_ids": ["region_A"]},
    ],
    ids=[
        "no_time_zone",
        "interval_too_small",
        "interval_too_large",
        "empty_duration",
        "duration_too_long",
        "region_filter_not_supported",
    ],
)
def test_invalid_requests_are_422(client, scene_payload, changes):
    create_scene(client, scene_payload)
    assert client.post(plan_url(scene_payload), json=request_body(**changes)).status_code == 422


def test_a_scene_without_protected_regions_is_422(client):
    room = json.loads((EXAMPLES / "fr06_manual_room.json").read_text(encoding="utf-8"))
    create_scene(client, room)
    assert client.post(plan_url(room), json=request_body()).status_code == 422


def test_openapi_lists_the_endpoint_and_the_plan_model(client):
    openapi = client.get("/openapi.json").json()
    assert "/api/scenes/{scene_id}/shading-plans" in openapi["paths"]
    assert "ShadingPlan" in openapi["components"]["schemas"]
