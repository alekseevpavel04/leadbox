import logging
from secrets import compare_digest
from typing import Annotated

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy.exc import DBAPIError

from leadbox.bot.runtime import WEBHOOK_PATH, BotRuntime

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(WEBHOOK_PATH, include_in_schema=False)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[str, Header()] = "",
) -> Response:
    runtime: BotRuntime = request.app.state.bot_runtime
    secret = runtime.webhook_secret
    if not secret or not compare_digest(x_telegram_bot_api_secret_token.encode(), secret.encode()):
        raise HTTPException(status_code=403)
    update = Update.model_validate(await request.json(), context={"bot": runtime.bot})
    # The update is handled before answering, so a failure can still ask Telegram to resend it.
    # Its transaction is rolled back either way, update_id included. Only a database failure
    # (Neon waking up, a dropped connection) gets 503 and a retry: it likely passes next time.
    # Any other error is a bug that would fail on every retry, and with max_connections=1 the
    # retries would hold up everyone else's updates; it is logged and answered with 200.
    try:
        await runtime.dispatcher.feed_update(runtime.bot, update)
    except (DBAPIError, OSError):
        logger.exception("update %s failed on the database, asking Telegram to retry", update.update_id)
        return Response(status_code=503)
    except Exception:
        logger.exception("update %s failed and is dropped", update.update_id)
    return Response(status_code=200)
