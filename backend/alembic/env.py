"""Alembic: run from the app with a connection already open, or from the command line (`alembic -c backend/alembic.ini upgrade head`)."""
import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from backend.config import settings
from backend.models import Base

config = context.config
target = Base.metadata


def run(connection):
    context.configure(connection=connection, target_metadata=target, render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async():
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        await connection.run_sync(run)
    await engine.dispose()


connection = config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    asyncio.run(run_async())
