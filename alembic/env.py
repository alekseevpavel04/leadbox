import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection

from leadbox.config import async_database_url, get_settings
from leadbox.db import create_engine
from leadbox.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# An explicit sqlalchemy.url (set by tests) wins over DATABASE_URL.
database_url = config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _configure(**kwargs) -> None:
    # Batch mode lets later ALTERs run on SQLite, which cannot alter most column properties in place.
    context.configure(target_metadata=Base.metadata, render_as_batch=True, **kwargs)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    url, _ = async_database_url(database_url)
    _configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)


async def run_async_migrations() -> None:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
