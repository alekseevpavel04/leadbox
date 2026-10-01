from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from leadbox.config import async_database_url


def create_engine(database_url: str, **kwargs: Any) -> AsyncEngine:
    url, connect_args = async_database_url(database_url)
    if url.get_backend_name() == "sqlite":
        engine = create_async_engine(url, **kwargs)
        event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
        return engine
    # Neon suspends idle compute and drops its connections, so a pooled connection may be dead.
    return create_async_engine(url, connect_args=connect_args, pool_pre_ping=True, **kwargs)


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: handlers read the lead after commit (notification, redirect), and an
    # expired attribute would need a lazy load, which async sessions cannot do implicitly.
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session
