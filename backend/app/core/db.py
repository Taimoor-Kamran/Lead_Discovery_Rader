"""Database engine and session management.

Three rules hold this file together, all of them paid for by the v0.9.0 production
incident (see `specs/v0.9.0.md`, "Implementation notes"):

1. **The scheduler never borrows the application's engine.** It runs in a daemon thread
   inside the RQ worker, and RQ forks a child process for every job. A connection the
   scheduler was using is therefore copied into a child that then writes on the same
   socket. The scheduler gets its own engine, with `NullPool` so it holds nothing
   between ticks and a fork has nothing of its to inherit.
2. **A forked child drops every inherited connection.** `os.register_at_fork` disposes
   the pools in the child *without closing* them: the sockets still belong to the
   parent, so closing them would cut the parent's own connection.
3. **No server-side prepared statements.** psycopg names them `_pg3_N` per connection and
   counts them client-side; two processes on one inherited socket collide on the names
   ("prepared statement `_pg3_0` already exists"). Nothing here needs the plan cache
   enough to be worth that failure mode, so it is off — belt as well as braces.
"""

import os
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_scheduler_engine: Engine | None = None
_scheduler_session_factory: sessionmaker[Session] | None = None


def connect_args_for(url: str) -> dict[str, Any]:
    """Driver-level arguments every engine in this process is built with.

    `prepare_threshold=None` turns psycopg's automatic prepared statements off. It is
    rule 3 above: the names are per-connection and client-counted, so any connection two
    processes end up sharing produces `DuplicatePreparedStatement` /
    `InvalidSqlStatementName` instead of a query. Other drivers do not take the option.
    """
    if make_url(url).get_driver_name() == "psycopg":
        return {"prepare_threshold": None}
    return {}


def _build_engine(url: str, **engine_kwargs: Any) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    connect_args = connect_args_for(url)
    if connect_args:
        kwargs["connect_args"] = connect_args
    kwargs.update(engine_kwargs)
    return create_engine(url, **kwargs)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine(get_settings().sync_database_url)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


def get_scheduler_engine() -> Engine:
    """The scheduler thread's own engine. `NullPool`: a connection per tick, kept by nobody.

    Rule 1. Nothing this engine opens is ever handed to a job, and because the pool keeps
    no idle connection between ticks, a fork in the middle of the schedule inherits none.
    """
    global _scheduler_engine
    if _scheduler_engine is None:
        _scheduler_engine = _build_engine(
            get_settings().sync_database_url, poolclass=NullPool, pool_pre_ping=False
        )
    return _scheduler_engine


def get_scheduler_session_factory() -> sessionmaker[Session]:
    global _scheduler_session_factory
    if _scheduler_session_factory is None:
        _scheduler_session_factory = sessionmaker(
            bind=get_scheduler_engine(), expire_on_commit=False, future=True
        )
    return _scheduler_session_factory


def reset_engine(**engine_kwargs: Any) -> Engine:
    """Rebuild both engines from current settings. Used by tests and by the worker."""
    global _engine, _session_factory, _scheduler_engine, _scheduler_session_factory
    if _engine is not None:
        _engine.dispose()
    if _scheduler_engine is not None:
        _scheduler_engine.dispose()
    _scheduler_engine = None
    _scheduler_session_factory = None
    url = get_settings().sync_database_url
    _engine = _build_engine(url, **engine_kwargs)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def dispose_inherited_connections() -> None:
    """Drop every pooled connection this process inherited from a fork.

    `close=False` is the whole point: the sockets are the parent's, still in use by the
    parent, and closing them here would break it. The child simply forgets them and dials
    its own. Registered with `os.register_at_fork` below, so RQ's work horse is covered
    without RQ having to know about it.
    """
    for engine in (_engine, _scheduler_engine):
        if engine is not None:
            engine.dispose(close=False)


os.register_at_fork(after_in_child=dispose_inherited_connections)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for code outside the request cycle (workers, scripts)."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def scheduler_session_scope() -> Iterator[Session]:
    """The same, on the scheduler's own engine. Only the scheduler thread may use it."""
    session = get_scheduler_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
