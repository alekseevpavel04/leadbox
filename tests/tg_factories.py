"""Telegram updates built by hand and a Bot API session that never leaves the process."""

import asyncio
import itertools
import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteWebhook,
    EditMessageReplyMarkup,
    GetMe,
    GetUpdates,
    SendMessage,
    SetWebhook,
    TelegramMethod,
)
from aiogram.types import Chat, Message, Update, User

from leadbox.bot.runtime import build_dispatcher
from leadbox.models import Lead
from leadbox.services.leads import get_lead, list_leads

USER_ID = 1001
MESSAGE_DATE = 1_790_000_000  # 2026-09-21, a fixed message date to check sent_at against

_message_ids = itertools.count(1)


class FakeApiSession(BaseSession):
    """Records every Bot API call and answers like Telegram would.

    `fail` makes every call of the given method types raise, `fail_chats` only messages to those chats.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.fail: set[type[TelegramMethod[Any]]] = set()
        self.fail_chats: set[int] = set()
        self._message_ids = itertools.count(5000)

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        self.calls.append(method)
        if type(method) in self.fail or (isinstance(method, SendMessage) and method.chat_id in self.fail_chats):
            raise TelegramBadRequest(method=method, message="Bad Request: simulated")
        if isinstance(method, SendMessage):
            return Message(
                message_id=next(self._message_ids),
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        if isinstance(method, AnswerCallbackQuery | EditMessageReplyMarkup | SetWebhook | DeleteWebhook):
            return True
        if isinstance(method, GetMe):
            return User(id=42, is_bot=True, first_name="Leadbox", username="leadbox_test_bot")
        if isinstance(method, GetUpdates):
            await asyncio.sleep(0.01)
            return []
        raise NotImplementedError(f"the fake Bot API does not know {type(method).__name__}")

    async def close(self) -> None:
        pass

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError
        yield b""

    def sent(self, chat_id: int | None = None) -> list[SendMessage]:
        return [
            call
            for call in self.calls
            if isinstance(call, SendMessage) and (chat_id is None or call.chat_id == chat_id)
        ]

    def sent_texts(self, chat_id: int = USER_ID) -> list[str]:
        return [call.text for call in self.sent(chat_id)]

    def last_text(self, chat_id: int = USER_ID) -> str:
        return self.sent_texts(chat_id)[-1]

    def last_markup(self, chat_id: int = USER_ID) -> Any:
        return self.sent(chat_id)[-1].reply_markup

    def of_type(self, method_type: type[TelegramMethod[Any]]) -> list[TelegramMethod[Any]]:
        return [call for call in self.calls if isinstance(call, method_type)]


def tg_user(user_id: int = USER_ID, first_name: str = "Анна", username: str | None = "anna_tg") -> dict[str, Any]:
    user: dict[str, Any] = {"id": user_id, "is_bot": False, "first_name": first_name}
    if username:
        user["username"] = username
    return user


def message(user: dict[str, Any] | None = None, chat_type: str = "private", **content: Any) -> dict[str, Any]:
    user = user or tg_user()
    chat = {"id": user["id"], "type": "private"} if chat_type == "private" else {"id": -100500, "type": chat_type}
    body: dict[str, Any] = {
        "message_id": next(_message_ids),
        "date": MESSAGE_DATE,
        "chat": chat,
        "from": user,
        **content,
    }
    _add_command_entity(body)
    return {"message": body}


def _add_command_entity(body: dict[str, Any]) -> None:
    if command := re.match(r"/\w+(@\w+)?", body.get("text", "")):
        body["entities"] = [{"type": "bot_command", "offset": 0, "length": command.end()}]


def text(value: str, **kwargs: Any) -> dict[str, Any]:
    return message(text=value, **kwargs)


def sticker(**kwargs: Any) -> dict[str, Any]:
    return message(
        sticker={
            "file_id": "sticker-file",
            "file_unique_id": "sticker-unique",
            "type": "regular",
            "width": 512,
            "height": 512,
            "is_animated": False,
            "is_video": False,
        },
        **kwargs,
    )


def photo(**kwargs: Any) -> dict[str, Any]:
    return message(
        photo=[{"file_id": "photo-file", "file_unique_id": "photo-unique", "width": 90, "height": 90}], **kwargs
    )


def voice(**kwargs: Any) -> dict[str, Any]:
    return message(voice={"file_id": "voice-file", "file_unique_id": "voice-unique", "duration": 3}, **kwargs)


def shared_contact(phone: str, owner_id: int | None = USER_ID, **kwargs: Any) -> dict[str, Any]:
    contact: dict[str, Any] = {"phone_number": phone, "first_name": "Анна"}
    if owner_id is not None:
        contact["user_id"] = owner_id
    return message(contact=contact, **kwargs)


def button(data: str, user: dict[str, Any] | None = None, message_id: int = 1) -> dict[str, Any]:
    user = user or tg_user()
    return {
        "callback_query": {
            "id": f"cb-{next(_message_ids)}",
            "from": user,
            "chat_instance": "ci",
            "data": data,
            "message": {
                "message_id": message_id,
                "date": MESSAGE_DATE,
                "chat": {"id": user["id"], "type": "private"},
                "from": {"id": 42, "is_bot": True, "first_name": "Leadbox"},
                "text": "old prompt",
                "reply_markup": {"inline_keyboard": [[{"text": "x", "callback_data": data}]]},
            },
        }
    }


OWNER_ID = 2002
CONNECTION_ID = "bc-test-connection"


def owner_user() -> dict[str, Any]:
    return tg_user(OWNER_ID, first_name="Павел", username="pavel_work")


def business_connection(is_enabled: bool = True, owner: dict[str, Any] | None = None) -> dict[str, Any]:
    owner = owner or owner_user()
    return {
        "business_connection": {
            "id": CONNECTION_ID,
            "user": owner,
            "user_chat_id": owner["id"],
            "date": MESSAGE_DATE,
            "is_enabled": is_enabled,
        }
    }


def business_message(
    sender: dict[str, Any] | None = None,
    client: dict[str, Any] | None = None,
    *,
    edited: bool = False,
    message_id: int | None = None,
    date: int = MESSAGE_DATE,
    **content: Any,
) -> dict[str, Any]:
    """A message in a chat of the business account. `client` is the other person, so the chat is theirs.

    The sender defaults to the client (incoming); pass `sender=owner_user()` for the owner's reply.
    """
    sender = sender or tg_user()
    client = client or sender
    body: dict[str, Any] = {
        "message_id": next(_message_ids) if message_id is None else message_id,
        "date": date,
        "chat": {"id": client["id"], "type": "private", "first_name": client["first_name"]},
        "from": sender,
        "business_connection_id": CONNECTION_ID,
        **content,
    }
    _add_command_entity(body)
    if edited:
        body["edit_date"] = date + 60
    return {"edited_business_message" if edited else "business_message": body}


def owner_reply(client: dict[str, Any] | None = None, **content: Any) -> dict[str, Any]:
    return business_message(sender=owner_user(), client=client or tg_user(), **content)


class TgHarness:
    """The real dispatcher with the real middleware, fed updates directly instead of over HTTP."""

    def __init__(self, sessionmaker: Any, settings: Any) -> None:
        self.sessionmaker = sessionmaker
        self.api = FakeApiSession()
        self.bot = Bot(settings.bot_token, session=self.api)
        self.dispatcher = build_dispatcher(sessionmaker, settings)
        self._update_ids = itertools.count(1)

    def update(self, payload: dict[str, Any], update_id: int | None = None) -> Update:
        update_id = next(self._update_ids) if update_id is None else update_id
        return Update.model_validate({"update_id": update_id, **payload}, context={"bot": self.bot})

    async def send(self, payload: dict[str, Any], update_id: int | None = None) -> None:
        await self.dispatcher.feed_update(self.bot, self.update(payload, update_id))

    async def leads(self) -> list[Lead]:
        """All leads, oldest first, each with tags and messages, read in a fresh session."""
        async with self.sessionmaker() as session:
            ids = [lead.id for lead in await list_leads(session)]
            return [await get_lead(session, lead_id) for lead_id in reversed(ids)]

    async def only_lead(self) -> Lead:
        leads = await self.leads()
        assert len(leads) == 1, f"expected one lead, got {len(leads)}"
        return leads[0]
