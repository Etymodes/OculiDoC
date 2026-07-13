"""SQLite engine creation."""

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool


def create_sqlite_engine(
    database_path: str | Path,
    *,
    echo: bool = False,
) -> Engine:
    """Create a SQLite engine for a file or an in-memory database."""
    if str(database_path) == ":memory:":
        return create_engine(
            "sqlite+pysqlite:///:memory:",
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    resolved_path = Path(database_path).expanduser().resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    return create_engine(
        f"sqlite+pysqlite:///{resolved_path.as_posix()}",
        echo=echo,
        connect_args={"check_same_thread": False},
    )
