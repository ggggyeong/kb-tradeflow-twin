from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import PROJECT_ROOT, get_settings
from app.db.models import Base


def _resolved_database_url(url: str) -> str:
    if not url.startswith("sqlite:///./"):
        return url
    relative = url.removeprefix("sqlite:///./")
    return f"sqlite:///{PROJECT_ROOT / relative}"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the configured SQLAlchemy engine."""
    url = _resolved_database_url(get_settings().database_url)
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True, connect_args={"check_same_thread": False})


@lru_cache(maxsize=1)
def session_factory() -> sessionmaker[Session]:
    """Return the transactional session factory."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False, class_=Session)


def init_database() -> None:
    """Create the v16 domain schema without document-specific typed tables."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    # `create_all` cannot add a column to an existing SQLite database.  Keep
    # the local demo DB forward-compatible with the additive Company profile
    # field; managed KB databases should apply the equivalent schema migration.
    if engine.dialect.name == "sqlite":
        company_columns = {column["name"] for column in inspect(engine).get_columns("company")}
        if "product_advisory_profile_json" not in company_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE company ADD COLUMN product_advisory_profile_json JSON")
                )
                connection.execute(
                    text(
                        "UPDATE company SET product_advisory_profile_json = '{}' "
                        "WHERE product_advisory_profile_json IS NULL"
                    )
                )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a transaction and commit only when the block succeeds."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Dispose the engine and clear factories for isolated tests."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    session_factory.cache_clear()
    get_engine.cache_clear()
