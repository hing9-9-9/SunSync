"""KMA GK2A adapter, local artifact, and scheduled-worker contracts."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import pytest
from app.db import init_db
from app.models.scene import SceneRecord
from app.nowcast import kma_worker
from app.nowcast.kma_adapter import (
    KmaGk2aAdapter,
    KmaGk2aError,
    build_kma_snapshot,
    parse_kma_wide_payload,
)
from app.nowcast.kma_collection_queue import (
    finalize_queue,
    prepare_queue,
    queue_summary,
    run_queue,
)
from app.nowcast.kma_dataset import write_kma_collection
from sqlalchemy import text
from sqlmodel import Session, create_engine

from engine.fr01.models import Site

START = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)
END = datetime(2026, 11, 5, 3, 20, tzinfo=UTC)
FETCHED = datetime(2026, 11, 6, 1, 0, tzinfo=UTC)
SITE = Site(37.5714, 126.9658, "Asia/Seoul")
EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "fr06_scene_example.json"


def payload(variable: str, values: tuple[str, ...] = ("505.7", "451.2", "451.4")) -> bytes:
    timestamps = ("202611050300", "202611050310", "202611050320")
    header = "  |  ".join(("TMFC", "VAR", "LON", "LAT", *timestamps))
    data = "  |  ".join(("202611050300", variable, "126.9658", "37.5714", *values))
    width = max(len(header), len(data)) + 6
    return (f"{'=' * width}\n|  {header}  |\n{'=' * width}\n|  {data}  |\n{'=' * width}\n").encode()


def range_payload(variable: str, start: datetime, end: datetime) -> bytes:
    timestamps: list[str] = []
    values: list[str] = []
    current = start
    while current <= end:
        timestamps.append(current.strftime("%Y%m%d%H%M"))
        values.append("0.5" if variable == "CA" else "500.0")
        current += timedelta(minutes=10)
    header = "  |  ".join(("TMFC", "VAR", "LON", "LAT", *timestamps))
    data = "  |  ".join((timestamps[0], variable, "126.9658", "37.5714", *values))
    width = max(len(header), len(data)) + 6
    return (f"{'=' * width}\n|  {header}  |\n{'=' * width}\n|  {data}  |\n{'=' * width}\n").encode()


def collection():
    dsr_payload = payload("DSR")
    cloud_payload = payload("CA", ("0.8", "1.0", "0.9"))
    dsr = parse_kma_wide_payload(dsr_payload, "DSR")
    cloud = parse_kma_wide_payload(cloud_payload, "CA")
    snapshot = build_kma_snapshot(SITE, START, END, FETCHED, dsr_payload, cloud_payload, dsr, cloud)
    from app.nowcast.kma_adapter import KmaCollection

    return KmaCollection(
        site=SITE,
        start_time=START,
        end_time=END,
        fetched_at=FETCHED,
        dsr_payloads=(dsr_payload,),
        cloud_payloads=(cloud_payload,),
        dsr=dsr,
        cloud=cloud,
        snapshot=snapshot,
    )


class Response:
    status = 200

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


def test_parser_normalizes_wide_table_and_cloud_fraction() -> None:
    dsr = parse_kma_wide_payload(payload("DSR"), "DSR")
    cloud = parse_kma_wide_payload(payload("CA", ("0.8", "1.0", "")), "CA")
    assert dsr.variable == "DSR"
    assert dsr.values[0] == (START, 505.7)
    assert dsr.values[-1][0] == END
    assert cloud.values[-1] == (END, None)


@pytest.mark.parametrize("missing", ["nan", "6553.5", "-999", ""])
def test_parser_preserves_provider_missing_tokens_as_null(missing: str) -> None:
    dsr = parse_kma_wide_payload(payload("DSR", ("505.7", missing, "0.0")), "DSR")
    assert dsr.values[1][1] is None
    assert dsr.values[2][1] == 0.0


def test_parser_rejects_non_ten_minute_but_preserves_out_of_range_cloud() -> None:
    bad_interval = payload("CA").replace(b"202611050310", b"202611050315")
    with pytest.raises(KmaGk2aError, match="kma_unexpected_interval"):
        parse_kma_wide_payload(bad_interval, "CA")
    cloud = parse_kma_wide_payload(payload("CA", ("0.8", "1.1", "0.9")), "CA")
    assert cloud.values[1] == (START + timedelta(minutes=10), 1.1)


def test_adapter_fetches_both_variables_without_exposing_key() -> None:
    calls: list[dict[str, list[str]]] = []

    def opener(url: str, **_kwargs):
        query = parse_qs(urlparse(url).query)
        calls.append(query)
        variable = query["varn"][0]
        return Response(
            payload(variable, ("0.8", "1.0", "0.9")) if variable == "CA" else payload(variable)
        )

    result = KmaGk2aAdapter(SITE, "secret-key", opener=opener).fetch_range(
        START, END, fetched_at=FETCHED
    )
    assert sorted(call["varn"][0] for call in calls) == ["CA", "DSR"]
    assert all(call["int"] == ["10"] for call in calls)
    assert len(result.snapshot.points) == 3
    assert result.snapshot.points[0].ghi_W_m2 == 505.7
    assert result.snapshot.points[0].cloud_cover_percent == 80.0
    assert "secret-key" not in repr(result)
    assert "secret-key" not in repr(KmaGk2aAdapter(SITE, "secret-key"))


def test_adapter_retries_sanitized_transport_failure() -> None:
    sleeps: list[float] = []

    def offline(*_args, **_kwargs):
        raise URLError("request URL intentionally omitted")

    adapter = KmaGk2aAdapter(
        SITE,
        "secret-key",
        max_attempts=2,
        opener=offline,
        sleeper=sleeps.append,
    )
    with pytest.raises(KmaGk2aError, match="kma_transport_error") as error:
        adapter.fetch_range(START, END, fetched_at=FETCHED)
    assert str(error.value) == "kma_transport_error"
    assert sorted(sleeps) == [1.0, 1.0]


def test_adapter_chunks_ranges_at_provider_twenty_four_point_limit() -> None:
    calls: list[tuple[str, datetime, datetime]] = []

    def opener(url: str, **_kwargs):
        query = parse_qs(urlparse(url).query)
        variable = query["varn"][0]
        start = datetime.strptime(query["tm1"][0], "%Y%m%d%H%M").replace(tzinfo=UTC)
        end = datetime.strptime(query["tm2"][0], "%Y%m%d%H%M").replace(tzinfo=UTC)
        calls.append((variable, start, end))
        return Response(range_payload(variable, start, end))

    end = START + timedelta(hours=4)
    result = KmaGk2aAdapter(SITE, "secret-key", opener=opener).fetch_range(
        START, end, fetched_at=FETCHED
    )
    assert len(result.snapshot.points) == 25
    assert len(result.dsr_payloads) == len(result.cloud_payloads) == 2
    assert sorted(variable for variable, _start, _end in calls) == ["CA", "CA", "DSR", "DSR"]
    assert all(
        (chunk_end - chunk_start) <= timedelta(minutes=230) for _, chunk_start, chunk_end in calls
    )


def test_artifact_writer_preserves_raw_and_creates_readable_csv(tmp_path: Path) -> None:
    result = collection()
    paths = write_kma_collection(result, tmp_path / "data" / "fr01")
    assert paths.dsr_raw[0].read_bytes() == result.dsr_payload
    assert paths.cloud_raw[0].read_bytes() == result.cloud_payload
    csv_text = paths.normalized_csv.read_text(encoding="utf-8")
    assert "timestamp_utc" in csv_text
    assert "2026-11-05T03:00:00Z" in csv_text
    assert ",505.7,0.8,0.8,80.0,W/m2,true,ok" in csv_text
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    assert manifest["row_count"] == 3
    assert manifest["unit_confirmed"] is True
    assert manifest["dsr_unit"] == "W/m2"
    assert "authKey" not in paths.manifest.read_text(encoding="utf-8")


def test_artifact_writer_preserves_invalid_cloud_raw_but_nulls_fraction(
    tmp_path: Path,
) -> None:
    dsr_payload = payload("DSR")
    cloud_payload = payload("CA", ("0.8", "1.1", "0.9"))
    dsr = parse_kma_wide_payload(dsr_payload, "DSR")
    cloud = parse_kma_wide_payload(cloud_payload, "CA")
    snapshot = build_kma_snapshot(SITE, START, END, FETCHED, dsr_payload, cloud_payload, dsr, cloud)
    from app.nowcast.kma_adapter import KmaCollection

    result = KmaCollection(
        site=SITE,
        start_time=START,
        end_time=END,
        fetched_at=FETCHED,
        dsr_payloads=(dsr_payload,),
        cloud_payloads=(cloud_payload,),
        dsr=dsr,
        cloud=cloud,
        snapshot=snapshot,
    )
    paths = write_kma_collection(result, tmp_path / "data" / "fr01")
    rows = paths.normalized_csv.read_text(encoding="utf-8").splitlines()
    assert "ca_raw_provider" in rows[0]
    assert ",451.2,1.1,,,W/m2,true,ca_out_of_range" in rows[2]
    assert snapshot.points[1].cloud_cover_percent is None
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    assert manifest["valid_cloud_count"] == 2
    assert manifest["out_of_range_cloud_count"] == 1


def test_worker_stores_snapshot_and_request_path_does_not_call_worker(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'kma-worker.db'}")
    init_db(engine)
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    with Session(engine) as session:
        session.add(
            SceneRecord(
                scene_id=document["scene_id"],
                revision=document["revision"],
                scene_json=json.dumps(document),
            )
        )
        session.commit()

    class Adapter:
        def fetch_recent(self, _now):
            return collection()

    result = kma_worker.run_once(
        engine,
        api_key="secret-key",
        output_root=tmp_path / "data" / "fr01",
        now=FETCHED,
        owner_id="test-worker",
        adapter_factory=lambda _site, _key: Adapter(),
    )
    assert result == {"sites": 1, "stored": 1, "failed": 0, "lease_acquired": 1}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM weather_snapshot")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM weather_point")).scalar_one() == 3

    from app.services import sunpath

    assert "app.nowcast.kma_worker" not in inspect.getsource(sunpath)


def test_collector_cli_help_runs_from_repository_root() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "tools/ai_experiments/collect_fr01_kma_data.py",
            "--help",
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--latitude" in result.stdout
    assert "--prepare-only" in result.stdout
    assert "--resume" in result.stdout


def test_durable_queue_prepares_seven_days_without_fetching(tmp_path: Path) -> None:
    start = datetime(2026, 10, 26, 0, 0, tzinfo=UTC)
    end = datetime(2026, 11, 1, 23, 50, tzinfo=UTC)
    output_root = tmp_path / "data" / "fr01"

    queue_path = prepare_queue(
        site=SITE,
        start_time=start,
        end_time=end,
        output_root=output_root,
        created_at=FETCHED,
    )

    journal = json.loads(queue_path.read_text(encoding="utf-8"))
    assert len(journal["tasks"]) == 84
    assert queue_summary(queue_path) == {
        "pending": 84,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "total": 84,
    }
    assert not (output_root / "raw").exists()
    assert "authKey" not in queue_path.read_text(encoding="utf-8")


def test_durable_queue_retries_only_failed_tasks_and_finalizes(tmp_path: Path) -> None:
    output_root = tmp_path / "data" / "fr01"
    queue_path = prepare_queue(
        site=SITE,
        start_time=START,
        end_time=END,
        output_root=output_root,
        created_at=FETCHED,
    )
    calls = {"DSR": 0, "CA": 0}

    class Adapter:
        def fetch_chunk(self, start: datetime, end: datetime, variable: str):
            calls[variable] += 1
            if variable == "DSR" and calls[variable] == 1:
                raise KmaGk2aError("kma_transport_error")
            body = range_payload(variable, start, end)
            return body, parse_kma_wide_payload(body, variable)

    first = run_queue(
        queue_path=queue_path,
        output_root=output_root,
        adapter=Adapter(),  # type: ignore[arg-type]
        max_concurrency=2,
    )
    assert first["succeeded"] == 1
    assert first["failed"] == 1

    second = run_queue(
        queue_path=queue_path,
        output_root=output_root,
        adapter=Adapter(),  # type: ignore[arg-type]
        max_concurrency=2,
        retry_failed=True,
    )
    assert second["succeeded"] == second["total"] == 2
    assert calls == {"DSR": 2, "CA": 1}

    paths = finalize_queue(queue_path=queue_path, output_root=output_root, site=SITE)
    assert paths.normalized_csv.exists()
    assert len(paths.dsr_raw) == len(paths.cloud_raw) == 1
    assert "2026-11-05T03:20:00Z" in paths.normalized_csv.read_text(encoding="utf-8")
