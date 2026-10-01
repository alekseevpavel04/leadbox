import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from leadbox.crm import install_crm
from leadbox.crm.templating import waiting
from leadbox.models import Channel, Direction, FormStep, Lead, Source, Status
from leadbox.services.leads import add_message, create_lead, get_lead, set_status
from leadbox.services.tags import add_tag

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
HTMX = {"HX-Request": "true"}


def csrf_from(html: str) -> str:
    return CSRF_RE.search(html).group(1)


@pytest.fixture
async def client(settings, sessionmaker):
    app = FastAPI()
    install_crm(app, settings)
    app.state.sessionmaker = sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as client:
        token = csrf_from((await client.get("/login")).text)
        response = await client.post(
            "/login", data={"username": "demo", "password": "test-password", "csrf_token": token}
        )
        assert response.status_code == 303
        yield client


@pytest.fixture
async def csrf(client) -> str:
    return csrf_from((await client.get("/")).text)


async def make_lead(session, *, tags=(), **fields) -> Lead:
    fields.setdefault("source", Source.MANUAL)
    lead = await create_lead(session, **fields)
    for tag in tags:
        await add_tag(session, lead, tag)
    await session.commit()
    return lead


async def stored(sessionmaker, lead_id: int) -> Lead | None:
    async with sessionmaker() as fresh:
        return await get_lead(fresh, lead_id)


def lead_ids(html: str) -> set[int]:
    return {int(found) for found in re.findall(r'href="/leads/(\d+)"', html)}


async def test_manual_lead_gets_manual_tag_and_opens_card(client, csrf, sessionmaker):
    data = {"csrf_token": csrf, "name": " Анна ", "contact": "@anna", "request_text": "Нужен лендинг"}
    response = await client.post("/leads", data=data)
    assert response.status_code == 303
    lead_id = int(response.headers["location"].rsplit("/", 1)[1])

    lead = await stored(sessionmaker, lead_id)
    assert (lead.name, lead.contact, lead.request) == ("Анна", "@anna", "Нужен лендинг")
    assert lead.source == Source.MANUAL
    assert lead.form_step is None
    assert [tag.name for tag in lead.tags] == ["вручную"]

    card = await client.get(response.headers["location"])
    assert card.status_code == 200
    assert "Анна" in card.text
    assert "вручную" in card.text


async def test_manual_lead_tags_are_normalized_and_deduplicated(client, csrf, sessionmaker):
    data = {"csrf_token": csrf, "name": "Олег", "tags": "горячий, Горячий , ГОРЯЧИЙ,, ,"}
    response = await client.post("/leads", data=data)
    assert response.status_code == 303
    lead = await stored(sessionmaker, int(response.headers["location"].rsplit("/", 1)[1]))
    assert sorted(tag.name for tag in lead.tags) == ["вручную", "горячий"]


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"name": "   "}, "Укажите имя"),
        ({"name": "Олег", "tags": "ок, " + "х" * 65}, "Тег длиннее 64 символов"),
        ({"name": "я" * 65}, "Имя длиннее 64 символов"),
        ({"name": "Олег", "request_text": "з" * 1001}, "Запрос длиннее 1000 символов"),
    ],
)
async def test_bad_manual_lead_returns_form_with_error(client, csrf, sessionmaker, data, error):
    data = {"csrf_token": csrf, "contact": "+79991234567"} | data
    response = await client.post("/leads", data=data)
    assert response.status_code == 400
    assert error in response.text
    assert 'value="+79991234567"' in response.text
    async with sessionmaker() as fresh:
        assert await fresh.scalar(select(func.count()).select_from(Lead)) == 0


