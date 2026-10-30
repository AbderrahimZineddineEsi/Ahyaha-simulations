"""The migrations are the schema: they must match the models, so a model change without a migration fails here."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from backend import db
from backend.models import Base


async def test_migrations_match_the_models(env):
    async with db.engine().connect() as conn:
        diff = await conn.run_sync(lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata))
    assert diff == []
