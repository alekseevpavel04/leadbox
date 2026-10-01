import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import TelegramMethod
from aiogram.types import TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from leadbox.bot.notify import send_notification
from leadbox.services.telegram import mark_update_processed

logger = logging.getLogger(__name__)


@dataclass
class Outbox:
    """Bot API calls a handler wants made, sent only after its transaction commits.

    Handlers never call Telegram directly. If the commit fails, the person has not been told
    "accepted" for a lead that is not there, and the retried update does not send everything twice.
    """

    replies: list[TelegramMethod[Any]] = field(default_factory=list)
    notifications: list[str] = field(default_factory=list)


class UpdateTransactionMiddleware(BaseMiddleware):
    """One database transaction per update, shared by webhook and polling.

    The update_id is recorded in the same transaction as the handler's changes: a repeated update
    is skipped, and a failed one is rolled back together with its mark, so a retry handles it anew.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], manager_chat_id: int | None) -> None:
        self.sessionmaker = sessionmaker
        self.manager_chat_id = manager_chat_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        outbox = Outbox()
        async with self.sessionmaker() as session:
            if not await mark_update_processed(session, event.update_id):
                logger.info("update %s already processed, skipped", event.update_id)
                return None
            result = await handler(event, {**data, "session": session, "outbox": outbox})
            await session.commit()
        await self._deliver(data["bot"], outbox)
        return result

    async def _deliver(self, bot: Bot, outbox: Outbox) -> None:
        # The update is committed and marked: raising now would not bring a retry, only cut off
        # the rest of the queue. A callback answered after a cold start ("query is too old") must
        # not keep the next message from going out, so each failed call is logged and skipped.
        for method in outbox.replies:
            try:
                await bot(method)
            except TelegramAPIError:
                logger.exception("Bot API call %s failed after commit", type(method).__name__)
        for text in outbox.notifications:
            await send_notification(bot, self.manager_chat_id, text)
