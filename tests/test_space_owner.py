"""Spaces are separated by account: each user sees and changes only their own spaces."""

import pytest
from app import auth
from app.main import create_app
from app.models.auth import AuthUser
from app.models.scene import SceneRecord
from fastapi import Request
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select
from test_ui_spaces import payload  # noqa: F401  (shared fixture)


def user(name: str) -> AuthUser:
    return AuthUser(
        id=name,
        google_sub=name,
        email=f"{name}@example.com",
        name=name,
        created_at="",
        last_login_at="",
    )


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'owner.db'}", connect_args={"check_same_thread": False}
    )
    yield engine
    engine.dispose()


@pytest.fixture
def app(engine):
    application = create_app(engine)

    def who(request: Request) -> AuthUser | None:
        name = request.headers.get("x-test-user")
        return user(name) if name else None

    application.dependency_overrides[auth.current_user] = who
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def as_user(name: str) -> dict:
    return {"x-test-user": name}


ALICE, BOB = as_user("alice"), as_user("bob")


def spaces_of(client, headers) -> list[str]:
    response = client.get("/api/ui/spaces", headers=headers)
    assert response.status_code == 200
    return [space["id"] for space in response.json()["spaces"]]


def test_a_new_space_is_owned_by_its_creator(client, engine, payload):  # noqa: F811
    assert client.post("/api/ui/spaces", json=payload, headers=ALICE).status_code == 201
    with Session(engine) as db:
        assert db.exec(select(SceneRecord)).one().owner_id == "alice"


def test_each_user_lists_only_their_own_spaces(client, payload):  # noqa: F811
    client.post("/api/ui/spaces", json=payload, headers=ALICE)
    client.post("/api/ui/spaces", json={**payload, "id": "bobs-space"}, headers=BOB)
    assert spaces_of(client, ALICE) == ["test-space"]
    assert spaces_of(client, BOB) == ["bobs-space"]


def test_another_user_cannot_open_change_or_delete_a_space(client, payload):  # noqa: F811
    saved = client.post("/api/ui/spaces", json=payload, headers=ALICE).json()
    url = f"/api/ui/spaces/{saved['id']}"
    assert client.get(url, headers=BOB).status_code == 404
    assert client.put(url, json=saved, headers=BOB).status_code == 404
    assert client.delete(url, headers=BOB).status_code == 404
    assert client.get(url, headers=ALICE).status_code == 200  # still there, unchanged


def test_scene_routes_hide_another_users_scene(client, payload):  # noqa: F811
    saved = client.post("/api/ui/spaces", json=payload, headers=ALICE).json()
    scene_id = saved["serverSceneId"]
    window = {"start_time": "2026-06-21T09:00:00+09:00", "duration_minutes": 30}
    own = client.get(f"/api/scenes/{scene_id}", headers=ALICE)
    assert own.status_code == 200
    assert client.get(f"/api/scenes/{scene_id}", headers=BOB).status_code == 404
    assert client.get("/api/scenes", headers=BOB).json() == []
    assert [s["scene_id"] for s in client.get("/api/scenes", headers=ALICE).json()] == [scene_id]
    for path in ("sunpath-predictions", "shading-plans"):
        url = f"/api/scenes/{scene_id}/{path}"
        assert client.post(url, json=window, headers=BOB).status_code == 404
        assert client.post(url, json=window, headers=ALICE).status_code != 404
    assert client.get(f"/api/scenes/{scene_id}/film-state", headers=BOB).status_code == 404
    assert client.get(f"/api/scenes/{scene_id}/film-state", headers=ALICE).status_code == 200
    assert client.get(f"/api/scenes/{scene_id}/ai-nowcast/latest", headers=BOB).status_code == 404


def test_handoff_belongs_to_the_account_of_its_space(client, payload):  # noqa: F811
    saved = client.post("/api/ui/spaces", json=payload, headers=ALICE).json()
    start = {"space_id": saved["id"]}
    assert client.post("/api/ui/handoffs", json=start, headers=BOB).status_code == 404
    handoff = client.post("/api/ui/handoffs", json=start, headers=ALICE).json()
    token, code = handoff["token"], handoff["code"]
    assert client.get(f"/api/ui/handoffs/{token}", headers=BOB).status_code == 404
    assert client.post(f"/api/ui/handoffs/{token}/connect", headers=BOB).status_code == 404
    assert (
        client.post("/api/ui/handoffs/claim", json={"code": code}, headers=BOB).status_code == 404
    )
    # the same account on the phone gets through
    assert (
        client.post("/api/ui/handoffs/claim", json={"code": code}, headers=ALICE).status_code == 200
    )
    assert client.get(f"/api/ui/handoffs/{token}", headers=ALICE).status_code == 200


def test_measurement_jobs_belong_to_the_account_of_their_space(client, payload):  # noqa: F811
    saved = client.post("/api/ui/spaces", json=payload, headers=ALICE).json()
    start = {"space_id": saved["id"]}
    assert client.post("/api/ui/measurements", json=start, headers=BOB).status_code == 404
    job = client.post("/api/ui/measurements", json=start, headers=ALICE).json()
    url = f"/api/ui/measurements/{job['token']}"
    assert client.get(url, headers=BOB).status_code == 404
    assert client.post(f"{url}/connect", headers=BOB).status_code == 404
    assert client.get("/api/ui/measurements", headers=BOB).json() == []
    assert len(client.get("/api/ui/measurements", headers=ALICE).json()) == 1
    assert client.get(url, headers=ALICE).status_code == 200


