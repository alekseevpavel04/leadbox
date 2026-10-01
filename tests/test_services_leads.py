from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import StatementError

from leadbox.models import Channel, Direction, FormStep, Source, Status, utcnow
from leadbox.services.leads import (
    add_message,
    create_lead,
    get_lead,
    get_open_lead_by_tg,
    list_leads,
    mark_first_response,
    set_status,
    update_lead_fields,
)
from leadbox.services.tags import add_tag


@pytest.mark.parametrize(
    ("source", "tag"),
    [(Source.BOT, "бот"), (Source.TELEGRAM, "telegram"), (Source.MANUAL, "вручную")],
)
async def test_create_lead_attaches_source_tag(session, source, tag):
    lead = await create_lead(session, source=source, name="Анна")
    assert lead.id is not None
    assert lead.status == Status.NEW
    assert lead.first_response_at is None
    assert [t.name for t in lead.tags] == [tag]


async def test_created_lead_reads_back_in_a_new_session(sessionmaker):
    async with sessionmaker() as session:
        lead = await create_lead(
            session, source=Source.BOT, tg_user_id=42, tg_username="anna", campaign="spring", form_step=FormStep.NAME
        )
        await session.commit()

    async with sessionmaker() as session:
        loaded = await get_lead(session, lead.id)
    assert loaded.source == Source.BOT
    assert loaded.form_step == FormStep.NAME
    assert loaded.campaign == "spring"
    assert loaded.created_at.tzinfo is not None
    assert utcnow() - loaded.created_at < timedelta(minutes=1)


async def test_open_lead_lookup_ignores_closed_and_other_users(session):
    assert await get_open_lead_by_tg(session, 42) is None

    won = await create_lead(session, source=Source.BOT, tg_user_id=42)
    await set_status(session, won, Status.WON)
    lost = await create_lead(session, source=Source.TELEGRAM, tg_user_id=42)
    await set_status(session, lost, Status.LOST)
    await create_lead(session, source=Source.BOT, tg_user_id=7)
    assert await get_open_lead_by_tg(session, 42) is None

    current = await create_lead(session, source=Source.TELEGRAM, tg_user_id=42)
    await set_status(session, current, Status.IN_PROGRESS)
    found = await get_open_lead_by_tg(session, 42)
    assert found.id == current.id
    assert [t.name for t in found.tags] == ["telegram"]


async def test_open_lead_lookup_prefers_newest_if_several(session):
    older = await create_lead(session, source=Source.BOT, tg_user_id=42)
    newer = await create_lead(session, source=Source.TELEGRAM, tg_user_id=42)
    await update_lead_fields(session, older, name="старый")
    assert (await get_open_lead_by_tg(session, 42)).id == newer.id


async def test_leaving_new_sets_first_response_once(session):
    lead = await create_lead(session, source=Source.MANUAL, name="Анна")

    await set_status(session, lead, Status.IN_PROGRESS)
    first = lead.first_response_at
    assert first is not None

    await set_status(session, lead, Status.NEW)
    await set_status(session, lead, Status.WON)
    assert lead.first_response_at == first


async def test_staying_new_does_not_set_first_response(session):
    lead = await create_lead(session, source=Source.MANUAL, name="Анна")
    await set_status(session, lead, Status.NEW)
    assert lead.first_response_at is None


async def test_mark_first_response_keeps_the_first_value(session):
    lead = await create_lead(session, source=Source.TELEGRAM, tg_user_id=42)
    first = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    await mark_first_response(session, lead, first)
    await mark_first_response(session, lead, first + timedelta(hours=1))
    await set_status(session, lead, Status.IN_PROGRESS)
    assert lead.first_response_at == first


async def test_first_response_from_business_survives_status_change(sessionmaker):
    at = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    async with sessionmaker() as session:
        lead = await create_lead(session, source=Source.TELEGRAM, tg_user_id=42)
        await mark_first_response(session, lead, at)
        await session.commit()

    async with sessionmaker() as session:
        lead = await get_lead(session, lead.id)
        await set_status(session, lead, Status.IN_PROGRESS)
        await session.commit()
        assert lead.first_response_at == at


