from __future__ import annotations

import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        s = get_settings()
        url = s.database_url
        kwargs = {}
        if url.startswith("sqlite"):
            path = url.replace("sqlite:///", "")
            if path and path != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            kwargs["pool_pre_ping"] = True
            kwargs["pool_size"] = 5
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _pragmas(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker:
    get_engine()
    return _SessionLocal


def migrate_additive() -> None:
    """Add columns that exist in the models but not in the database (no Alembic; additive changes only)."""
    from sqlalchemy import inspect, text
    engine = get_engine()
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ctype = col.type.compile(engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "arg", None) is not None and not callable(col.default.arg):
                    v = col.default.arg
                    default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v) if isinstance(v, str) else v}"
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ctype}{default}'))


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)
    Base.metadata.create_all(get_engine())
    migrate_additive()
    from .harness.defaults import ensure_default_harness
    from .settings_store import ensure_defaults
    with db_session() as db:
        ensure_default_harness(db)
        ensure_defaults(db)


@contextmanager
def db_session() -> Session:
    db = session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    """FastAPI dependency."""
    db = session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reset_engine_for_tests():
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
