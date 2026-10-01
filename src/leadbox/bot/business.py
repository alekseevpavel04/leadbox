"""Item 2: chats of a work account the bot is connected to through Telegram Business.

The bot only listens here. It never writes into a business chat: the person is talking to the
manager, not to a bot, and every reply in these chats is the manager's own.
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatType, ContentType
from aiogram.types import BusinessConnection, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from leadbox.bot.notify import lead_notification
from leadbox.bot.transaction import Outbox
from leadbox.config import Settings
from leadbox.models import Channel, Direction, Lead, Source
from leadbox.services.leads import (
    SOURCE_TAGS,
    add_message,
    create_lead,
    get_open_lead_by_tg,
    mark_first_response,
    update_lead_fields,
)
from leadbox.services.tags import add_tag
from leadbox.services.telegram import get_business_connection, update_message_text, upsert_business_connection

logger = logging.getLogger(__name__)

CONTENT_LABELS = {
    ContentType.PHOTO: "[фото]",
    ContentType.LIVE_PHOTO: "[фото]",
    ContentType.VIDEO: "[видео]",
    ContentType.VIDEO_NOTE: "[кружок]",
    ContentType.VOICE: "[голосовое]",
    ContentType.AUDIO: "[аудио]",
    ContentType.ANIMATION: "[гиф]",
    ContentType.DOCUMENT: "[файл]",
    ContentType.STICKER: "[стикер]",
    ContentType.CONTACT: "[контакт]",
    ContentType.LOCATION: "[геопозиция]",
    ContentType.VENUE: "[геопозиция]",
    ContentType.POLL: "[опрос]",
}
OTHER_CONTENT = "[без текста]"


def build_router() -> Router:
    router = Router(name="business")
    router.business_connection.register(on_connection)
    # business_message is its own update type: the form's router.message handlers never see it.
    router.business_message.filter(F.chat.type == ChatType.PRIVATE)
    router.edited_business_message.filter(F.chat.type == ChatType.PRIVATE)
    router.business_message.register(on_message)
    router.edited_business_message.register(on_edit)
    return router


async def on_connection(connection: BusinessConnection, session: AsyncSession) -> None:
    await upsert_business_connection(
        session, id=connection.id, owner_user_id=connection.user.id, is_enabled=connection.is_enabled
    )
    logger.info("business connection %s %s", connection.id, "enabled" if connection.is_enabled else "disabled")


async def on_message(message: Message, session: AsyncSession, outbox: Outbox, settings: Settings) -> None:
    if await _is_from_owner(session, message):
        await _on_owner_message(session, message)
    elif not message.from_user.is_bot:
        await _on_client_message(session, outbox, settings, message)


async def on_edit(message: Message, session: AsyncSession) -> None:
    # The lead of a private chat is always the other person, whoever wrote the edited message.
    await update_message_text(
        session,
        channel=Channel.BUSINESS,
        tg_user_id=message.chat.id,
        tg_message_id=message.message_id,
        text=message_content(message),
    )


async def _is_from_owner(session: AsyncSession, message: Message) -> bool:
    connection = await get_business_connection(session, message.business_connection_id)
    if connection is not None:
        return message.from_user.id == connection.owner_user_id
    # The connection is unknown when its update came after this one or the table was cleared.
    # A private chat's id is the other person's id, so anyone else in it writes from the business
    # account. Taking such a message for a client's would turn the manager's reply into a lead.
    logger.warning("business connection %s is unknown, direction taken from the chat", message.business_connection_id)
    return message.from_user.id != message.chat.id


async def _on_owner_message(session: AsyncSession, message: Message) -> None:
    lead = await get_open_lead_by_tg(session, message.chat.id)
    if lead is None:
        # A chat the CRM has no open lead for (an old one, or a closed deal): the manager's business.
        return
    await _record(session, lead, message, Direction.OUT)
    # Greetings and away messages are sent by Telegram on the owner's behalf: nobody answered yet.
    if not message.is_from_offline:
        await mark_first_response(session, lead, message.date)


async def _on_client_message(session: AsyncSession, outbox: Outbox, settings: Settings, message: Message) -> None:
    user = message.from_user
    lead = await get_open_lead_by_tg(session, user.id)
    if lead is None:
        lead = await create_lead(
            session,
            source=Source.TELEGRAM,
            name=_profile_name(user),
            contact=f"@{user.username}" if user.username else None,
            request=message_content(message),
            tg_user_id=user.id,
            tg_username=user.username,
        )
        outbox.notifications.append(lead_notification(lead, settings.public_base_url))
    else:
        # The same person may have filled the bot's form before: one lead, both sources as tags.
        if SOURCE_TAGS[Source.TELEGRAM] not in {tag.name for tag in lead.tags}:
            await add_tag(session, lead, SOURCE_TAGS[Source.TELEGRAM])
        if lead.tg_username != user.username:
            await update_lead_fields(session, lead, tg_username=user.username)
    await _record(session, lead, message, Direction.IN)


async def _record(session: AsyncSession, lead: Lead, message: Message, direction: Direction) -> None:
    await add_message(
        session,
        lead,
        direction=direction,
        channel=Channel.BUSINESS,
        text=message_content(message),
        sent_at=message.date,
        tg_message_id=message.message_id,
    )


def message_content(message: Message) -> str:
    """The text as written, or a label like "[фото]" with the caption after it."""
    if message.text is not None:
        return message.text
    label = CONTENT_LABELS.get(message.content_type, OTHER_CONTENT)
    return f"{label} {message.caption}" if message.caption else label


def _profile_name(user: User) -> str | None:
    return " ".join(f"{user.first_name} {user.last_name or ''}".split()) or None
