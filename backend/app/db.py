from collections.abc import Iterator

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

DATABASE_URL = "sqlite:///./sunsync.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def init_db(database_engine: Engine | None = None) -> None:
    from app import models  # noqa: F401  (register tables)
    from app.migrations import upgrade_database

    target = database_engine if database_engine is not None else engine
    SQLModel.metadata.create_all(target)
    upgrade_database(target)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