async def test_tag_filter_returns_only_matching_leads(client, session):
    hot = await make_lead(session, name="Горячий", tags=["горячий"])
    campaign = await make_lead(session, source=Source.BOT, name="Из кампании", tags=["кампания:camp_test"])
    other = await make_lead(session, name="Прочий")

    assert lead_ids((await client.get("/")).text) == {hot.id, campaign.id, other.id}
    assert lead_ids((await client.get("/?tag=горячий")).text) == {hot.id}
    assert lead_ids((await client.get("/?tag=" + quote("Горячий "))).text) == {hot.id}
    assert lead_ids((await client.get("/?tag=" + quote("кампания:camp_test"))).text) == {campaign.id}
    assert lead_ids((await client.get("/?tag=бот")).text) == {campaign.id}


async def test_tag_chip_links_to_its_filter(client, session):
    lead = await make_lead(session, name="Чип", tags=["кампания:camp_test"])
    page = (await client.get("/")).text
    href = "/?tag=%D0%BA%D0%B0%D0%BC%D0%BF%D0%B0%D0%BD%D0%B8%D1%8F%3Acamp_test"
    assert f'href="{href}"' in page
    assert lead_ids((await client.get(href)).text) == {lead.id}


async def test_status_and_source_filters(client, session):
    manual = await make_lead(session, name="Ручной")
    bot = await make_lead(session, source=Source.BOT, name="Бот", form_step=FormStep.DONE)
    await set_status(session, bot, Status.WON)
    await session.commit()

    assert lead_ids((await client.get("/?source=bot")).text) == {bot.id}
    assert lead_ids((await client.get("/?status=new")).text) == {manual.id}
    assert lead_ids((await client.get("/?status=won&source=manual")).text) == set()
    response = await client.get("/?status=nonsense&source=&tag=")
    assert response.status_code == 200
    assert lead_ids(response.text) == {manual.id, bot.id}


async def test_newest_lead_is_on_top(client, session):
    old = await make_lead(session, name="Старый")
    old.created_at = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()
    new = await make_lead(session, name="Новый")
    page = (await client.get("/")).text
    assert page.index(f"/leads/{new.id}") < page.index(f"/leads/{old.id}")


async def test_poll_fragment_keeps_filters(client, session):
    hot = await make_lead(session, name="Горячий", tags=["горячий"])
    await make_lead(session, name="Холодный", tags=["холодный"])

    page = (await client.get("/?tag=горячий&status=new")).text
    poll_url = re.search(r'hx-get="([^"]+)"', page).group(1).replace("&amp;", "&")
    assert poll_url.startswith("/leads/table?")
    assert 'hx-trigger="every 10s"' in page

    fragment = await client.get(poll_url, headers=HTMX)
    assert fragment.status_code == 200
    assert "<html" not in fragment.text
    assert lead_ids(fragment.text) == {hot.id}


async def test_empty_list_says_so(client, session):
    assert "Лидов пока нет" in (await client.get("/")).text
    await make_lead(session, name="Есть")
    assert "Под эти фильтры лидов нет" in (await client.get("/?tag=нет-такого")).text


async def test_add_and_remove_tag_without_htmx(client, csrf, session):
    lead = await make_lead(session, name="Теги")
    response = await client.post(f"/leads/{lead.id}/tags", data={"csrf_token": csrf, "tag": " VIP "})
    assert response.status_code == 303
    assert response.headers["location"] == f"/leads/{lead.id}"
    assert lead_ids((await client.get("/?tag=vip")).text) == {lead.id}

    response = await client.post(f"/leads/{lead.id}/tags/delete", data={"csrf_token": csrf, "name": "vip"})
    assert response.status_code == 303
    assert lead_ids((await client.get("/?tag=vip")).text) == set()


async def test_add_and_remove_tag_with_htmx_returns_fragment(client, csrf, session, sessionmaker):
    lead = await make_lead(session, name="Теги")
    response = await client.post(f"/leads/{lead.id}/tags", data={"csrf_token": csrf, "tag": "a, b"}, headers=HTMX)
    assert response.status_code == 200
    assert response.text.lstrip().startswith('<section id="tags">')
    assert sorted(tag.name for tag in (await stored(sessionmaker, lead.id)).tags) == ["a", "b", "вручную"]

    response = await client.post(
        f"/leads/{lead.id}/tags/delete", data={"csrf_token": csrf, "name": "вручную"}, headers=HTMX
    )
    assert response.status_code == 200
    assert "вручную" not in response.text
    assert lead_ids((await client.get("/?tag=вручную")).text) == set()


