"""The path the task asks for, end to end on the real app: a message to the bot, a lead with a tag in the CRM."""

import re

import httpx
import pytest
from aiogram.methods import SendMessage

from leadbox.bot import dialog
from leadbox.bot.runtime import start_bot, stop_bot
from leadbox.main import app as main_app
from tg_factories import FakeApiSession, button, text

SECRET = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"}


@pytest.fixture
async def app(sessionmaker, settings):
    main_app.state.sessionmaker = sessionmaker
    await start_bot(main_app, settings, api_session=FakeApiSession())
    yield main_app
    await stop_bot(main_app)


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
        yield client


async def login(client):
    page = await client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = await client.post("/login", data={"username": "demo", "password": "test-password", "csrf_token": csrf})
    assert response.status_code == 303


async def test_bot_message_becomes_a_tagged_lead_in_the_crm(app, client):
    steps = [
        text("/start camp_test"),
        button(dialog.CB_PROFILE_NAME),
        text("+7 999 123-45-67"),
        text("Нужна реклама для кофейни"),
        button(dialog.CB_SEND),
    ]
    for update_id, payload in enumerate(steps, start=1):
        response = await client.post("/tg/webhook", json={"update_id": update_id, **payload}, headers=SECRET)
        assert response.status_code == 200

    sent = app.state.bot_runtime.bot.session.of_type(SendMessage)
    assert any(call.text == dialog.DONE for call in sent)
    assert any(call.text.startswith("<b>Новый лид: бот</b>") for call in sent)

    await login(client)
    table = (await client.get("/leads/table", params={"tag": "кампания:camp_test"})).text
    assert "Нужна реклама для кофейни" in table
    assert "+79991234567" in table
    assert "анкета не завершена" not in table
    assert "кофейни" not in (await client.get("/leads/table", params={"tag": "вручную"})).text
