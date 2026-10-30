"""
The database connection (SQLAlchemy async) and the migrations. SQLite today; a Postgres URL in DATABASE_URL should be
the only change needed.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import settings

HERE = Path(__file__).resolve().parent
log = logging.getLogger("solvex")
_engine = None
_maker = None


def engine():
    global _engine, _maker
    if _engine is None:
        _engine = create_async_engine(settings.database_url)
        _maker = async_sessionmaker(_engine, expire_on_commit=False)
        if _engine.dialect.name == "sqlite":
            @event.listens_for(_engine.sync_engine, "connect")
            def pragmas(conn, _):
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()
    return _engine


async def dispose():
    global _engine, _maker
    if _engine is not None:
        await _engine.dispose()
    _engine = _maker = None


def session() -> AsyncSession:
    engine()
    return _maker()


@asynccontextmanager
async def session_scope():
    """One unit of work: commits at the end, rolls back on an error."""
    async with session() as s:
        try:
            yield s
            await commit(s)
        except BaseException:
            await s.rollback()
            raise


def _upgrade(connection):
    cfg = Config(str(HERE / "alembic.ini"))
    cfg.set_main_option("script_location", str(HERE / "alembic"))
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


async def migrate():
    async with engine().begin() as conn:
        await conn.run_sync(_upgrade)


def after_commit(s, fn, *args):
    """Run `fn(*args)` (a coroutine function) once the session's changes are saved: clients are told only what the database holds."""
    s.info.setdefault("outbox", []).append((fn, args))


async def commit(s):
    await s.commit()
    outbox, s.info["outbox"] = s.info.get("outbox", []), []
    for fn, args in outbox:
        try:
            await fn(*args)
        except Exception:   # it is saved: a client that can't be told is no reason to fail, or to skip the others
            log.exception("telling clients failed")
