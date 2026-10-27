"""Stage 1 weather-source selection and Open-Meteo adapter contract tests."""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

import pytest
from app.schemas.scene import Scene
from app.schemas.sunpath import IrradianceResponse
from app.services import sunpath

from engine.fr01.models import Site

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "fr06_scene_example.json"
SITE = Site(37.5, 127.0, "Asia/Seoul")
RECEIVED_AT = datetime(2026, 10, 14, 3, 5, tzinfo=UTC)


@pytest.fixture
def scene_data():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def instant_weather(data, values=(600, 400), fetched_at="2026-10-14T03:00:00Z"):
    weather = data["weather"]
    weather.update(
        {
            "temporal_meaning": "instant",
            "variable": "direct_normal_irradiance_instant",
            "interpolation": "linear_estimate",
            "fetched_at": fetched_at,
            "max_age_s": 3600,
            "records": [
                {"valid_time": "2026-10-14T03:00:00Z", "dni_W_m2": values[0]},
                {"valid_time": "2026-10-14T04:00:00Z", "dni_W_m2": values[1]},
            ],
        }
    )


def scene_from(data) -> Scene:
    return Scene.model_validate(copy.deepcopy(data))


def remove_weather(data):
    data.pop("weather")
    data.pop("scenario")


def test_scene_weather_has_priority_and_interpolates_instants(scene_data, monkeypatch):
    instant_weather(scene_data)
    calls = 0

    def unexpected_open_meteo(*_args):
        nonlocal calls
        calls += 1
        return {}

    monkeypatch.setattr(sunpath, "_open_meteo_snapshot", unexpected_open_meteo)
    values = sunpath._select_dni(
        scene_from(scene_data),
        SITE,
        (datetime(2026, 10, 14, 3, tzinfo=UTC), datetime(2026, 10, 14, 3, 30, tzinfo=UTC)),
        RECEIVED_AT,
    )
    assert calls == 0
    assert [value.source for value in values] == ["scene_weather", "scene_weather"]
    assert [value.value for value in values] == [600, 500]


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        (lambda data: instant_weather(data, values=(None, 400)), "scene_weather_invalid"),
        (
            lambda data: instant_weather(data, fetched_at="2026-10-14T01:00:00Z"),
            "scene_weather_stale",
        ),
        (lambda data: instant_weather(data, values=(600, None)), "scene_weather_invalid"),
    ],
)
def test_invalid_or_stale_scene_weather_falls_through(
    scene_data, monkeypatch, change, expected_reason
):
    change(scene_data)
    calls = 0

    def open_meteo(*_args):
        nonlocal calls
        calls += 1
        return {
            datetime(2026, 10, 14, 4, tzinfo=UTC): (
                321.0,
                datetime(2026, 10, 14, 4, tzinfo=UTC),
                RECEIVED_AT,
            )
        }

    monkeypatch.setattr(sunpath, "_open_meteo_snapshot", open_meteo)
    value = sunpath._select_dni(
        scene_from(scene_data), SITE, (datetime(2026, 10, 14, 3, 30, tzinfo=UTC),), RECEIVED_AT
    )[0]
    assert calls == 1
    assert (value.value, value.source, value.fallback_reason) == (
        321.0,
        "open_meteo",
        expected_reason,
    )


def test_hourly_weather_uses_half_open_interval_and_zero_is_valid(scene_data, monkeypatch):
    weather = scene_data["weather"]
    weather.update(
        {
            "temporal_meaning": "preceding_hour_mean",
            "variable": "direct_normal_irradiance",
            "interpolation": "none",
            "fetched_at": "2026-10-14T03:00:00Z",
            "max_age_s": 3600,
            "records": [
                {
                    "valid_time": "2026-10-14T03:00:00Z",
                    "interval_start": "2026-10-14T02:00:00Z",
                    "interval_end": "2026-10-14T03:00:00Z",
                    "dni_W_m2": 0,
                },
                {
                    "valid_time": "2026-10-14T04:00:00Z",
                    "interval_start": "2026-10-14T03:00:00Z",
                    "interval_end": "2026-10-14T04:00:00Z",
                    "dni_W_m2": 400,
                },
            ],
        }
    )
    monkeypatch.setattr(sunpath, "_open_meteo_snapshot", lambda *_args: {})
    monkeypatch.setattr(sunpath, "_clear_sky_dni", lambda *_args: 700.0)
    values = sunpath._select_dni(
        scene_from(scene_data),
        SITE,
        (datetime(2026, 10, 14, 2, 59, tzinfo=UTC), datetime(2026, 10, 14, 3, tzinfo=UTC)),
        RECEIVED_AT,
    )
    assert (values[0].value, values[0].source) == (0.0, "scene_weather")
    assert (values[1].value, values[1].source, values[1].fallback_reason) == (
        400.0,
        "scene_weather",
        None,
    )


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (sunpath.OpenMeteoError("open_meteo_timeout"), "open_meteo_timeout"),
        (sunpath.OpenMeteoError("open_meteo_transport_error"), "open_meteo_transport_error"),
        (sunpath.OpenMeteoError("open_meteo_invalid_response"), "open_meteo_invalid_response"),
    ],
)
def test_open_meteo_failures_fall_back_to_clear_sky(scene_data, monkeypatch, error, reason):
    remove_weather(scene_data)
    monkeypatch.setattr(
        sunpath, "_open_meteo_snapshot", lambda *_args: (_ for _ in ()).throw(error)
    )
    monkeypatch.setattr(sunpath, "_clear_sky_dni", lambda *_args: 111.0)
    value = sunpath._select_dni(
        scene_from(scene_data), SITE, (datetime(2026, 10, 14, 3, tzinfo=UTC),), RECEIVED_AT
    )[0]
    assert (value.value, value.source, value.fallback_reason) == (111.0, "pvlib_clear_sky", reason)


