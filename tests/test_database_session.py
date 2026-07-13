"""SQLAlchemy session factory tests."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from oculidoc.infrastructure.database import (
    create_session_factory,
    create_sqlite_engine,
)


def test_session_factory_creates_working_session() -> None:
    engine = create_sqlite_engine(":memory:")
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        result = session.scalar(text("SELECT 1"))

    assert result == 1

    engine.dispose()


def test_session_factory_returns_sqlalchemy_session() -> None:
    engine = create_sqlite_engine(":memory:")
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        assert isinstance(session, Session)
        assert session.bind is engine

    engine.dispose()


def test_session_does_not_expire_objects_on_commit() -> None:
    engine = create_sqlite_engine(":memory:")
    session_factory = create_session_factory(engine)

    assert session_factory.kw["expire_on_commit"] is False
    assert session_factory.kw["autoflush"] is False

    engine.dispose()
