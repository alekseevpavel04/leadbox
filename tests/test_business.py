from datetime import UTC, datetime

import pytest
from aiogram.methods import SendMessage

from leadbox.bot import dialog
from leadbox.models import Channel, Direction, FormStep, Source, Status
from leadbox.services.leads import get_lead, set_status
from leadbox.services.telegram import get_business_connection
from tg_factories import (
    CONNECTION_ID,
    MESSAGE_DATE,
    OWNER_ID,
    USER_ID,
    TgHarness,
    business_connection,
    business_message,
    button,
    owner_reply,
    text,
    tg_user,
)

MANAGER_CHAT_ID = -1001234567890


@pytest.fixture
async def tg(sessionmaker, settings):
    harness = TgHarness(sessionmaker, settings)
    await harness.send(business_connection())
    return harness


def client(**kwargs):
    return {**tg_user(), "last_name": "Смирнова", **kwargs}


def at(timestamp):
    return datetime.fromtimestamp(timestamp, UTC)


def notifications(tg):
    return tg.api.sent_texts(MANAGER_CHAT_ID)


async def test_connection_is_stored_and_can_be_disabled(sessionmaker, settings):
    tg = TgHarness(sessionmaker, settings)
    await tg.send(business_connection())
    async with sessionmaker() as session:
        connection = await get_business_connection(session, CONNECTION_ID)
        assert (connection.owner_user_id, connection.is_enabled) == (OWNER_ID, True)

    await tg.send(business_connection(is_enabled=False))
    async with sessionmaker() as session:
        connection = await get_business_connection(session, CONNECTION_ID)
        assert (connection.owner_user_id, connection.is_enabled) == (OWNER_ID, False)
    assert tg.api.calls == []


async def test_client_message_creates_a_telegram_lead(tg):
    await tg.send(business_message(client(), text="Здравствуйте, нужна реклама кофейни"))

    lead = await tg.only_lead()
    assert lead.source == Source.TELEGRAM
    assert {tag.name for tag in lead.tags} == {"telegram"}
    assert (lead.name, lead.contact, lead.request) == (
        "Анна Смирнова",
        "@anna_tg",
        "Здравствуйте, нужна реклама кофейни",
    )
    assert (lead.tg_user_id, lead.tg_username, lead.form_step) == (USER_ID, "anna_tg", None)
    assert lead.first_response_at is None
    [message] = lead.messages
    assert (message.direction, message.channel) == (Direction.IN, Channel.BUSINESS)
    assert message.sent_at == at(MESSAGE_DATE)

    [notification] = notifications(tg)
    assert notification.startswith("<b>Новый лид: личка</b>")
    assert f"https://leadbox.test/leads/{lead.id}" in notification


async def test_client_without_username_gets_an_empty_contact(tg):
    await tg.send(business_message(tg_user(username=None), text="Добрый день"))
    lead = await tg.only_lead()
    assert (lead.contact, lead.tg_username) == (None, None)


async def test_bot_writes_nothing_into_the_business_chat(tg):
    await tg.send(business_message(client(), text="Привет"))
    await tg.send(business_message(client(), text="Есть кто?"))
    await tg.send(owner_reply(text="Да, слушаю"))
    assert [call.chat_id for call in tg.api.calls if isinstance(call, SendMessage)] == [MANAGER_CHAT_ID]
    assert len(tg.api.calls) == 1


async def test_second_message_goes_to_the_same_lead_without_a_second_notification(tg):
    await tg.send(business_message(client(), text="Здравствуйте"))
    await tg.send(business_message(client(), text="Нужна реклама кофейни"))

    lead = await tg.only_lead()
    assert lead.request == "Здравствуйте"
    assert [m.text for m in lead.messages] == ["Здравствуйте", "Нужна реклама кофейни"]
    assert len(notifications(tg)) == 1


