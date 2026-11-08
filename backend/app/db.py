import os
import sqlite3
from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

DEFAULT_DATABASE_URL = "sqlite:///./sunsync.db"


def database_url() -> str:
    """Return the database URL; a deployed server sets an absolute path in the environment."""

    return os.environ.get("SUNSYNC_DATABASE_URL") or DEFAULT_DATABASE_URL


DATABASE_URL = database_url()
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30.0},
)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
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


def init_db(database_engine: Engine | None = None) -> None:
    from app import models  # noqa: F401  (register tables)
    from app.migrations import upgrade_database

    target = database_engine if database_engine is not None else engine
    SQLModel.metadata.create_all(target)
    upgrade_database(target)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
