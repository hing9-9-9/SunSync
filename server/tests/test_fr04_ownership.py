"""The FR-04 API follows the owner of the space: another account cannot reach a space's plans."""

import pytest
from app import auth
from app.main import create_app
from app.models.auth import AuthUser
from fastapi import Request
from fastapi.testclient import TestClient
from sqlmodel import create_engine
from test_fr04_adaptive_control import scene_payload  # noqa: F401  (shared fixture)


def user(name):
    return AuthUser(
        id=name,
        google_sub=name,
        email=f"{name}@example.com",
        name=name,
        created_at="",
        last_login_at="",
    )


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'c.db'}", connect_args={"check_same_thread": False}
    )
    app = create_app(engine)

    def who(request: Request):
        name = request.headers.get("x-test-user")
        return user(name) if name else None

    app.dependency_overrides[auth.current_user] = who
    with TestClient(app) as c:
        yield c
    engine.dispose()


A, B = {"x-test-user": "alice"}, {"x-test-user": "bob"}
REQ = {"start_time": "2026-10-14T04:30:00Z", "duration_minutes": 120, "interval_seconds": 300}


def segs(plan):
    return sum(len(c["segments"]) for w in plan["windows"] for c in w["cells"])


def test_adaptive_plan_follows_the_space_owner(client, scene_payload):  # noqa: F811
    sid = client.post("/api/scenes", json=scene_payload, headers=A).json()["scene_id"]
    # the owner gets a real plan in both modes, the thinq sync works
    for mode in ("smooth_fr02", "replace"):
        r = client.post(
            f"/api/scenes/{sid}/adaptive-shading-plan", json={**REQ, "mode": mode}, headers=A
        )
        assert r.status_code == 200, r.text
        assert segs(r.json()["baseline_plan"]) > 0 and segs(r.json()["stabilized_plan"]) > 0
    sync = {"target_timestamp": "2026-10-14T04:50:00Z", "plan_request": REQ}
    assert client.post(f"/api/scenes/{sid}/thinq-hub-sync", json=sync, headers=A).status_code == 200
    # another account cannot reach it in any way
    for path, body in (
        (f"/api/scenes/{sid}/adaptive-shading-plan", REQ),
        (f"/api/scenes/{sid}/thinq-hub-sync", sync),
        (f"/api/scenes/{sid}/shading-plans", REQ),
    ):
        assert client.post(path, json=body, headers=B).status_code == 404, path
    # apply_to_virtual_film as the other account must not write the film state
    r = client.post(
        f"/api/scenes/{sid}/adaptive-shading-plan",
        json={**REQ, "apply_to_virtual_film": True},
        headers=B,
    )
    assert r.status_code == 404
    assert client.get(f"/api/scenes/{sid}/film-state", headers=B).status_code == 404


def test_unowned_scene_is_closed_to_everybody_with_login_on(client, scene_payload):  # noqa: F811
    sid = client.post("/api/scenes", json=scene_payload).json()[
        "scene_id"
    ]  # login off at creation: no owner
    assert (
        client.post(f"/api/scenes/{sid}/adaptive-shading-plan", json=REQ, headers=A).status_code
        == 404
    )


def test_login_off_nothing_changes(client, scene_payload):  # noqa: F811
    sid = client.post("/api/scenes", json=scene_payload).json()["scene_id"]
    assert client.post(f"/api/scenes/{sid}/adaptive-shading-plan", json=REQ).status_code == 200


def test_unknown_scene_is_404_for_everybody(client):
    assert (
        client.post("/api/scenes/nope/adaptive-shading-plan", json=REQ, headers=A).status_code
        == 404
    )
