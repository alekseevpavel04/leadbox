import asyncio
import logging

import httpx
import pytest
from aiogram.methods import DeleteWebhook, SetWebhook
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from leadbox.bot import dialog
from leadbox.bot.runtime import ALLOWED_UPDATES, start_bot, stop_bot
from leadbox.bot.webhook import router
from leadbox.models import FormStep
from leadbox.services.leads import get_lead, list_leads
from tg_factories import FakeApiSession, button, sticker, text

SECRET = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"}


@pytest.fixture
async def app(sessionmaker, settings):
    app = FastAPI()
    app.include_router(router)
    app.state.sessionmaker = sessionmaker
    await start_bot(app, settings, api_session=FakeApiSession())
    yield app
    await stop_bot(app)


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


def api(app) -> FakeApiSession:
    return app.state.bot_runtime.bot.session


async def leads(app):
    async with app.state.sessionmaker() as session:
        return [await get_lead(session, lead.id) for lead in await list_leads(session)]


def update(update_id, payload):
    return {"update_id": update_id, **payload}


async def test_startup_registers_the_webhook(app):
    [call] = api(app).of_type(SetWebhook)
    assert call.url == "https://leadbox.test/tg/webhook"
    assert call.secret_token == "test-webhook-secret"
    assert call.allowed_updates == ALLOWED_UPDATES
    assert call.drop_pending_updates is False
    assert call.max_connections == 1


@pytest.mark.parametrize("headers", [{}, {"X-Telegram-Bot-Api-Secret-Token": "wrong"}])
async def test_request_without_the_secret_is_refused(app, client, headers):
    response = await client.post("/tg/webhook", json=update(1, text("/start")), headers=headers)
    assert response.status_code == 403
    assert await leads(app) == []


async def test_update_creates_a_lead(app, client):
    response = await client.post("/tg/webhook", json=update(1, text("/start camp_test")), headers=SECRET)
    assert response.status_code == 200
    [lead] = await leads(app)
    assert {tag.name for tag in lead.tags} == {"бот", "кампания:camp_test"}
    assert api(app).last_text().startswith(dialog.GREETING)


async def test_repeated_update_changes_nothing(app, client):
    await client.post("/tg/webhook", json=update(1, text("/start")), headers=SECRET)
    answer = update(2, text("Анна"))
    assert (await client.post("/tg/webhook", json=answer, headers=SECRET)).status_code == 200
    sent_before = len(api(app).calls)

    assert (await client.post("/tg/webhook", json=answer, headers=SECRET)).status_code == 200

    [lead] = await leads(app)
    assert [m.text for m in lead.messages] == ["Анна"]
    assert lead.form_step == FormStep.CONTACT
    assert len(api(app).calls) == sent_before


async def test_bug_in_a_handler_rolls_back_and_is_answered_200(app, client, monkeypatch, caplog):
    async def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(dialog, "add_message", broken)
    with caplog.at_level(logging.ERROR, logger="leadbox.bot.webhook"):
        response = await client.post("/tg/webhook", json=update(1, text("Здравствуйте")), headers=SECRET)
    assert response.status_code == 200
    assert "update 1 failed and is dropped" in caplog.text
    assert await leads(app) == []
    assert api(app).sent() == []

    # Nothing was half-written, the update_id included: the same update is handled in full later.
    monkeypatch.undo()
    await client.post("/tg/webhook", json=update(1, text("Здравствуйте")), headers=SECRET)
    [lead] = await leads(app)
    assert [m.text for m in lead.messages] == ["Здравствуйте"]


async def test_database_failure_asks_telegram_to_retry(app, client, monkeypatch):
    async def db_down(*args, **kwargs):
        raise OperationalError("SELECT", {}, ConnectionError("connection was closed"))

    monkeypatch.setattr(dialog, "get_open_lead_by_tg", db_down)
    response = await client.post("/tg/webhook", json=update(1, sticker()), headers=SECRET)
    assert response.status_code == 503
    assert api(app).sent() == []

    monkeypatch.undo()
    assert (await client.post("/tg/webhook", json=update(1, sticker()), headers=SECRET)).status_code == 200
    assert len(await leads(app)) == 1


async def test_button_over_http_is_answered(app, client):
    await client.post("/tg/webhook", json=update(1, button(dialog.CB_SEND)), headers=SECRET)
    assert api(app).last_text().endswith(dialog.NO_FORM)


async def test_polling_mode_uses_the_dev_bot(sessionmaker, settings):
    app = FastAPI()
    app.state.sessionmaker = sessionmaker
    fake = FakeApiSession()
    runtime = await start_bot(app, settings.model_copy(update={"bot_mode": "polling"}), api_session=fake)
    await asyncio.sleep(0.05)
    assert runtime.bot.token == settings.dev_bot_token
    assert len(fake.of_type(DeleteWebhook)) == 1
    assert fake.of_type(SetWebhook) == []
    assert not runtime.polling.done()
    await stop_bot(app)
    assert runtime.polling.done()


async def test_polling_without_dev_token_refuses_to_start(sessionmaker, settings):
    app = FastAPI()
    app.state.sessionmaker = sessionmaker
    fake = FakeApiSession()
    polling = settings.model_copy(update={"bot_mode": "polling", "dev_bot_token": ""})
    with pytest.raises(RuntimeError, match="DEV_BOT_TOKEN"):
        await start_bot(app, polling, api_session=fake)
    assert fake.of_type(DeleteWebhook) == []


async def test_webhook_mode_needs_its_settings(sessionmaker, settings):
    app = FastAPI()
    app.state.sessionmaker = sessionmaker
    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
        await start_bot(app, settings.model_copy(update={"webhook_secret": ""}), api_session=FakeApiSession())