async def test_owner_reply_marks_the_first_response_and_creates_no_lead(tg):
    await tg.send(business_message(client(), text="Здравствуйте"))
    await tg.send(owner_reply(text="Добрый день! Расскажите подробнее", date=MESSAGE_DATE + 300))
    await tg.send(owner_reply(text="Вы тут?", date=MESSAGE_DATE + 900))

    lead = await tg.only_lead()
    assert lead.first_response_at == at(MESSAGE_DATE + 300)
    assert [(m.direction, m.channel) for m in lead.messages] == [
        (Direction.IN, Channel.BUSINESS),
        (Direction.OUT, Channel.BUSINESS),
        (Direction.OUT, Channel.BUSINESS),
    ]
    assert len(notifications(tg)) == 1


async def test_owner_message_in_a_chat_without_a_lead_creates_nothing(tg):
    # An old chat, or someone the owner writes first: not a lead the bot should invent.
    await tg.send(owner_reply(text="Привет, как дела?"))
    assert await tg.leads() == []
    assert tg.api.calls == []


async def test_automatic_greeting_is_not_an_answer(tg):
    await tg.send(business_message(client(), text="Здравствуйте"))
    await tg.send(owner_reply(text="Спасибо за сообщение, отвечу в течение часа", is_from_offline=True))

    lead = await tg.only_lead()
    assert lead.first_response_at is None
    assert [m.direction for m in lead.messages] == [Direction.IN, Direction.OUT]


async def test_messages_from_bots_are_ignored(tg):
    bot_user = {**tg_user(3003, first_name="Spam", username="spam_bot"), "is_bot": True}
    await tg.send(business_message(bot_user, text="Купите подписчиков"))
    assert await tg.leads() == []
    assert tg.api.calls == []


async def test_repeated_update_changes_nothing(tg):
    payload = business_message(client(), text="Здравствуйте")
    await tg.send(payload, update_id=500)
    await tg.send(payload, update_id=500)

    lead = await tg.only_lead()
    assert len(lead.messages) == 1
    assert len(notifications(tg)) == 1


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({"photo": [{"file_id": "p", "file_unique_id": "pu", "width": 90, "height": 90}]}, "[фото]"),
        (
            {
                "photo": [{"file_id": "p", "file_unique_id": "pu", "width": 90, "height": 90}],
                "caption": "Вот такой макет",
            },
            "[фото] Вот такой макет",
        ),
        ({"voice": {"file_id": "v", "file_unique_id": "vu", "duration": 3}}, "[голосовое]"),
        ({"video_note": {"file_id": "n", "file_unique_id": "nu", "length": 240, "duration": 5}}, "[кружок]"),
        (
            {
                "sticker": {
                    "file_id": "s",
                    "file_unique_id": "su",
                    "type": "regular",
                    "width": 512,
                    "height": 512,
                    "is_animated": False,
                    "is_video": False,
                }
            },
            "[стикер]",
        ),
        ({"document": {"file_id": "d", "file_unique_id": "du", "file_name": "brief.pdf"}}, "[файл]"),
        (
            # Telegram sends a GIF with a document alongside the animation, for old clients.
            {
                "animation": {
                    "file_id": "a",
                    "file_unique_id": "au",
                    "width": 320,
                    "height": 240,
                    "duration": 2,
                },
                "document": {"file_id": "a", "file_unique_id": "au"},
            },
            "[гиф]",
        ),
        ({"story": {"chat": {"id": 3004, "type": "channel", "title": "Канал"}, "id": 1}}, "[без текста]"),
    ],
)
async def test_non_text_message_is_recorded_as_a_label(tg, content, expected):
    await tg.send(business_message(client(), **content))

    lead = await tg.only_lead()
    assert lead.request == expected
    assert [m.text for m in lead.messages] == [expected]
    assert len(notifications(tg)) == 1


async def test_edit_updates_the_history(tg):
    await tg.send(business_message(client(), text="Нужен сайт", message_id=777))
    await tg.send(owner_reply(text="Какой бюджет?", message_id=778))
    await tg.send(business_message(client(), text="Нужен лендинг", message_id=777, edited=True))
    await tg.send(owner_reply(text="Какой у вас бюджет?", message_id=778, edited=True))

    lead = await tg.only_lead()
    assert [m.text for m in lead.messages] == ["Нужен лендинг", "Какой у вас бюджет?"]
    assert len(notifications(tg)) == 1


