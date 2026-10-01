from datetime import UTC, datetime

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup
from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove

from leadbox.bot import dialog
from leadbox.models import FormStep, Source, Status
from leadbox.services.leads import create_lead, get_lead, set_status
from tg_factories import (
    MESSAGE_DATE,
    USER_ID,
    TgHarness,
    button,
    photo,
    shared_contact,
    sticker,
    text,
    tg_user,
    voice,
)

MANAGER_CHAT_ID = -1001234567890


@pytest.fixture
def tg(sessionmaker, settings):
    return TgHarness(sessionmaker, settings)


async def fill_form(tg, start="/start camp_test", contact="8 (999) 123-45-67", request="Нужна реклама кофейни"):
    await tg.send(text(start))
    await tg.send(button(dialog.CB_PROFILE_NAME))
    await tg.send(text(contact))
    await tg.send(text(request))
    await tg.send(button(dialog.CB_SEND))


def button_data(markup):
    return [row[0].callback_data for row in markup.inline_keyboard]


async def test_full_form_from_campaign_link(tg):
    await fill_form(tg)

    lead = await tg.only_lead()
    assert lead.form_step == FormStep.DONE
    assert lead.status == Status.NEW
    assert (lead.name, lead.contact, lead.request) == ("Анна", "+79991234567", "Нужна реклама кофейни")
    assert lead.campaign == "camp_test"
    assert lead.tg_user_id == USER_ID
    assert lead.tg_username == "anna_tg"
    assert {tag.name for tag in lead.tags} == {"бот", "кампания:camp_test"}
    assert [m.text for m in lead.messages] == ["8 (999) 123-45-67", "Нужна реклама кофейни"]
    assert lead.messages[0].sent_at == datetime.fromtimestamp(MESSAGE_DATE, UTC)

    texts = tg.api.sent_texts()
    assert texts[0].startswith(dialog.GREETING)
    assert "соглашаетесь на их обработку" in texts[0]
    assert texts[-1] == dialog.DONE
    assert len(tg.api.sent(MANAGER_CHAT_ID)) == 1


async def test_name_can_be_typed(tg):
    await tg.send(text("/start"))
    await tg.send(text("  Пётр   Иванович "))
    lead = await tg.only_lead()
    assert lead.name == "Пётр Иванович"
    assert lead.form_step == FormStep.CONTACT
    assert [m.text for m in lead.messages] == ["  Пётр   Иванович "]


async def test_name_button_shows_profile_name(tg):
    await tg.send(text("/start"))
    markup = tg.api.last_markup()
    assert markup.inline_keyboard[0][0].text == "Да, так и зовите (Анна)"


async def test_repeated_start_mid_form_continues_the_same_lead(tg):
    await tg.send(text("/start camp_a"))
    await tg.send(text("Анна"))
    await tg.send(text("/start"))

    lead = await tg.only_lead()
    assert lead.form_step == FormStep.CONTACT
    assert lead.name == "Анна"
    assert tg.api.last_text().startswith(dialog.RESUME)
    assert dialog.ASK_CONTACT in tg.api.last_text()
    assert isinstance(tg.api.last_markup(), ReplyKeyboardMarkup)


async def test_repeated_start_with_another_campaign_adds_its_tag(tg):
    await tg.send(text("/start camp_a"))
    await tg.send(text("/start camp_b"))
    lead = await tg.only_lead()
    assert lead.campaign == "camp_a"
    assert {tag.name for tag in lead.tags} == {"бот", "кампания:camp_a", "кампания:camp_b"}


@pytest.mark.parametrize("payload", ["bad payload!", "кампания", "a" * 56, "camp.test"])
async def test_invalid_payload_is_ignored(tg, payload):
    await tg.send(text(f"/start {payload}"))
    lead = await tg.only_lead()
    assert lead.campaign is None
    assert {tag.name for tag in lead.tags} == {"бот"}
    assert lead.form_step == FormStep.NAME
    assert tg.api.last_text().startswith(dialog.GREETING)


async def test_payload_tag_is_lowercased_and_55_chars_fit(tg):
    payload = "Camp_" + "x" * 50
    await tg.send(text(f"/start {payload}"))
    lead = await tg.only_lead()
    assert lead.campaign == payload
    assert f"кампания:{payload.lower()}" in {tag.name for tag in lead.tags}


@pytest.mark.parametrize("non_text", [sticker, photo, voice])
async def test_non_text_on_name_step_asks_for_text(tg, non_text):
    await tg.send(text("/start"))
    await tg.send(non_text())
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.NAME
    assert lead.name is None
    assert lead.messages == []
    assert tg.api.last_text().startswith(dialog.TEXT_ONLY)
    assert dialog.ASK_NAME in tg.api.last_text()