def test_a_space_without_an_owner_is_visible_to_nobody(client, engine, payload):  # noqa: F811
    saved = client.post("/api/ui/spaces", json=payload).json()  # login off: no owner
    assert spaces_of(client, ALICE) == []
    assert client.get(f"/api/ui/spaces/{saved['id']}", headers=ALICE).status_code == 404
    assert client.get(f"/api/scenes/{saved['serverSceneId']}", headers=ALICE).status_code == 404


def test_without_a_login_nothing_is_filtered(client, payload):  # noqa: F811
    client.post("/api/ui/spaces", json=payload, headers=ALICE)
    assert spaces_of(client, {}) == ["test-space"]


def test_the_owner_survives_a_new_revision_and_a_restart(app, engine, payload):  # noqa: F811
    with TestClient(app) as first:
        saved = first.post("/api/ui/spaces", json=payload, headers=ALICE).json()
        saved["name"] = "서재 2"
        assert (
            first.put(f"/api/ui/spaces/{saved['id']}", json=saved, headers=ALICE).status_code == 200
        )
    with TestClient(app) as second:  # the application starts again on the same database
        assert spaces_of(second, ALICE) == ["test-space"]
        assert spaces_of(second, BOB) == []


def test_the_migration_adds_the_owner_column_to_an_old_database(tmp_path):
    from app.db import init_db

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE scenes (scene_id TEXT PRIMARY KEY, revision INTEGER, scene_json TEXT)"
        )
        connection.exec_driver_sql("INSERT INTO scenes VALUES ('s1', 1, '{}')")
        connection.exec_driver_sql("PRAGMA user_version = 4")
    init_db(engine)
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("SELECT scene_id, owner_id FROM scenes").all()
    assert rows == [("s1", None)]


def test_assign_owner_command_gives_unowned_spaces_to_one_account(client, engine, payload):  # noqa: F811
    from app.assign_owner import assign_unowned

    client.post("/api/ui/spaces", json=payload)  # no owner
    client.post("/api/ui/spaces", json={**payload, "id": "bobs"}, headers=BOB)
    with Session(engine) as db:
        db.add(user("alice"))
        db.commit()
        assert assign_unowned(db, "alice@example.com", apply=False) == 1  # report only
    assert spaces_of(client, ALICE) == []
    with Session(engine) as db:
        assert assign_unowned(db, "alice@example.com", apply=True) == 1
    assert spaces_of(client, ALICE) == ["test-space"]
    assert spaces_of(client, BOB) == ["bobs"]  # an owned space is not taken over
    with Session(engine) as db, pytest.raises(SystemExit):
        assign_unowned(db, "nobody@example.com", apply=True)


def test_each_user_gets_only_the_space_ids_they_deleted(client, payload):  # noqa: F811
    client.post("/api/ui/spaces", json=payload, headers=ALICE)
    client.post("/api/ui/spaces", json={**payload, "id": "bobs-space"}, headers=BOB)
    assert client.delete("/api/ui/spaces/test-space", headers=ALICE).status_code == 204
    assert client.delete("/api/ui/spaces/bobs-space", headers=BOB).status_code == 204
    alice = client.get("/api/ui/spaces", headers=ALICE).json()
    bob = client.get("/api/ui/spaces", headers=BOB).json()
    assert alice["deleted"] == ["test-space"]
    assert bob["deleted"] == ["bobs-space"]
    # login off (local development): nothing is filtered
    assert set(client.get("/api/ui/spaces").json()["deleted"]) == {"test-space", "bobs-space"}


def test_a_deleted_id_stays_closed_to_everyone(client, payload):  # noqa: F811
    client.post("/api/ui/spaces", json=payload, headers=ALICE)
    client.delete("/api/ui/spaces/test-space", headers=ALICE)
    assert client.post("/api/ui/spaces", json=payload, headers=BOB).status_code == 409
    assert client.post("/api/ui/spaces", json=payload, headers=ALICE).status_code == 409


def test_the_migration_adds_the_owner_column_to_deleted_ids(tmp_path):
    from app.db import init_db

    engine = create_engine(f"sqlite:///{tmp_path / 'old_deleted.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ui_deleted_spaces (space_id TEXT PRIMARY KEY, deleted_at TEXT)"
        )
        connection.exec_driver_sql("INSERT INTO ui_deleted_spaces VALUES ('gone', 'x')")
        connection.exec_driver_sql("PRAGMA user_version = 5")
    init_db(engine)
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("SELECT space_id, owner_id FROM ui_deleted_spaces").all()
    assert rows == [("gone", None)]


def test_deleted_ids_without_an_owner_follow_the_assign_owner_command(client, engine, payload):  # noqa: F811
    from app.assign_owner import assign_unowned

    client.post("/api/ui/spaces", json=payload)  # login off: no owner
    client.delete("/api/ui/spaces/test-space")
    assert client.get("/api/ui/spaces", headers=ALICE).json()["deleted"] == []
    with Session(engine) as db:
        db.add(user("alice"))
        db.commit()
        assign_unowned(db, "alice@example.com", apply=True)
    assert client.get("/api/ui/spaces", headers=ALICE).json()["deleted"] == ["test-space"]
    assert client.get("/api/ui/spaces", headers=BOB).json()["deleted"] == []
