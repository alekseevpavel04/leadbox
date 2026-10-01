from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from leadbox.models import BusinessConnection, Channel, Lead, Message, ProcessedUpdate
from leadbox.services.common import insert_ignoring_duplicate


async def mark_update_processed(session: AsyncSession, update_id: int) -> bool:
    """False if this update_id was already handled: Telegram resends updates it got no answer for.

    Must run in the same transaction as the update's changes, so a failed update is not marked.
    """
    return await insert_ignoring_duplicate(session, ProcessedUpdate.__table__, update_id=update_id)


async def upsert_business_connection(session: AsyncSession, *, id: str, owner_user_id: int, is_enabled: bool) -> None:
    connection = await session.get(BusinessConnection, id)
    if connection is None:
        session.add(BusinessConnection(id=id, owner_user_id=owner_user_id, is_enabled=is_enabled))
    else:
        connection.owner_user_id = owner_user_id
        connection.is_enabled = is_enabled
    await session.flush()


async def get_business_connection(session: AsyncSession, id: str) -> BusinessConnection | None:
    return await session.get(BusinessConnection, id)


async def update_message_text(
    session: AsyncSession, *, channel: Channel, tg_user_id: int, tg_message_id: int, text: str | None
) -> None:
    # Message ids are unique only within a chat: the bot chat and the business chat with the same
    # person count separately. So the edit is matched by channel and through the lead's user.
    lead_ids = select(Lead.id).where(Lead.tg_user_id == tg_user_id)
    await session.execute(
        update(Message)
        .where(Message.channel == channel, Message.tg_message_id == tg_message_id, Message.lead_id.in_(lead_ids))
        .values(text=text)
        .execution_options(synchronize_session=False)
    )
