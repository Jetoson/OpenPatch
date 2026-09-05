"""Engine and session setup.
"""

import os
from config import DATABASE_URL
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

IS_SQLITE = DATABASE_URL.startswith("sqlite")

# How long a writer waits for a competing write before giving up.
_SQLITE_BUSY_TIMEOUT_MS = 15_000

def _ensure_sqlite_directory(url: str) -> None:
    """Creates the directory where SQLite database lives in.
    """
    path = url.split("sqlite:///", 1)[-1]
    if not path or path.startswith(":"):
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


if IS_SQLITE:
    _ensure_sqlite_directory(DATABASE_URL)
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": _SQLITE_BUSY_TIMEOUT_MS / 1000},
        pool_pre_ping=True,
    )
else:
    # Postgres/MySQL.
    engine = create_engine(
        DATABASE_URL,
        pool_size=20,
        max_overflow=40,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        """configures sqlite per connection since PRAGMAs are connection-scoped."""
        cursor = dbapi_connection.cursor()
        # WAL mode: readers doesn't block on the writer
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        # Let SQLite keep indexes hot instead of re-reading them per query.
        cursor.execute("PRAGMA cache_size=-64000")  # 64 MB
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
