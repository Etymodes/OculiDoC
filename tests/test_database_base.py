"""Database declarative base tests."""

from sqlalchemy.orm import DeclarativeBase

from oculidoc.infrastructure.database import Base


def test_database_base_is_declarative_base() -> None:
    assert issubclass(Base, DeclarativeBase)


def test_database_base_has_metadata() -> None:
    assert Base.metadata is not None