async def test_sticker_as_first_message_starts_a_lead(tg):
    await tg.send(sticker())
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.NAME
    assert tg.api.last_text().startswith(dialog.GREETING)


async def test_text_as_first_message_starts_a_lead_and_is_kept(tg):
    await tg.send(text("Здравствуйте, сколько стоит таргет?"))
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.NAME
    assert lead.name is None
    assert [m.text for m in lead.messages] == ["Здравствуйте, сколько стоит таргет?"]


async def test_too_long_name_is_rejected(tg):
    await tg.send(text("/start"))
    await tg.send(text("Я" * 65))
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.NAME
    assert tg.api.last_text().startswith(dialog.NAME_TOO_LONG)


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("8 (999) 123-45-67", "+79991234567"),
        ("+7 999 1234567", "+79991234567"),
        ("@user", "@user"),
        ("a@b.c", "a@b.c"),
    ],
)
async def test_valid_contact_is_normalized(tg, typed, stored):
    await tg.send(text("/start"))
    await tg.send(text("Анна"))
    await tg.send(text(typed))
    lead = await tg.only_lead()
    assert lead.contact == stored
    assert lead.form_step == FormStep.REQUEST
    saved = tg.api.sent()[-2]
    assert saved.text == dialog.CONTACT_SAVED.format(contact=stored)
    assert isinstance(saved.reply_markup, ReplyKeyboardRemove)
    assert dialog.ASK_REQUEST in tg.api.last_text()


@pytest.mark.parametrize("typed", ["12345", "abc"])
async def test_invalid_contact_gets_an_example(tg, typed):
    await tg.send(text("/start"))
    await tg.send(text("Анна"))
    await tg.send(text(typed))
    lead = await tg.only_lead()
    assert lead.contact is None
    assert lead.form_step == FormStep.CONTACT
    assert tg.api.last_text().startswith(dialog.CONTACT_INVALID)
    assert "+7 999 123-45-67" in tg.api.last_text()
    assert isinstance(tg.api.last_markup(), ReplyKeyboardMarkup)


async def test_own_shared_contact_is_accepted(tg):
    await tg.send(text("/start"))
    await tg.send(text("Анна"))
    await tg.send(shared_contact("79991234567"))
    lead = await tg.only_lead()
    assert lead.contact == "+79991234567"
    assert lead.form_step == FormStep.REQUEST


@pytest.mark.parametrize("owner_id", [2002, None])
async def test_foreign_shared_contact_is_rejected(tg, owner_id):
    await tg.send(text("/start"))
    await tg.send(text("Анна"))
    await tg.send(shared_contact("79990000000", owner_id=owner_id))
    lead = await tg.only_lead()
    assert lead.contact is None
    assert lead.form_step == FormStep.CONTACT
    assert tg.api.last_text().startswith(dialog.CONTACT_FOREIGN)


async def reach_request_step(tg):
    await tg.send(text("/start"))
    await tg.send(text("Анна"))
    await tg.send(text("@anna_tg"))


async def test_too_long_request_asks_to_shorten(tg):
    await reach_request_step(tg)
    await tg.send(text("а" * 1001))
    lead = await tg.only_lead()
    assert lead.request is None
    assert lead.form_step == FormStep.REQUEST
    assert tg.api.last_text().startswith(dialog.REQUEST_TOO_LONG.format(length=1001))


@pytest.mark.parametrize("gap", [0, 1, 2])
async def test_text_split_by_the_client_is_rejected_as_a_whole(tg, gap):
    # 5000 characters leave a Telegram client as two messages, 4096 and 904, within a second or two.
    await reach_request_step(tg)
    await tg.send(text("а" * 4096, date=MESSAGE_DATE))
    await tg.send(text("б" * 904, date=MESSAGE_DATE + gap))

    lead = await tg.only_lead()
    assert lead.request is None
    assert lead.form_step == FormStep.REQUEST
    answers = tg.api.sent_texts()[-2:]
    assert answers[0].startswith(dialog.REQUEST_TOO_LONG.format(length=4096))
    assert answers[1].startswith(dialog.REQUEST_TOO_LONG.format(length=5000))


async def test_text_split_in_three_counts_every_piece(tg):
    await reach_request_step(tg)
    for piece in ("а" * 4096, "б" * 4096, "в" * 808):
        await tg.send(text(piece))
    assert (await tg.only_lead()).form_step == FormStep.REQUEST
    assert tg.api.last_text().startswith(dialog.REQUEST_TOO_LONG.format(length=9000))


