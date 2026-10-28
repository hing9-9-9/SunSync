"""FR-01.4 Implementation Step B — GK2A DSR point adapter and worker contracts."""

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

import pytest
from app.db import init_db
from app.models.scene import SceneRecord
from app.nowcast import gk2a_worker
from app.nowcast.adapters import Gk2aDsrAdapter, Gk2aDsrError
from sqlalchemy import text
from sqlmodel import Session, create_engine

from engine.fr01.models import Site

NOW = datetime(2026, 10, 28, 3, 5, tzinfo=UTC)
EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "fr06_scene_example.json"


class Response:
    status = 200

    def __init__(self, body: str):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body.encode()


def valid_table() -> str:
    return (
        "| TMFC | VAR | LON | LAT | 202610270250 | 202610270300 | 202610270310 |\n"
        "| 202610270250 | DSR | 127.0100 | 37.5100 | 0 | - | 123.4 |\n"
    )


def engine_with_scene(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'gk2a.db'}")
    init_db(engine)
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    with Session(engine) as session:
        session.add(SceneRecord(scene_id=document["scene_id"], revision=document["revision"], scene_json=json.dumps(document)))
        session.commit()
    return engine


def test_gk2a_parses_header_order_zero_and_missing_and_keeps_spatial_limit(monkeypatch):
    monkeypatch.setattr("app.nowcast.adapters.urlopen", lambda *_args, **_kwargs: Response(valid_table()))
    snapshot = Gk2aDsrAdapter(Site(37.5, 127.0, "Asia/Seoul"), "secret").fetch(NOW)
    assert [point.ghi_W_m2 for point in snapshot.points] == [0.0, None, 123.4]
    assert all(point.temporal_meaning == "instant" for point in snapshot.points)
    assert snapshot.observed_at == datetime(2026, 10, 27, 3, 10, tzinfo=UTC)
    assert snapshot.points[0].latitude_deg == 37.51
    assert snapshot.points[0].longitude_deg == 127.01
    assert snapshot.provenance["request_to_representative_distance_km"] > 0


@pytest.mark.parametrize(
    "body,reason",
    [
        ("authentication denied", "gk2a_auth_error"),
        ("<Error>upstream failure</Error>", "gk2a_provider_error"),
        ("자료 없음", "gk2a_no_data"),
        ("| TMFC | VAR | LON | LAT |\n", "gk2a_no_data"),
        ("| TMFC | VAR | LON | LAT | 202610270250 |\n| x | DSR | 127 | 37 | bad |\n", "gk2a_invalid_response"),
        ("| TMFC | VALUE | LON | LAT | 202610270250 |\n| x | DSR | 127 | 37 | 1 |\n", "gk2a_invalid_response"),
    ],
)
def test_gk2a_classifies_non_data_and_malformed_responses(monkeypatch, body, reason):
    monkeypatch.setattr("app.nowcast.adapters.urlopen", lambda *_args, **_kwargs: Response(body))
    with pytest.raises(Gk2aDsrError, match=reason):
        Gk2aDsrAdapter(Site(37.5, 127.0, "Asia/Seoul"), "secret").fetch(NOW)


def test_gk2a_transport_failure_and_missing_key_do_not_store(monkeypatch, tmp_path):
    engine = engine_with_scene(tmp_path)
    assert gk2a_worker.run_once(engine, api_key=None, now=NOW)["blocked"] == 1
    monkeypatch.setattr(
        "app.nowcast.adapters.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline"))
    )
    result = gk2a_worker.run_once(engine, api_key="secret", now=NOW, owner_id="one")
    assert result == {"sites": 1, "stored": 0, "failed": 1, "lease_acquired": 1, "blocked": 0}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM weather_snapshot")).scalar_one() == 0


def test_gk2a_worker_stores_ghi_and_provenance_without_secret(monkeypatch, tmp_path):
    engine = engine_with_scene(tmp_path)
    monkeypatch.setattr("app.nowcast.adapters.urlopen", lambda *_args, **_kwargs: Response(valid_table()))
    assert gk2a_worker.run_once(engine, api_key="secret", now=NOW, owner_id="one")["stored"] == 1
    with engine.connect() as connection:
        point = connection.execute(text("SELECT dni_W_m2, ghi_W_m2 FROM weather_point")).first()
        provenance = connection.execute(text("SELECT provenance_json FROM weather_snapshot")).scalar_one()
        assert point == (None, 0.0)
        assert "secret" not in provenance
        assert json.loads(provenance)["spatial_representation"] == "representative_coordinate_from_response"
