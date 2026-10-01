from leadbox.models import Channel, Direction, Source, utcnow
from leadbox.services.leads import add_message, create_lead, get_lead
from leadbox.services.telegram import (
    get_business_connection,
    mark_update_processed,
    update_message_text,
    upsert_business_connection,
)


async def test_update_is_processed_once(session):
    assert await mark_update_processed(session, 1001) is True
    assert await mark_update_processed(session, 1001) is False
    assert await mark_update_processed(session, 1002) is True


async def test_rolled_back_update_can_be_processed_again(sessionmaker):
    async with sessionmaker() as session:
        assert await mark_update_processed(session, 7)
        await session.rollback()
    async with sessionmaker() as session:
        assert await mark_update_processed(session, 7)


async def test_business_connection_upsert(session):
    assert await get_business_connection(session, "bc1") is None
    await upsert_business_connection(session, id="bc1", owner_user_id=42, is_enabled=True)
    await upsert_business_connection(session, id="bc1", owner_user_id=42, is_enabled=False)
    connection = await get_business_connection(session, "bc1")
    assert connection.owner_user_id == 42
    assert connection.is_enabled is False


async def test_edit_touches_only_the_same_chat(session):
    anna = await create_lead(session, source=Source.TELEGRAM, tg_user_id=1)
    boris = await create_lead(session, source=Source.TELEGRAM, tg_user_id=2)
    for lead in (anna, boris):
        await add_message(
            session,
            lead,
            direction=Direction.IN,
            channel=Channel.BUSINESS,
            text="old",
            sent_at=utcnow(),
            tg_message_id=5,
        )
    anna_id, boris_id = anna.id, boris.id
    await update_message_text(session, channel=Channel.BUSINESS, tg_user_id=1, tg_message_id=5, text="new")
    session.expire_all()
    assert [m.text for m in (await get_lead(session, anna_id)).messages] == ["new"]
    assert [m.text for m in (await get_lead(session, boris_id)).messages] == ["old"]


async def test_edit_in_business_chat_leaves_bot_chat_message_alone(session):
    lead = await create_lead(session, source=Source.BOT, tg_user_id=1)
    for channel in (Channel.BOT, Channel.BUSINESS):
        await add_message(
            session, lead, direction=Direction.IN, channel=channel, text="old", sent_at=utcnow(), tg_message_id=5
        )
    lead_id = lead.id
    await update_message_text(session, channel=Channel.BUSINESS, tg_user_id=1, tg_message_id=5, text="new")
    session.expire_all()
    texts = {m.channel: m.text for m in (await get_lead(session, lead_id)).messages}
    assert texts == {Channel.BOT: "old", Channel.BUSINESS: "new"}
