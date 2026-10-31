"""FR-01.4 Implementation Step A — Open-Meteo normalization and standalone-worker contracts."""

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

import pytest
from app.db import init_db
from app.models.scene import SceneRecord
from app.nowcast import open_meteo_worker
from app.nowcast.adapters import OpenMeteoDniAdapter, OpenMeteoSnapshotError
from sqlalchemy import text
from sqlmodel import Session, create_engine

from engine.fr01.models import Site

NOW = datetime(2026, 10, 28, 3, 5, tzinfo=UTC)
EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "fr06_scene_example.json"


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.body).encode("utf-8")


def payload(dnis=(0, None), times=(1_760_000_000, 1_760_003_600)):
    return {
        "hourly_units": {"direct_normal_irradiance": "W/m²"},
        "hourly": {
            "time": list(times),
            "direct_normal_irradiance": list(dnis),
            "cloud_cover": [10, 20],
        },
    }


def make_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    init_db(engine)
    return engine


def add_scene(engine, scene_id="scene_demo_001", latitude=None, longitude=None):
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    document["scene_id"] = scene_id
    if latitude is not None:
        document["site"]["latitude_deg"] = latitude
        document["site"]["longitude_deg"] = longitude
    with Session(engine) as session:
        session.add(SceneRecord(scene_id=scene_id, revision=document["revision"], scene_json=json.dumps(document)))
        session.commit()


def test_open_meteo_normalizes_preceding_hour_means_and_preserves_zero(monkeypatch):
    monkeypatch.setattr("app.nowcast.adapters.urlopen", lambda *_args, **_kwargs: Response(payload()))
    raw = OpenMeteoDniAdapter(Site(37.5, 127.0, "Asia/Seoul")).fetch(NOW)
    assert raw.provider == "open_meteo"
    assert raw.points[0].dni_W_m2 == 0.0
    assert raw.points[1].dni_W_m2 is None
    assert raw.points[0].temporal_meaning == "mean"
    assert raw.points[0].interval_end == raw.points[0].valid_time
    assert (raw.points[0].interval_end - raw.points[0].interval_start).total_seconds() == 3600


@pytest.mark.parametrize("body", [b"not-json", payload(times=(1_760_003_600, 1_760_000_000))])
def test_open_meteo_rejects_invalid_json_or_timestamp_order(monkeypatch, body):
    class RawResponse(Response):
        def read(self):
            return body if isinstance(body, bytes) else super().read()

    monkeypatch.setattr("app.nowcast.adapters.urlopen", lambda *_args, **_kwargs: RawResponse(body))
    with pytest.raises(OpenMeteoSnapshotError, match="open_meteo_invalid_response"):
        OpenMeteoDniAdapter(Site(37.5, 127.0, "Asia/Seoul")).fetch(NOW)


def test_open_meteo_timeout_is_not_stored(monkeypatch, tmp_path):
    engine = make_engine(tmp_path)
    add_scene(engine)
    monkeypatch.setattr(
        "app.nowcast.adapters.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline"))
    )
    assert open_meteo_worker.run_once(engine, now=NOW, owner_id="a") == {
        "sites": 1, "stored": 0, "failed": 1, "lease_acquired": 1
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM weather_snapshot")).scalar_one() == 0


def test_worker_deduplicates_sites_and_provider_hashes(monkeypatch, tmp_path):
    engine = make_engine(tmp_path)
    add_scene(engine, "scene_demo_001")
    add_scene(engine, "scene_demo_002")
    calls = 0

    def open_url(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return Response(payload(dnis=(0, 200)))

    monkeypatch.setattr("app.nowcast.adapters.urlopen", open_url)
    assert open_meteo_worker.run_once(engine, now=NOW, owner_id="one")["stored"] == 1
    # The lease is intentionally held by another process until its TTL expires.
    assert open_meteo_worker.run_once(engine, now=NOW, owner_id="two")["lease_acquired"] == 0
    assert open_meteo_worker.run_once(engine, now=NOW, owner_id="one")["stored"] == 1
    assert calls == 2
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM weather_snapshot")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM weather_point")).scalar_one() == 2


def test_request_path_does_not_invoke_open_meteo_collection_worker(monkeypatch):
    # Step D may read durable snapshots, but API requests must never invoke the
    # Open-Meteo collection process directly.
    from app.services import sunpath

    monkeypatch.setattr(open_meteo_worker, "run_once", lambda *_args, **_kwargs: pytest.fail("worker invoked"))
    assert "app.nowcast.open_meteo_worker" not in inspect.getsource(sunpath)
