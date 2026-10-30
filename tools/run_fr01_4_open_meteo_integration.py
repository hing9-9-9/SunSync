"""Run the local-only FR-01.4 Open-Meteo integration smoke test.

Creates an ignored SQLite database and summary under ``artifacts/local``.
It intentionally uses the real provider once through the collection worker;
the subsequent API request is guarded so it cannot make provider HTTP calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from app.db import init_db
from app.main import create_app
from app.models.scene import SceneRecord
from app.nowcast import adapters
from app.nowcast.open_meteo_worker import run_once
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, create_engine

SEOUL_CITY_HALL = (37.5665, 126.9780, "Asia/Seoul")


def _utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _make_scene() -> dict[str, object]:
    source = ROOT / "docs" / "examples" / "fr06_scene_example.json"
    scene = json.loads(source.read_text(encoding="utf-8"))
    scene["scene_id"] = f"local_open_meteo_{uuid4().hex[:8]}"
    scene["site"] = {
        "latitude_deg": SEOUL_CITY_HALL[0],
        "longitude_deg": SEOUL_CITY_HALL[1],
        "timezone": SEOUL_CITY_HALL[2],
        "source": "local_open_meteo_integration",
    }
    scene.pop("weather", None)
    scene.pop("scenario", None)
    return scene


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    run_dir = ROOT / "artifacts" / "local" / "fr01_4_open_meteo" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    database_path = run_dir / "integration.db"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    init_db(engine)
    scene = _make_scene()
    with Session(engine) as session:
        session.add(SceneRecord(scene_id=scene["scene_id"], revision=scene["revision"], scene_json=json.dumps(scene)))
        session.commit()

    collected_at = datetime.now(UTC)
    worker = run_once(engine, now=collected_at, owner_id=f"local-{run_id}")
    if worker != {"sites": 1, "stored": 1, "failed": 0, "lease_acquired": 1}:
        raise RuntimeError(f"worker failed: {worker}")
    with engine.connect() as connection:
        snapshot = connection.execute(text("""SELECT snapshot_id, fetched_at, provenance_json
            FROM weather_snapshot WHERE provider='open_meteo'""")).mappings().one()
        point = connection.execute(text("""SELECT valid_time, interval_start, interval_end, dni_W_m2
            FROM weather_point WHERE snapshot_id=:snapshot_id AND dni_W_m2 IS NOT NULL
            ORDER BY interval_start LIMIT 1"""), {"snapshot_id": snapshot["snapshot_id"]}).mappings().one()
        last_interval_end = connection.execute(text("""SELECT MAX(interval_end) FROM weather_point
            WHERE snapshot_id=:snapshot_id"""), {"snapshot_id": snapshot["snapshot_id"]}).scalar_one()
    provenance = json.loads(snapshot["provenance_json"])
    interval_start = datetime.fromisoformat(point["interval_start"])
    interval_end = datetime.fromisoformat(point["interval_end"])
    if not interval_start < interval_end or interval_end - interval_start != timedelta(hours=1):
        raise RuntimeError("stored hourly interval is invalid")
    if provenance["request_latitude_deg"] != SEOUL_CITY_HALL[0] or provenance["request_longitude_deg"] != SEOUL_CITY_HALL[1]:
        raise RuntimeError("request-coordinate provenance is invalid")

    def no_provider_request(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("provider HTTP called during prediction request")

    adapters.urlopen = no_provider_request  # type: ignore[assignment]
    with TestClient(create_app(engine)) as client:
        response = client.post(
            f"/api/scenes/{scene['scene_id']}/sunpath-predictions",
            json={"start_time": _utc_z(interval_start), "duration_minutes": 5, "interval_seconds": 300},
        )
        fallback_response = client.post(
            f"/api/scenes/{scene['scene_id']}/sunpath-predictions",
            json={"start_time": _utc_z(datetime.fromisoformat(last_interval_end) + timedelta(minutes=5)), "duration_minutes": 5, "interval_seconds": 300},
        )
    if response.status_code != 200:
        raise RuntimeError(f"prediction failed: {response.status_code} {response.text}")
    if fallback_response.status_code != 200:
        raise RuntimeError(f"fallback prediction failed: {fallback_response.status_code} {fallback_response.text}")
    body = response.json()
    irradiance = body["irradiance"]
    open_meteo = [entry for entry in irradiance if entry["source"] == "open_meteo"]
    fallback = [entry for entry in fallback_response.json()["irradiance"] if entry["source"] in {"pvlib_clear_sky", "unknown"}]
    if body["result_version"] != "0.3.0" or not open_meteo or not fallback:
        raise RuntimeError("expected v0.3 Open-Meteo selection and out-of-coverage fallback")
    selected = open_meteo[0]
    if selected["interval_start"] != _utc_z(interval_start) or selected["interval_end"] != _utc_z(interval_end):
        raise RuntimeError("API interval provenance does not match stored point")
    summary = {
        "run_id": run_id,
        "site": {"latitude_deg": SEOUL_CITY_HALL[0], "longitude_deg": SEOUL_CITY_HALL[1]},
        "worker": worker,
        "snapshot_id": snapshot["snapshot_id"],
        "fetched_at": snapshot["fetched_at"],
        "interval": {"start": point["interval_start"], "end": point["interval_end"], "dni_W_m2": point["dni_W_m2"]},
        "provenance": provenance,
        "api": {"result_version": body["result_version"], "open_meteo_entries": len(open_meteo), "fallback_entries": len(fallback)},
        "result": "PASS",
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": "PASS", "run_dir": str(run_dir), "snapshot_id": snapshot["snapshot_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