@pytest.mark.parametrize(
    ("raw", "error"),
    [("", "Введите тег"), (" , ", "Введите тег"), ("т" * 65, "Тег длиннее 64 символов")],
)
@pytest.mark.parametrize("htmx", [False, True])
async def test_bad_tag_shows_error_and_adds_nothing(client, csrf, session, sessionmaker, raw, error, htmx):
    lead = await make_lead(session, name="Ошибка")
    response = await client.post(
        f"/leads/{lead.id}/tags", data={"csrf_token": csrf, "tag": raw}, headers=HTMX if htmx else {}
    )
    assert response.status_code == (200 if htmx else 400)
    assert error in response.text
    assert [tag.name for tag in (await stored(sessionmaker, lead.id)).tags] == ["вручную"]


async def test_tag_of_64_chars_is_accepted(client, csrf, session, sessionmaker):
    lead = await make_lead(session, name="Длинный")
    response = await client.post(f"/leads/{lead.id}/tags", data={"csrf_token": csrf, "tag": "т" * 64})
    assert response.status_code == 303
    assert "т" * 64 in [tag.name for tag in (await stored(sessionmaker, lead.id)).tags]


async def test_status_change_from_new_removes_waiting_badge(client, csrf, session, sessionmaker):
    lead = await make_lead(session, name="Ждёт")
    assert "ждёт" in (await client.get("/")).text
    assert "ждёт ответа" in (await client.get(f"/leads/{lead.id}")).text

    response = await client.post(f"/leads/{lead.id}/status", data={"csrf_token": csrf, "status": "in_progress"})
    assert response.status_code == 303
    assert "ждёт" not in (await client.get("/")).text
    card = (await client.get(f"/leads/{lead.id}")).text
    assert "ждёт ответа" not in card
    assert "Первый ответ" in card
    assert (await stored(sessionmaker, lead.id)).status == Status.IN_PROGRESS


async def test_unknown_status_is_a_readable_error(client, csrf, session):
    lead = await make_lead(session, name="Статус")
    response = await client.post(f"/leads/{lead.id}/status", data={"csrf_token": csrf, "status": "deleted"})
    assert response.status_code == 400
    assert "Выберите статус из списка" in response.text


XSS_NAME = "<script>alert(1)</script>"
XSS_REQUEST = "<b>жирный</b>"


async def test_user_input_is_escaped_in_list_card_and_poll(client, session):
    lead = await make_lead(session, source=Source.BOT, name=XSS_NAME, request=XSS_REQUEST, contact='"><img src=x>')
    await add_tag(session, lead, "<i>тег</i>")
    await add_message(
        session,
        lead,
        direction=Direction.IN,
        channel=Channel.BOT,
        text=XSS_NAME,
        sent_at=datetime.now(UTC),
        tg_message_id=1,
    )
    await session.commit()

    for url in ("/", "/leads/table", f"/leads/{lead.id}", "/tags"):
        html = (await client.get(url)).text
        assert "<script>alert" not in html
        assert "<b>жирный" not in html
        assert "<i>тег" not in html
        assert "<img src=x>" not in html
    card = (await client.get(f"/leads/{lead.id}")).text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in card
    assert "&lt;b&gt;жирный&lt;/b&gt;" in card
    assert "&lt;b&gt;жирный&lt;/b&gt;" in (await client.get("/")).text


@pytest.mark.parametrize("path", ["/leads/999999", "/leads/99999999999999999999"])
async def test_missing_lead_is_russian_404(client, path):
    response = await client.get(path)
    assert response.status_code == 404
    assert "Лид не найден" in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize("path", ["/leads/abc", "/no-such-page"])
async def test_unknown_page_is_russian_404(client, path):
    response = await client.get(path)
    assert response.status_code == 404
    assert "Страница не найдена" in response.text