@pytest.mark.parametrize("gap", [3, 10])
async def test_short_request_after_a_rejected_long_one_is_accepted(tg, gap):
    await reach_request_step(tg)
    await tg.send(text("а" * 4096, date=MESSAGE_DATE))
    await tg.send(text("Нужна реклама кофейни", date=MESSAGE_DATE + gap))
    lead = await tg.only_lead()
    assert lead.request == "Нужна реклама кофейни"
    assert lead.form_step == FormStep.CONFIRM


async def test_request_of_exactly_1000_chars_is_accepted(tg):
    await reach_request_step(tg)
    await tg.send(text("а" * 1000))
    lead = await tg.only_lead()
    assert lead.request == "а" * 1000
    assert lead.form_step == FormStep.CONFIRM
    assert button_data(tg.api.last_markup()) == [dialog.CB_SEND, dialog.CB_EDIT]


async def test_text_on_confirm_step_repeats_the_summary(tg):
    await tg.send(text("/start"))
    await tg.send(text("Анна"))
    await tg.send(text("@anna_tg"))
    await tg.send(text("Нужен лендинг"))
    await tg.send(text("а ещё логотип"))
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.CONFIRM
    assert lead.request == "Нужен лендинг"
    assert tg.api.last_text().startswith(dialog.CONFIRM_HINT)
    assert "Запрос: Нужен лендинг" in tg.api.last_text()
    assert [m.text for m in lead.messages][-1] == "а ещё логотип"


async def test_edit_goes_back_with_current_values(tg):
    await tg.send(text("/start"))
    await tg.send(text("Аня"))
    await tg.send(text("@anna_tg"))
    await tg.send(text("Нужен лендинг"))
    await tg.send(button(dialog.CB_EDIT))

    assert (await tg.only_lead()).form_step == FormStep.NAME
    assert "Сейчас: Аня" in tg.api.last_text()
    assert button_data(tg.api.last_markup()) == [dialog.CB_PROFILE_NAME, dialog.CB_KEEP_NAME]

    await tg.send(button(dialog.CB_KEEP_NAME))
    assert "Сейчас: @anna_tg" in tg.api.last_text()
    keyboard = tg.api.last_markup().keyboard
    assert keyboard[1][0].text == dialog.BUTTON_KEEP

    await tg.send(text("+7 999 1234567"))
    assert "Сейчас: Нужен лендинг" in tg.api.last_text()
    await tg.send(button(dialog.CB_KEEP_REQUEST))

    lead = await tg.only_lead()
    assert (lead.name, lead.contact, lead.request) == ("Аня", "+79991234567", "Нужен лендинг")
    assert lead.form_step == FormStep.CONFIRM


async def test_keep_contact_button_keeps_the_old_value(tg):
    await tg.send(text("/start"))
    await tg.send(text("Аня"))
    await tg.send(text("@anna_tg"))
    await tg.send(text("Нужен лендинг"))
    await tg.send(button(dialog.CB_EDIT))
    await tg.send(text("Аня"))
    await tg.send(text(dialog.BUTTON_KEEP))
    lead = await tg.only_lead()
    assert lead.contact == "@anna_tg"
    assert lead.form_step == FormStep.REQUEST


async def test_keep_contact_text_without_a_value_is_just_invalid(tg):
    await tg.send(text("/start"))
    await tg.send(text("Аня"))
    await tg.send(text(dialog.BUTTON_KEEP))
    assert (await tg.only_lead()).form_step == FormStep.CONTACT
    assert tg.api.last_text().startswith(dialog.CONTACT_INVALID)


async def test_cancel_stops_the_form_without_notification(tg):
    await tg.send(text("/start camp_test"))
    await tg.send(text("Анна"))
    await tg.send(text("/cancel"))

    lead = await tg.only_lead()
    assert lead.form_step == FormStep.CANCELLED
    assert tg.api.last_text() == dialog.CANCELLED
    assert isinstance(tg.api.last_markup(), ReplyKeyboardRemove)

    await tg.send(text("+79991234567"))
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.CANCELLED
    assert lead.contact is None
    assert tg.api.last_text() == dialog.CANCELLED_HINT
    assert tg.api.sent(MANAGER_CHAT_ID) == []


async def test_start_after_cancel_resumes_at_the_first_empty_field(tg):
    await tg.send(text("/start"))
    await tg.send(text("Анна"))
    await tg.send(text("/cancel"))
    await tg.send(text("/start"))
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.CONTACT
    assert tg.api.last_text().startswith(dialog.RESUME)


async def test_cancel_without_a_form(tg):
    await tg.send(text("/cancel"))
    assert await tg.leads() == []
    assert tg.api.last_text() == dialog.NOTHING_TO_CANCEL


