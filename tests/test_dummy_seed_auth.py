"""The dummy seed tool logs in with the session cookie of the Google login."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "dummy_data" / "seed_dummy_spaces.py"
spec = importlib.util.spec_from_file_location("seed_dummy_spaces", SCRIPT)
assert spec is not None and spec.loader is not None
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("SUNSYNC_COOKIE", "SUNSYNC_USER", "SUNSYNC_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_no_settings_send_no_credentials():
    assert seed.auth_headers() == {}


def test_the_session_cookie_is_sent_as_it_is(monkeypatch):
    monkeypatch.setenv("SUNSYNC_COOKIE", "__Host-sunsync_session=abc")
    assert seed.auth_headers() == {"Cookie": "__Host-sunsync_session=abc"}


def test_basic_auth_needs_both_values(monkeypatch):
    monkeypatch.setenv("SUNSYNC_USER", "u")
    assert seed.auth_headers() == {}
    monkeypatch.setenv("SUNSYNC_PASSWORD", "p")
    assert seed.auth_headers()["Authorization"].startswith("Basic ")
