"""Collection workers: failure reasons in the log, one retry, and when a run counts as failed."""

from collections import Counter
from urllib.error import URLError

import pytest
from app.nowcast import kma_worker, open_meteo_worker
from app.nowcast.kma_adapter import KmaGk2aError
from app.nowcast.worker_report import exit_code, report_line
from test_nowcast_open_meteo_worker import NOW, Response, add_scene, make_engine, payload


def test_report_line_shows_counts_and_then_the_reasons():
    result = {"sites": 3, "stored": 1, "failed": 2, "lease_acquired": 1}
    assert report_line(result, Counter()) == "sites=3 stored=1 failed=2 lease_acquired=1"
    assert report_line(result, Counter({"b_reason": 1, "a_reason": 2})) == (
        "sites=3 stored=1 failed=2 lease_acquired=1 reasons=a_reason:2,b_reason:1"
    )


@pytest.mark.parametrize(
    ("stored", "failed", "expected"),
    [
        (0, 3, 1),  # tried and stored nothing: the run failed
        (2, 1, 0),  # some sites failed (for example out of provider coverage): reported, not failed
        (3, 0, 0),
        (0, 0, 0),  # nothing to do, or the lease is held by another run
    ],
)
def test_a_run_fails_only_when_it_stored_nothing(stored, failed, expected):
    result = {"sites": stored + failed, "stored": stored, "failed": failed, "lease_acquired": 1}
    assert exit_code(result) == expected


def test_open_meteo_network_failure_is_retried_once(monkeypatch, tmp_path):
    engine = make_engine(tmp_path)
    add_scene(engine)
    calls = []

    def open_url(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise URLError("blip")
        return Response(payload(dnis=(0, 200)))

    monkeypatch.setattr("app.nowcast.adapters.urlopen", open_url)
    failures: Counter[str] = Counter()
    result = open_meteo_worker.run_once(
        engine, now=NOW, owner_id="a", failures=failures, retry_delay_seconds=0
    )
    assert (result["stored"], result["failed"], len(calls)) == (1, 0, 2)
    assert not failures


def test_open_meteo_failure_after_the_retry_names_its_reason(monkeypatch, tmp_path):
    engine = make_engine(tmp_path)
    add_scene(engine)
    calls = []

    def offline(*_args, **_kwargs):
        calls.append(1)
        raise URLError("offline")

    monkeypatch.setattr("app.nowcast.adapters.urlopen", offline)
    failures: Counter[str] = Counter()
    result = open_meteo_worker.run_once(
        engine, now=NOW, owner_id="a", failures=failures, retry_delay_seconds=0
    )
    assert (result["stored"], result["failed"], len(calls)) == (0, 1, 2)
    assert failures == {"open_meteo_transport_error": 1}


def test_open_meteo_bad_response_is_not_retried(monkeypatch, tmp_path):
    engine = make_engine(tmp_path)
    add_scene(engine)
    calls = []

    def bad(*_args, **_kwargs):
        calls.append(1)
        return Response({"hourly": {}})

    monkeypatch.setattr("app.nowcast.adapters.urlopen", bad)
    failures: Counter[str] = Counter()
    open_meteo_worker.run_once(
        engine, now=NOW, owner_id="a", failures=failures, retry_delay_seconds=0
    )
    assert len(calls) == 1
    assert failures == {"open_meteo_invalid_response": 1}


def test_open_meteo_worker_prints_the_reasons_and_fails_when_nothing_was_stored(
    monkeypatch, tmp_path, capsys
):
    engine = make_engine(tmp_path)
    add_scene(engine)
    monkeypatch.setattr(
        "app.nowcast.adapters.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    monkeypatch.setattr(open_meteo_worker, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(open_meteo_worker, "create_engine", lambda *_a, **_k: engine)
    monkeypatch.setattr("sys.argv", ["open_meteo_worker"])
    assert open_meteo_worker.main() == 1
    assert "sites=1 stored=0 failed=1 lease_acquired=1 reasons=open_meteo_transport_error:1" in (
        capsys.readouterr().out
    )


def test_kma_worker_names_the_reason_of_each_failed_site(tmp_path):
    engine = make_engine(tmp_path)
    add_scene(engine, "korea", latitude=37.5, longitude=127.0)
    add_scene(engine, "abroad", latitude=12.0, longitude=120.0)

    class Adapter:
        def __init__(self, site):
            self.site = site

        def fetch_recent(self, _now):
            raise KmaGk2aError("kma_transport_error")

    failures: Counter[str] = Counter()
    result = kma_worker.run_once(
        engine,
        api_key="secret-key",
        output_root=tmp_path / "out",
        now=NOW,
        owner_id="w",
        adapter_factory=lambda site, _key: Adapter(site),
        failures=failures,
    )
    assert (result["sites"], result["stored"], result["failed"]) == (2, 0, 2)
    assert failures == {"kma_transport_error": 2}
    assert "secret-key" not in report_line(result, failures)
