import asyncio
from contextlib import suppress
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from leadbox.bot import business, dialog
from leadbox.bot.transaction import UpdateTransactionMiddleware
from leadbox.config import Settings

WEBHOOK_PATH = "/tg/webhook"
ALLOWED_UPDATES = [
    "message",
    "callback_query",
    "business_connection",
    "business_message",
    "edited_business_message",
]


@dataclass
class BotRuntime:
    bot: Bot
    dispatcher: Dispatcher
    webhook_secret: str
    polling: asyncio.Task[None] | None = None


def build_dispatcher(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> Dispatcher:
    # FSM off: the form's state lives in leads.form_step, so a restart of the host loses nothing.
    dispatcher = Dispatcher(disable_fsm=True, settings=settings)
    dispatcher.update.outer_middleware(UpdateTransactionMiddleware(sessionmaker, settings.manager_chat_id))
    dispatcher.include_router(dialog.build_router())
    dispatcher.include_router(business.build_router())
    return dispatcher


async def start_bot(app: FastAPI, settings: Settings, *, api_session: BaseSession | None = None) -> BotRuntime:
    """Create the bot, put it into app.state.bot_runtime and connect it to Telegram.

    Webhook mode registers the webhook; polling mode (local work) polls in a background task.
    `api_session` replaces the HTTP session to Telegram in tests.
    """
    dispatcher = build_dispatcher(app.state.sessionmaker, settings)
    if settings.bot_mode == "webhook":
        if not settings.public_base_url or not settings.webhook_secret:
            raise RuntimeError("webhook mode needs PUBLIC_BASE_URL and WEBHOOK_SECRET")
        bot = Bot(settings.bot_token, session=api_session)
        # max_connections=1: Telegram delivers one update at a time, so two quick taps of the same
        # person never race for the same lead. One process and a handful of leads need no more.
        await bot.set_webhook(
            url=settings.public_base_url.rstrip("/") + WEBHOOK_PATH,
            secret_token=settings.webhook_secret,
            allowed_updates=ALLOWED_UPDATES,
            drop_pending_updates=False,
            max_connections=1,
        )
        runtime = BotRuntime(bot, dispatcher, settings.webhook_secret)
    else:
        if not settings.dev_bot_token:
            # Polling with BOT_TOKEN would delete the stand's webhook and silently take the bot off it.
            raise RuntimeError("polling mode needs DEV_BOT_TOKEN, a separate bot for local work")
        bot = Bot(settings.dev_bot_token, session=api_session)
        # getUpdates is refused while a webhook is set.
        await bot.delete_webhook(drop_pending_updates=False)
        polling = asyncio.create_task(
            dispatcher.start_polling(
                bot,
                allowed_updates=ALLOWED_UPDATES,
                handle_as_tasks=False,  # one update at a time, as the webhook gets them
                handle_signals=False,  # uvicorn owns the signals
                close_bot_session=False,
            )
        )
        runtime = BotRuntime(bot, dispatcher, settings.webhook_secret, polling)
    app.state.bot_runtime = runtime
    return runtime


async def stop_bot(app: FastAPI) -> None:
    runtime: BotRuntime = app.state.bot_runtime
    if runtime.polling is not None:
        runtime.polling.cancel()
        with suppress(asyncio.CancelledError):
            await runtime.polling
    await runtime.bot.session.close()