def test_null_open_meteo_dni_falls_through_to_clear_sky(scene_data, monkeypatch):
    remove_weather(scene_data)
    endpoint = datetime(2026, 10, 14, 4, tzinfo=UTC)
    monkeypatch.setattr(
        sunpath, "_open_meteo_snapshot", lambda *_args: {endpoint: (None, endpoint, RECEIVED_AT)}
    )
    monkeypatch.setattr(sunpath, "_clear_sky_dni", lambda *_args: 222.0)
    value = sunpath._select_dni(
        scene_from(scene_data), SITE, (datetime(2026, 10, 14, 3, 30, tzinfo=UTC),), RECEIVED_AT
    )[0]
    assert (value.value, value.source, value.fallback_reason) == (
        222.0,
        "pvlib_clear_sky",
        "open_meteo_invalid_response",
    )


def test_open_meteo_snapshot_is_loaded_once_for_all_unresolved_timestamps(scene_data, monkeypatch):
    remove_weather(scene_data)
    calls = 0

    def open_meteo(*_args):
        nonlocal calls
        calls += 1
        return {
            datetime(2026, 10, 14, 4, tzinfo=UTC): (
                100.0,
                datetime(2026, 10, 14, 4, tzinfo=UTC),
                RECEIVED_AT,
            ),
            datetime(2026, 10, 14, 5, tzinfo=UTC): (
                200.0,
                datetime(2026, 10, 14, 5, tzinfo=UTC),
                RECEIVED_AT,
            ),
        }

    monkeypatch.setattr(sunpath, "_open_meteo_snapshot", open_meteo)
    values = sunpath._select_dni(
        scene_from(scene_data),
        SITE,
        (datetime(2026, 10, 14, 3, 30, tzinfo=UTC), datetime(2026, 10, 14, 4, 30, tzinfo=UTC)),
        RECEIVED_AT,
    )
    assert calls == 1
    assert [value.value for value in values] == [100.0, 200.0]


class FakeResponse:
    status = 200

    def __init__(self, body: object):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def forecast_payload(times=(1_760_000_000, 1_760_003_600), dnis=(0, 123)):
    return {
        "hourly_units": {"direct_normal_irradiance": "W/m²"},
        "hourly": {
            "time": list(times),
            "direct_normal_irradiance": list(dnis),
            "cloud_cover": [10, 20],
        },
    }


def test_open_meteo_adapter_validates_units_and_monotonic_timestamps(monkeypatch):
    monkeypatch.setattr(
        sunpath, "urlopen", lambda *_args, **_kwargs: FakeResponse(forecast_payload())
    )
    snapshot = sunpath._open_meteo_snapshot(SITE, RECEIVED_AT)
    assert [value[0] for value in snapshot.values()] == [0.0, 123.0]

    for payload in (
        forecast_payload(times=(1_760_003_600, 1_760_000_000)),
        forecast_payload(times=(1_760_000_000, 1_760_000_000)),
        {**forecast_payload(), "hourly_units": {"direct_normal_irradiance": "W/m2"}},
    ):
        monkeypatch.setattr(
            sunpath, "urlopen", lambda *_args, payload=payload, **_kwargs: FakeResponse(payload)
        )
        with pytest.raises(sunpath.OpenMeteoError, match="open_meteo_invalid_response"):
            sunpath._open_meteo_snapshot(SITE, RECEIVED_AT)


def test_open_meteo_transport_error_is_mapped(monkeypatch):
    monkeypatch.setattr(
        sunpath, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline"))
    )
    with pytest.raises(sunpath.OpenMeteoError, match="open_meteo_transport_error"):
        sunpath._open_meteo_snapshot(SITE, RECEIVED_AT)


def test_irradiance_dto_rejects_negative_values_and_unknown_fallback_reason():
    value = {
        "timestamp": datetime(2026, 10, 14, 3, tzinfo=UTC),
        "baseline_dni_W_m2": 0,
        "final_dni_W_m2": 0,
        "source": "scene_weather",
        "fallback_used": False,
        "fallback_reason": None,
        "valid_time": datetime(2026, 10, 14, 3, tzinfo=UTC),
        "fetched_at": datetime(2026, 10, 14, 3, tzinfo=UTC),
    }
    assert IrradianceResponse.model_validate(value).baseline_dni_W_m2 == 0
    for field, invalid in (
        ("baseline_dni_W_m2", -1),
        ("final_dni_W_m2", -1),
        ("fallback_reason", "unexpected"),
    ):
        changed = {**value, field: invalid}
        with pytest.raises(ValueError):
            IrradianceResponse.model_validate(changed)
