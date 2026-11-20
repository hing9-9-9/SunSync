"""The database location can be set from the environment and defaults to the local file."""

from app.db import DEFAULT_DATABASE_URL, database_url


def test_default_is_the_local_file(monkeypatch):
    monkeypatch.delenv("SUNSYNC_DATABASE_URL", raising=False)
    assert database_url() == DEFAULT_DATABASE_URL


def test_environment_sets_the_location(monkeypatch):
    monkeypatch.setenv("SUNSYNC_DATABASE_URL", "sqlite:////var/lib/sunsync/sunsync.db")
    assert database_url() == "sqlite:////var/lib/sunsync/sunsync.db"


def test_empty_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("SUNSYNC_DATABASE_URL", "")
    assert database_url() == DEFAULT_DATABASE_URL
