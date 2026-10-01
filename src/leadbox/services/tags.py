from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbox.models import Lead, Tag, lead_tags
from leadbox.services.common import insert_ignoring_duplicate

TAG_MAX_LEN = 64


def canonical_tag(raw: str) -> str:
    return " ".join(raw.split()).lower()


def normalize_tag(raw: str) -> str:
    name = canonical_tag(raw)
    if not name:
        raise ValueError("Тег не может быть пустым")
    if len(name) > TAG_MAX_LEN:
        raise ValueError(f"Тег длиннее {TAG_MAX_LEN} символов")
    return name


async def add_tag(session: AsyncSession, lead: Lead, raw: str) -> Tag:
    tag = await get_or_create_tag(session, normalize_tag(raw))
    await insert_ignoring_duplicate(session, lead_tags, lead_id=lead.id, tag_id=tag.id)
    await session.refresh(lead, ["tags"])
    return tag


async def remove_tag(session: AsyncSession, lead: Lead, name: str) -> None:
    # No validation: a name that could never be a tag simply matches nothing.
    tag_ids = select(Tag.id).where(Tag.name == canonical_tag(name)).scalar_subquery()
    await session.execute(delete(lead_tags).where(lead_tags.c.lead_id == lead.id, lead_tags.c.tag_id == tag_ids))
    await session.refresh(lead, ["tags"])


async def list_tags_with_counts(session: AsyncSession) -> list[tuple[str, int]]:
    # Inner join: a tag removed from every lead is not worth a row on the tags page.
    count = func.count(lead_tags.c.lead_id)
    stmt = (
        select(Tag.name, count)
        .join(lead_tags, lead_tags.c.tag_id == Tag.id)
        .group_by(Tag.id, Tag.name)
        .order_by(count.desc(), Tag.name)
    )
    return [(name, n) for name, n in (await session.execute(stmt)).all()]


async def get_or_create_tag(session: AsyncSession, name: str) -> Tag:
    """`name` must already be normalized."""
    tag = await session.scalar(select(Tag).where(Tag.name == name))
    if tag is None:
        await insert_ignoring_duplicate(session, Tag.__table__, name=name)
        tag = await session.scalar(select(Tag).where(Tag.name == name))
    return tag