async def test_person_from_the_bot_form_keeps_one_lead_with_both_tags(tg):
    await tg.send(text("/start camp_a"))
    sent_before = len(tg.api.calls)

    await tg.send(business_message(client(), text="Решил написать напрямую"))
    await tg.send(owner_reply(text="Здравствуйте!", date=MESSAGE_DATE + 60))

    lead = await tg.only_lead()
    assert lead.source == Source.BOT
    assert {tag.name for tag in lead.tags} == {"бот", "кампания:camp_a", "telegram"}
    assert lead.form_step == FormStep.NAME
    assert lead.first_response_at == at(MESSAGE_DATE + 60)
    assert [(m.channel, m.direction) for m in lead.messages] == [
        (Channel.BUSINESS, Direction.IN),
        (Channel.BUSINESS, Direction.OUT),
    ]
    # The dropped form never reached the managers, so this message does; the person hears nothing.
    [notification] = notifications(tg)
    assert notification.startswith("<b>Новый лид: бот</b>")
    assert len(tg.api.calls) == sent_before + 1


async def test_lead_announced_from_business_is_not_announced_again_by_the_form(tg):
    await tg.send(text("/start"))
    await tg.send(business_message(client(), text="Решил написать напрямую"))
    await tg.send(text("Анна"))
    await tg.send(text("@anna_tg"))
    await tg.send(text("Нужна реклама"))
    await tg.send(button(dialog.CB_SEND))

    assert (await tg.only_lead()).form_step == FormStep.DONE
    assert len(notifications(tg)) == 1


async def test_closed_lead_gives_way_to_a_new_one(tg):
    await tg.send(business_message(client(), text="Первый заказ"))
    async with tg.sessionmaker() as session:
        lead = await get_lead(session, (await tg.only_lead()).id)
        await set_status(session, lead, Status.WON)
        await session.commit()

    await tg.send(business_message(client(), text="Нужна ещё одна кампания"))
    first, second = await tg.leads()
    assert (first.status, second.status) == (Status.WON, Status.NEW)
    assert second.request == "Нужна ещё одна кампания"
    assert len(notifications(tg)) == 2


async def test_html_in_profile_and_text_is_escaped_in_the_notification(tg):
    sender = tg_user(first_name="<b>Анна</b>", username=None)
    await tg.send(business_message(sender, text="<script>alert(1)</script>"))

    [notification] = notifications(tg)
    assert "Имя: &lt;b&gt;Анна&lt;/b&gt;" in notification
    assert "Запрос: &lt;script&gt;alert(1)&lt;/script&gt;" in notification
    assert "<script>" not in notification
    assert (await tg.only_lead()).name == "<b>Анна</b>"


async def test_start_in_a_business_chat_does_not_run_the_bot_form(tg):
    await tg.send(business_message(client(), text="/start camp_a"))

    lead = await tg.only_lead()
    assert lead.source == Source.TELEGRAM
    assert lead.form_step is None
    assert lead.campaign is None
    assert {tag.name for tag in lead.tags} == {"telegram"}
    assert tg.api.sent(USER_ID) == []


async def test_unknown_connection_still_tells_owner_from_client(sessionmaker, settings):
    # The business_connection update was lost or came late: nothing is in the table.
    tg = TgHarness(sessionmaker, settings)
    await tg.send(owner_reply(text="Здравствуйте, это менеджер"))
    assert await tg.leads() == []

    await tg.send(business_message(client(), text="Здравствуйте"))
    await tg.send(owner_reply(text="Слушаю вас", date=MESSAGE_DATE + 120))
    lead = await tg.only_lead()
    assert lead.source == Source.TELEGRAM
    assert lead.first_response_at == at(MESSAGE_DATE + 120)
    assert [m.direction for m in lead.messages] == [Direction.IN, Direction.OUT]