async def test_update_lead_fields(session):
    lead = await create_lead(session, source=Source.BOT, tg_user_id=42, form_step=FormStep.NAME)
    await update_lead_fields(session, lead, name="Анна", form_step=FormStep.CONTACT)
    assert (lead.name, lead.form_step) == ("Анна", FormStep.CONTACT)


@pytest.mark.parametrize("field", ["status", "first_response_at", "id", "nonexistent"])
async def test_update_lead_fields_refuses_guarded_fields(session, field):
    lead = await create_lead(session, source=Source.MANUAL, name="Анна")
    with pytest.raises(TypeError):
        await update_lead_fields(session, lead, **{field: None})


async def test_messages_come_back_in_telegram_order(sessionmaker):
    t0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    async with sessionmaker() as session:
        lead = await create_lead(session, source=Source.TELEGRAM, tg_user_id=42)
        await session.commit()

    # A fresh session with tags loaded but messages not, as the bot gets the lead from a lookup.
    async with sessionmaker() as session:
        lead = await get_open_lead_by_tg(session, 42)
        await add_message(
            session,
            lead,
            direction=Direction.OUT,
            channel=Channel.BUSINESS,
            text="второе",
            sent_at=t0 + timedelta(1),
            tg_message_id=2,
        )
        await add_message(
            session, lead, direction=Direction.IN, channel=Channel.BUSINESS, text="первое", sent_at=t0, tg_message_id=1
        )
        await session.commit()

    async with sessionmaker() as session:
        lead = await get_lead(session, lead.id)
    assert [m.text for m in lead.messages] == ["первое", "второе"]
    assert lead.messages[0].sent_at == t0
    assert lead.messages[1].direction == Direction.OUT


async def test_naive_datetime_is_rejected(session):
    lead = await create_lead(session, source=Source.TELEGRAM, tg_user_id=42)
    with pytest.raises(StatementError, match="naive datetime"):
        await add_message(
            session,
            lead,
            direction=Direction.IN,
            channel=Channel.BUSINESS,
            text="x",
            sent_at=datetime(2026, 10, 1),
            tg_message_id=None,
        )


async def test_get_lead_missing_returns_none(session):
    assert await get_lead(session, 999999) is None


async def test_list_leads_newest_first_with_filters(session):
    anna = await create_lead(session, source=Source.MANUAL, name="Анна")
    boris = await create_lead(session, source=Source.BOT, name="Борис", tg_user_id=1)
    vera = await create_lead(session, source=Source.TELEGRAM, name="Вера", tg_user_id=2)
    await add_tag(session, anna, "горячий")
    await add_tag(session, vera, "Горячий")
    await set_status(session, boris, Status.IN_PROGRESS)

    def names(leads):
        return [lead.name for lead in leads]

    assert names(await list_leads(session)) == ["Вера", "Борис", "Анна"]
    assert names(await list_leads(session, tag=" ГОРЯЧИЙ ")) == ["Вера", "Анна"]
    assert names(await list_leads(session, status=Status.NEW)) == ["Вера", "Анна"]
    assert names(await list_leads(session, source=Source.BOT)) == ["Борис"]
    assert names(await list_leads(session, tag="горячий", source=Source.MANUAL)) == ["Анна"]
    assert names(await list_leads(session, tag="горячий", status=Status.IN_PROGRESS)) == []
    assert names(await list_leads(session, tag="нет такого")) == []
    assert names(await list_leads(session, tag="")) == ["Вера", "Борис", "Анна"]


async def test_list_leads_has_tags_loaded_after_session_closes(sessionmaker):
    async with sessionmaker() as session:
        lead = await create_lead(session, source=Source.MANUAL, name="Анна")
        await add_tag(session, lead, "vip")
        await session.commit()

    async with sessionmaker() as session:
        leads = await list_leads(session)
    assert [t.name for t in leads[0].tags] == ["vip", "вручную"]
