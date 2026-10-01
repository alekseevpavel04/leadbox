from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from leadbox.models import OPEN_STATUSES, Channel, Direction, FormStep, Lead, Message, Source, Status, Tag, utcnow
from leadbox.services.tags import canonical_tag, get_or_create_tag

SOURCE_TAGS = {Source.BOT: "бот", Source.TELEGRAM: "telegram", Source.MANUAL: "вручную"}

# status and first_response_at are left out on purpose: they change only through set_status and
# mark_first_response, otherwise the "waiting N min" badge stops being honest.
EDITABLE_FIELDS = frozenset({"name", "contact", "request", "campaign", "tg_username", "form_step"})


async def get_open_lead_by_tg(session: AsyncSession, tg_user_id: int) -> Lead | None:
    return await session.scalar(
        select(Lead)
        .where(Lead.tg_user_id == tg_user_id, Lead.status.in_(OPEN_STATUSES))
        .options(selectinload(Lead.tags))
        .order_by(Lead.created_at.desc(), Lead.id.desc())
        .limit(1)
    )


async def create_lead(
    session: AsyncSession,
    *,
    source: Source,
    name: str | None = None,
    contact: str | None = None,
    request: str | None = None,
    campaign: str | None = None,
    tg_user_id: int | None = None,
    tg_username: str | None = None,
    form_step: FormStep | None = None,
) -> Lead:
    lead = Lead(
        source=source,
        name=name,
        contact=contact,
        request=request,
        campaign=campaign,
        tg_user_id=tg_user_id,
        tg_username=tg_username,
        form_step=form_step,
        tags=[await get_or_create_tag(session, SOURCE_TAGS[source])],
        messages=[],
    )
    session.add(lead)
    await session.flush()
    return lead


async def update_lead_fields(session: AsyncSession, lead: Lead, **fields: object) -> Lead:
    unknown = fields.keys() - EDITABLE_FIELDS
    if unknown:
        raise TypeError(f"not editable through update_lead_fields: {sorted(unknown)}")
    for key, value in fields.items():
        setattr(lead, key, value)
    await session.flush()
    return lead


async def set_status(session: AsyncSession, lead: Lead, status: Status) -> Lead:
    if lead.status == Status.NEW and status != Status.NEW and lead.first_response_at is None:
        lead.first_response_at = utcnow()
    lead.status = status
    await session.flush()
    return lead


async def mark_first_response(session: AsyncSession, lead: Lead, at: datetime) -> None:
    if lead.first_response_at is None:
        lead.first_response_at = at
        await session.flush()


async def add_message(
    session: AsyncSession,
    lead: Lead,
    *,
    direction: Direction,
    channel: Channel,
    text: str | None,
    sent_at: datetime,
    tg_message_id: int | None,
) -> None:
    # Through the relationship, so lead.messages stays current if it is already loaded.
    session.add(
        Message(
            lead=lead,
            direction=direction,
            channel=channel,
            text=text,
            sent_at=sent_at,
            tg_message_id=tg_message_id,
        )
    )
    await session.flush()


async def list_leads(
    session: AsyncSession,
    *,
    tag: str | None = None,
    status: Status | None = None,
    source: Source | None = None,
) -> list[Lead]:
    stmt = select(Lead).options(selectinload(Lead.tags)).order_by(Lead.created_at.desc(), Lead.id.desc())
    if tag:
        stmt = stmt.where(Lead.tags.any(Tag.name == canonical_tag(tag)))
    if status is not None:
        stmt = stmt.where(Lead.status == status)
    if source is not None:
        stmt = stmt.where(Lead.source == source)
    return list(await session.scalars(stmt))


async def get_lead(session: AsyncSession, lead_id: int) -> Lead | None:
    return await session.scalar(
        select(Lead).where(Lead.id == lead_id).options(selectinload(Lead.tags), selectinload(Lead.messages))
    )