async def test_posting_to_missing_lead_is_404(client, csrf):
    response = await client.post("/leads/999999/tags", data={"csrf_token": csrf, "tag": "x"})
    assert response.status_code == 404
    assert "Лид не найден" in response.text


async def test_get_on_post_only_url_is_readable(client):
    response = await client.get("/logout")
    assert response.status_code == 405
    assert "<html" in response.text
    assert "Эта страница открывается только из формы." in response.text
    assert "Так нельзя" not in response.text


@pytest.mark.parametrize(("path", "location"), [("/leads", "/"), ("/leads?tag=x", "/?tag=x")])
async def test_leads_address_opens_the_list(client, path, location):
    response = await client.get(path)
    assert response.status_code == 303
    assert response.headers["location"] == location


@pytest.mark.parametrize(
    ("fields", "incomplete"),
    [
        ({"source": Source.BOT, "form_step": FormStep.NAME}, True),
        ({"source": Source.BOT, "form_step": FormStep.CANCELLED}, True),
        ({"source": Source.BOT, "form_step": FormStep.DONE}, False),
        ({"source": Source.TELEGRAM}, False),
        ({"source": Source.MANUAL}, False),
    ],
)
async def test_unfinished_bot_form_is_marked(client, session, fields, incomplete):
    lead = await make_lead(session, name="Анкета", **fields)
    assert ("анкета не завершена" in (await client.get("/")).text) is incomplete
    assert ("Анкета не завершена" in (await client.get(f"/leads/{lead.id}")).text) is incomplete


async def test_card_shows_moscow_time_and_message_history(client, session):
    lead = await make_lead(session, source=Source.TELEGRAM, name="Мария", campaign="autumn")
    lead.created_at = datetime(2026, 10, 1, 18, 30, tzinfo=UTC)
    await add_message(
        session,
        lead,
        direction=Direction.OUT,
        channel=Channel.BUSINESS,
        text="Добрый день!",
        sent_at=datetime(2026, 10, 1, 19, 5, tzinfo=UTC),
        tg_message_id=2,
    )
    await add_message(
        session,
        lead,
        direction=Direction.IN,
        channel=Channel.BUSINESS,
        text="Здравствуйте",
        sent_at=datetime(2026, 10, 1, 19, 0, tzinfo=UTC),
        tg_message_id=1,
    )
    await session.commit()

    card = (await client.get(f"/leads/{lead.id}")).text
    assert "01.10.2026 21:30 МСК" in card
    assert "autumn" in card
    assert card.index("Здравствуйте") < card.index("Добрый день!")
    assert "клиент, личка, 01.10.2026 22:00 МСК" in card
    assert "менеджер, личка, 01.10.2026 22:05 МСК" in card


async def test_tags_page_counts_and_links(client, session):
    await make_lead(session, name="Один", tags=["горячий"])
    await make_lead(session, name="Два", tags=["горячий"])
    page = (await client.get("/tags")).text
    hot = re.search(r'<a class="chip" href="([^"]+)">горячий</a></td><td>(\d+)</td>', page)
    assert hot.group(2) == "2"
    assert len(lead_ids((await client.get(hot.group(1).replace("&amp;", "&"))).text)) == 2


@pytest.mark.parametrize(
    ("age", "text"),
    [
        (timedelta(seconds=30), "0 мин"),
        (timedelta(minutes=59), "59 мин"),
        (timedelta(minutes=125), "2 ч 5 мин"),
        (timedelta(days=2, hours=3, minutes=10), "2 дн 3 ч"),
    ],
)
def test_waiting_text(age, text):
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    lead = Lead(status=Status.NEW, created_at=now - age, first_response_at=None)
    assert waiting(lead, now) == text


def test_no_waiting_after_first_response():
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert waiting(Lead(status=Status.NEW, created_at=now, first_response_at=now), now) is None
    assert waiting(Lead(status=Status.LOST, created_at=now, first_response_at=None), now) is None
