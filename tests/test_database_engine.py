"""SQLite engine tests."""

from pathlib import Path

from sqlalchemy import text

from oculidoc.infrastructure.database import create_sqlite_engine


def test_in_memory_sqlite_engine_executes_queries() -> None:
    engine = create_sqlite_engine(":memory:")

    with engine.connect() as connection:
        result = connection.scalar(text("SELECT 1"))

    assert result == 1

    engine.dispose()


def test_file_sqlite_engine_creates_database(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "nested" / "oculidoc-test.sqlite3"
    engine = create_sqlite_engine(database_path)

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE test_record (id INTEGER PRIMARY KEY, value TEXT)"))

    engine.dispose()

    assert database_path.exists()
