import pytest
from sqlalchemy import func, select

from leadbox.models import Source, Tag, lead_tags
from leadbox.services.leads import create_lead, get_lead, list_leads
from leadbox.services.tags import add_tag, list_tags_with_counts, normalize_tag, remove_tag


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Горячий ", "горячий"),
        ("ГОРЯЧИЙ", "горячий"),
        ("  повторный   клиент\t", "повторный клиент"),
        ("кампания:Spring_2026", "кампания:spring_2026"),
        ("x" * 64, "x" * 64),
    ],
)
def test_normalize_tag(raw, expected):
    assert normalize_tag(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_empty_tag_is_rejected(raw):
    with pytest.raises(ValueError, match="пуст"):
        normalize_tag(raw)


def test_long_tag_is_rejected():
    with pytest.raises(ValueError, match="64"):
        normalize_tag("x" * 65)


def test_length_is_checked_after_collapsing_spaces():
    assert normalize_tag("a" + " " * 80 + "b") == "a b"


async def test_add_tag_is_idempotent_across_spellings(session):
    lead = await create_lead(session, source=Source.MANUAL, name="Анна")
    for raw in ["горячий", "Горячий ", "ГОРЯЧИЙ"]:
        tag = await add_tag(session, lead, raw)
        assert tag.name == "горячий"

    assert [t.name for t in lead.tags] == ["вручную", "горячий"]
    assert await session.scalar(select(func.count()).select_from(Tag).where(Tag.name == "горячий")) == 1
    assert await session.scalar(select(func.count()).select_from(lead_tags)) == 2


async def test_tag_row_is_shared_between_leads(session):
    first = await create_lead(session, source=Source.MANUAL, name="Анна")
    second = await create_lead(session, source=Source.MANUAL, name="Борис")
    tag_a = await add_tag(session, first, "vip")
    tag_b = await add_tag(session, second, "VIP")
    assert tag_a.id == tag_b.id


async def test_add_tag_rejects_invalid_name_and_changes_nothing(session):
    lead = await create_lead(session, source=Source.MANUAL, name="Анна")
    with pytest.raises(ValueError):
        await add_tag(session, lead, "   ")
    assert [t.name for t in lead.tags] == ["вручную"]


async def test_remove_tag_is_idempotent(session):
    lead = await create_lead(session, source=Source.MANUAL, name="Анна")
    await add_tag(session, lead, "горячий")

    await remove_tag(session, lead, "Горячий")
    await remove_tag(session, lead, "горячий")
    await remove_tag(session, lead, "никогда не было")
    await remove_tag(session, lead, "")

    assert [t.name for t in lead.tags] == ["вручную"]
    assert await list_leads(session, tag="горячий") == []


async def test_removed_tag_is_gone_in_a_new_session(sessionmaker):
    async with sessionmaker() as session:
        lead = await create_lead(session, source=Source.MANUAL, name="Анна")
        await add_tag(session, lead, "горячий")
        await session.commit()
        lead_id = lead.id

    async with sessionmaker() as session:
        lead = await get_lead(session, lead_id)
        await remove_tag(session, lead, "горячий")
        await session.commit()

    async with sessionmaker() as session:
        lead = await get_lead(session, lead_id)
        assert [t.name for t in lead.tags] == ["вручную"]


async def test_list_tags_with_counts(session):
    anna = await create_lead(session, source=Source.MANUAL, name="Анна")
    boris = await create_lead(session, source=Source.BOT, tg_user_id=1)
    vera = await create_lead(session, source=Source.BOT, tg_user_id=2)
    for lead in (anna, boris, vera):
        await add_tag(session, lead, "горячий")
    await add_tag(session, boris, "vip")
    await add_tag(session, anna, "забытый")
    await remove_tag(session, anna, "забытый")

    assert await list_tags_with_counts(session) == [
        ("горячий", 3),
        ("бот", 2),
        ("vip", 1),
        ("вручную", 1),
    ]
