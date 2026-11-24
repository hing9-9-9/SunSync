"""SQLite WAL database configuration and initialization for sunsync-hub."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

DEFAULT_HUB_DATABASE_URL = "sqlite:///./sunsync-hub.db"


def hub_database_url() -> str:
    """Return the hub SQLite database URL from SUNSYNC_HUB_DATABASE_URL or default."""
    return os.environ.get("SUNSYNC_HUB_DATABASE_URL") or DEFAULT_HUB_DATABASE_URL


def _configure_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.close()
    except (sqlite3.Error, AttributeError):
        return


def create_hub_engine(url: str | None = None) -> Engine:
    """Create a SQLite SQLAlchemy engine configured for WAL mode and busy timeout."""
    target_url = url if url is not None else hub_database_url()
    connect_args: dict[str, Any] = {}
    kwargs: dict[str, Any] = {}

    if target_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30.0}
        if target_url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool

    eng = create_engine(target_url, connect_args=connect_args, **kwargs)
    event.listen(eng, "connect", _configure_sqlite_pragmas)
    return eng


engine = create_hub_engine()


def init_db(database_engine: Engine | None = None) -> Engine:
    """Register hub tables and create them on the target engine."""
    from sunsync_hub.models import HubCommandReceipt, HubWindowShadow

    target = database_engine if database_engine is not None else engine
    SQLModel.metadata.create_all(
        target,
        tables=[HubWindowShadow.__table__, HubCommandReceipt.__table__],
    )
    return target