async def test_cancel_after_done_keeps_the_lead_done(tg):
    await fill_form(tg)
    await tg.send(text("/cancel"))
    assert (await tg.only_lead()).form_step == FormStep.DONE
    assert tg.api.last_text() == dialog.CANCEL_AFTER_DONE


async def test_old_send_button_after_done_is_answered_once_more_without_a_second_notification(tg):
    await fill_form(tg)
    await tg.send(button(dialog.CB_SEND))

    assert (await tg.only_lead()).form_step == FormStep.DONE
    assert len(tg.api.sent(MANAGER_CHAT_ID)) == 1
    assert tg.api.last_text() == f"{dialog.STALE_BUTTON}\n\n{dialog.ALREADY_DONE}"


async def test_old_button_mid_form_repeats_the_current_step(tg):
    await tg.send(text("/start"))
    await tg.send(button(dialog.CB_SEND))
    assert (await tg.only_lead()).form_step == FormStep.NAME
    assert tg.api.last_text().startswith(dialog.STALE_BUTTON)
    assert dialog.ASK_NAME in tg.api.last_text()


async def test_button_without_any_lead(tg):
    await tg.send(button(dialog.CB_SEND))
    assert await tg.leads() == []
    assert tg.api.last_text() == f"{dialog.STALE_BUTTON}\n\n{dialog.NO_FORM}"


async def test_every_button_press_is_answered_and_loses_its_keyboard(tg):
    await fill_form(tg)
    await tg.send(button(dialog.CB_SEND))
    await tg.send(button("garbage"))
    await tg.send(button(dialog.CB_EDIT, user=tg_user(3003, username=None)))
    assert len(tg.api.of_type(AnswerCallbackQuery)) == 5
    assert len(tg.api.of_type(EditMessageReplyMarkup)) == 5


async def test_message_after_done_goes_to_history(tg):
    await fill_form(tg)
    await tg.send(text("Забыла: бюджет 50 тысяч"))
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.DONE
    assert lead.messages[-1].text == "Забыла: бюджет 50 тысяч"
    assert tg.api.last_text() == dialog.AFTER_DONE
    assert len(tg.api.sent(MANAGER_CHAT_ID)) == 1


async def test_start_after_done_does_not_restart_the_form(tg):
    await fill_form(tg)
    await tg.send(text("/start camp_other"))
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.DONE
    assert "кампания:camp_other" in {tag.name for tag in lead.tags}
    assert tg.api.last_text() == dialog.ALREADY_DONE


@pytest.mark.parametrize("closed", [Status.WON, Status.LOST])
async def test_closed_lead_is_not_continued(tg, closed):
    await fill_form(tg)
    first = await tg.only_lead()
    async with tg.sessionmaker() as session:
        await set_status(session, await get_lead(session, first.id), closed)
        await session.commit()

    await tg.send(text("/start"))
    leads = await tg.leads()
    assert len(leads) == 2
    assert leads[0].form_step == FormStep.DONE
    assert leads[1].form_step == FormStep.NAME
    assert leads[1].status == Status.NEW


async def test_unknown_command_repeats_the_step_and_is_not_recorded(tg):
    await tg.send(text("/start"))
    await tg.send(text("/help"))
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.NAME
    assert lead.messages == []
    assert tg.api.last_text().startswith(dialog.UNKNOWN_COMMAND)


async def test_username_change_is_picked_up(tg):
    await tg.send(text("/start"))
    await tg.send(text("Анна", user=tg_user(username="anna_new")))
    assert (await tg.only_lead()).tg_username == "anna_new"


async def test_group_messages_are_ignored(tg):
    await tg.send(text("/start", chat_type="supergroup"))
    await tg.send(text("привет", chat_type="supergroup"))
    assert await tg.leads() == []
    assert tg.api.sent() == []


async def test_two_people_get_two_leads(tg):
    await tg.send(text("/start"))
    await tg.send(text("/start", user=tg_user(2002, "Борис", None)))
    leads = await tg.leads()
    assert [lead.tg_user_id for lead in leads] == [USER_ID, 2002]


async def test_open_lead_without_a_form_joins_it_at_the_first_gap(tg):
    # An open lead from another source (a Business chat): the form fills only what is missing.
    async with tg.sessionmaker() as session:
        await create_lead(session, source=Source.TELEGRAM, name="Анна", request="Сколько стоит?", tg_user_id=USER_ID)
        await session.commit()

    await tg.send(text("/start"))
    lead = await tg.only_lead()
    assert lead.form_step == FormStep.CONTACT
    assert {tag.name for tag in lead.tags} == {"telegram", "бот"}
    assert tg.api.last_text().startswith(dialog.GREETING)
