import logging
from types import SimpleNamespace

import pytest
from aiogram.methods import AnswerCallbackQuery

from leadbox.bot import dialog
from leadbox.bot.notify import lead_notification
from leadbox.models import FormStep, Source
from tg_factories import TgHarness, button, text

MANAGER_CHAT_ID = -1001234567890


@pytest.fixture
def tg(sessionmaker, settings):
    return TgHarness(sessionmaker, settings)


async def fill_form(tg, name, request="Нужна реклама"):
    await tg.send(text("/start camp_test"))
    await tg.send(text(name))
    await tg.send(text("@anna_tg"))
    await tg.send(text(request))
    await tg.send(button(dialog.CB_SEND))


async def test_done_form_notifies_the_managers(tg):
    await fill_form(tg, "Анна")
    lead = await tg.only_lead()
    [notification] = tg.api.sent(MANAGER_CHAT_ID)
    assert notification.parse_mode == "HTML"
    assert notification.text.startswith("<b>Новый лид: бот</b>")
    assert "Контакт: @anna_tg" in notification.text
    assert "Теги: бот, кампания:camp_test" in notification.text
    assert f'href="https://leadbox.test/leads/{lead.id}"' in notification.text


async def test_user_fields_are_escaped(tg):
    await fill_form(tg, "<script>alert(1)</script>", request="<b>жирный</b> & <i>курсив</i>")
    [notification] = tg.api.sent(MANAGER_CHAT_ID)
    assert "Имя: &lt;script&gt;alert(1)&lt;/script&gt;" in notification.text
    assert "Запрос: &lt;b&gt;жирный&lt;/b&gt; &amp; &lt;i&gt;курсив&lt;/i&gt;" in notification.text
    assert "<script>" not in notification.text
    assert "<b>жирный" not in notification.text


def test_long_request_is_cut_before_escaping():
    lead = SimpleNamespace(
        id=7, source=Source.BOT, name="Анна", contact=None, request="x" * 299 + "<b>" + "y" * 50, tags=[]
    )
    notification = lead_notification(lead, "https://leadbox.test/")
    assert "Запрос: " + "x" * 299 + "&lt;…" in notification
    assert "Контакт: -" in notification
    assert "Теги: -" in notification
    assert 'href="https://leadbox.test/leads/7"' in notification


async def test_failed_notification_keeps_the_lead(tg, caplog):
    tg.api.fail_chats.add(MANAGER_CHAT_ID)
    with caplog.at_level(logging.ERROR, logger="leadbox.bot.notify"):
        await fill_form(tg, "Анна")
    assert (await tg.only_lead()).form_step == FormStep.DONE
    assert tg.api.last_text() == dialog.DONE
    assert "failed to send lead notification" in caplog.text


async def test_failed_callback_answer_does_not_stop_the_rest(tg, caplog):
    # A callback answered too late (cold start) fails with "query is too old"; the reply still goes out.
    tg.api.fail.add(AnswerCallbackQuery)
    with caplog.at_level(logging.ERROR, logger="leadbox.bot.transaction"):
        await fill_form(tg, "Анна")
    assert (await tg.only_lead()).form_step == FormStep.DONE
    assert tg.api.last_text() == dialog.DONE
    assert len(tg.api.sent(MANAGER_CHAT_ID)) == 1
    assert "AnswerCallbackQuery failed after commit" in caplog.text


async def test_abandoned_form_sends_nothing(tg):
    await tg.send(text("/start"))
    await tg.send(text("Анна"))
    await tg.send(text("@anna_tg"))
    await tg.send(text("Нужна реклама"))
    assert (await tg.only_lead()).form_step == FormStep.CONFIRM
    assert tg.api.sent(MANAGER_CHAT_ID) == []


async def test_no_manager_chat_means_no_notification(sessionmaker, settings, caplog):
    tg = TgHarness(sessionmaker, settings.model_copy(update={"manager_chat_id": None}))
    with caplog.at_level(logging.WARNING, logger="leadbox.bot.notify"):
        await fill_form(tg, "Анна")
    assert (await tg.only_lead()).form_step == FormStep.DONE
    assert all(call.chat_id == tg.api.sent()[0].chat_id for call in tg.api.sent())
    assert "MANAGER_CHAT_ID is not set" in caplog.text
