from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

from app.settings import settings


def _make_engine() -> Engine:
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        # timeout: seconds SQLite waits on a locked DB before raising "database
        # is locked" -- the app has several concurrent writers (ingestion,
        # classification, aggregation, the scheduler loop), so a bare default
        # (5s) can trip under contention even with WAL mode below.
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        # WAL: readers don't block writers and vice versa (vs. default DELETE
        # mode, where any writer blocks all readers for the transaction).
        cursor.execute("PRAGMA journal_mode=WAL")
        # Belt-and-suspenders alongside connect_args.timeout above: have
        # SQLite itself retry internally for up to 30s before raising.
        cursor.execute("PRAGMA busy_timeout=30000")
        # NORMAL is safe (not corruption-risking) in WAL mode and avoids an
        # fsync on every commit -- FULL's extra durability guard is redundant
        # once WAL's own checkpoint fsyncs are in play.
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return eng


engine: Engine = _make_engine()


def create_db_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
