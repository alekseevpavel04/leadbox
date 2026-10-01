from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_ignoring_duplicate(session: AsyncSession, table: Table, **values: object) -> bool:
    """INSERT ... ON CONFLICT DO NOTHING; returns whether a row was inserted.

    Two webhook updates or a double-clicked form can insert the same key at once. Plain
    select-then-insert would fail the second one on the unique key; this lets it pass quietly.
    """
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    result = await session.execute(insert(table).values(**values).on_conflict_do_nothing())
    return result.rowcount == 1
