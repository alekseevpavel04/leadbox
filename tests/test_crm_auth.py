import re
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from leadbox.crm import install_crm
from leadbox.crm.auth import safe_next
from leadbox.models import Lead, Source
from leadbox.services.leads import create_lead

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "page has no CSRF field"
    return match.group(1)


def build_app(settings, sessionmaker) -> FastAPI:
    app = FastAPI()
    install_crm(app, settings)
    app.state.sessionmaker = sessionmaker
    return app


@pytest.fixture
async def client(settings, sessionmaker):
    transport = ASGITransport(app=build_app(settings, sessionmaker))
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        yield client


async def log_in(client: AsyncClient, password: str = "test-password"):
    token = csrf_from((await client.get("/login")).text)
    data = {"username": "demo", "password": password, "csrf_token": token}
    return await client.post("/login", data=data)


@pytest.fixture
async def csrf(client) -> str:
    assert (await log_in(client)).status_code == 303
    return csrf_from((await client.get("/")).text)


@pytest.fixture
async def lead(session) -> Lead:
    lead = await create_lead(session, source=Source.MANUAL, name="Иван")
    await session.commit()
    return lead


@pytest.mark.parametrize(
    ("path", "location"),
    [
        ("/", "/login"),
        ("/leads/new", "/login?next=%2Fleads%2Fnew"),
        ("/tags", "/login?next=%2Ftags"),
        ("/leads/1", "/login?next=%2Fleads%2F1"),
    ],
)
async def test_anonymous_get_redirects_to_login(client, path, location):
    response = await client.get(path)
    assert response.status_code == 303
    assert response.headers["location"] == location


async def test_shared_filter_link_survives_login(client):
    response = await client.get("/?tag=горячий")
    assert response.status_code == 303
    login_page = await client.get(response.headers["location"])
    token = csrf_from(login_page.text)
    next_value = re.search(r'name="next" value="([^"]+)"', login_page.text).group(1)
    data = {"username": "demo", "password": "test-password", "csrf_token": token, "next": next_value}
    response = await client.post("/login", data=data)
    assert response.status_code == 303
    assert unquote(response.headers["location"]) == "/?tag=горячий"


async def test_anonymous_htmx_poll_gets_401_with_redirect(client):
    headers = {"HX-Request": "true", "HX-Current-URL": "https://testserver/?tag=x"}
    response = await client.get("/leads/table", headers=headers)
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login?next=%2F%3Ftag%3Dx"


async def test_anonymous_post_redirects_to_login(client, lead):
    response = await client.post(f"/leads/{lead.id}/tags", data={"tag": "x"})
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_static_needs_no_login(client):
    response = await client.get("/static/htmx-2.0.11.min.js")
    assert response.status_code == 200
    assert "htmx" in response.text


@pytest.mark.parametrize(("username", "password"), [("demo", "wrong"), ("Demo", "test-password"), ("", "")])
async def test_wrong_credentials_do_not_let_in(client, username, password):
    token = csrf_from((await client.get("/login")).text)
    response = await client.post("/login", data={"username": username, "password": password, "csrf_token": token})
    assert response.status_code == 401
    assert "Неверный логин или пароль" in response.text
    assert (await client.get("/")).status_code == 303


async def test_cyrillic_password_does_not_crash(client):
    response = await log_in(client, password="пароль")
    assert response.status_code == 401


async def test_login_sets_hardened_cookie_and_opens_list(client):
    response = await log_in(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    page = await client.get("/")
    assert page.status_code == 200
    assert "<title>Leadbox</title>" in page.text


async def test_cookie_not_secure_on_plain_http(settings, sessionmaker):
    app = build_app(settings.model_copy(update={"public_base_url": "http://localhost:8000"}), sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await log_in(client)
    assert response.status_code == 303
    assert "secure" not in response.headers["set-cookie"].lower()


async def test_login_rotates_csrf_token(client):
    before = csrf_from((await client.get("/login")).text)
    await client.post("/login", data={"username": "demo", "password": "test-password", "csrf_token": before})
    after = csrf_from((await client.get("/")).text)
    assert after != before


async def test_logged_in_user_skips_login_page(client, csrf):
    response = await client.get("/login")
    assert response.status_code == 303
    assert response.headers["location"] == "/"


async def test_logout_ends_session(client, csrf):
    response = await client.post("/logout", data={"csrf_token": csrf})
    assert response.status_code == 303
    assert (await client.get("/")).status_code == 303


async def test_login_without_csrf_is_rejected(client):
    await client.get("/login")
    response = await client.post("/login", data={"username": "demo", "password": "test-password"})
    assert response.status_code == 403
    assert "Обновите страницу" in response.text


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/leads", {"name": "Пётр"}),
        ("/leads/{id}/status", {"status": "won"}),
        ("/leads/{id}/tags", {"tag": "чужой"}),
        ("/leads/{id}/tags/delete", {"name": "вручную"}),
        ("/logout", {}),
    ],
)
@pytest.mark.parametrize("token", [None, "forged-token"])
async def test_post_without_valid_csrf_gets_403(client, csrf, lead, sessionmaker, path, data, token):
    form = dict(data) | ({"csrf_token": token} if token else {})
    response = await client.post(path.format(id=lead.id), data=form)
    assert response.status_code == 403
    assert "HX-Refresh" not in response.headers

    async with sessionmaker() as fresh:
        assert await fresh.scalar(select(func.count()).select_from(Lead)) == 1
        stored = await fresh.scalar(select(Lead).where(Lead.id == lead.id))
        await fresh.refresh(stored, ["tags"])
        assert stored.status == "new"
        assert [tag.name for tag in stored.tags] == ["вручную"]
    assert (await client.get("/")).status_code == 200


async def test_htmx_post_with_csrf_header_passes(client, csrf, lead):
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf}
    response = await client.post(f"/leads/{lead.id}/tags", data={"tag": "тёплый"}, headers=headers)
    assert response.status_code == 200
    assert "тёплый" in response.text


async def test_htmx_post_with_wrong_csrf_header_gets_403(client, csrf, lead):
    headers = {"HX-Request": "true", "X-CSRF-Token": "forged"}
    # A valid form token does not rescue a wrong header: the header wins when present.
    response = await client.post(f"/leads/{lead.id}/tags", data={"tag": "x", "csrf_token": csrf}, headers=headers)
    assert response.status_code == 403


async def test_htmx_post_with_stale_token_asks_for_reload(client, csrf, lead, sessionmaker):
    # Logging out and in again in another tab rotates the token; the open card still sends the old one.
    await client.post("/logout", data={"csrf_token": csrf})
    assert (await log_in(client)).status_code == 303
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf}
    response = await client.post(f"/leads/{lead.id}/tags", data={"tag": "x"}, headers=headers)
    assert response.status_code == 403
    assert response.headers["HX-Refresh"] == "true"
    async with sessionmaker() as fresh:
        stored = await fresh.scalar(select(Lead).where(Lead.id == lead.id))
        await fresh.refresh(stored, ["tags"])
        assert [tag.name for tag in stored.tags] == ["вручную"]


async def test_pages_carry_csrf_header_for_htmx(client, csrf):
    page = await client.get("/")
    assert f'hx-headers=\'{{"X-CSRF-Token": "{csrf}"}}\'' in page.text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/?tag=горячий", "/?tag=горячий"),
        ("/leads/5", "/leads/5"),
        (None, "/"),
        ("", "/"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("/\t/evil.example", "/"),
        ("leads", "/"),
    ],
)
def test_safe_next_stays_on_site(raw, expected):
    assert safe_next(raw) == expected


@pytest.mark.parametrize("field", ["session_secret", "crm_user", "crm_password"])
def test_install_refuses_empty_secrets(settings, field):
    with pytest.raises(RuntimeError, match=field.upper()):
        install_crm(FastAPI(), settings.model_copy(update={field: ""}))


def test_templates_do_not_disable_escaping():
    crm = Path(__file__).resolve().parents[1] / "src" / "leadbox" / "crm"
    for path in [*crm.glob("templates/*.html"), *crm.glob("*.py")]:
        text = path.read_text(encoding="utf-8")
        for marker in ("|safe", "| safe", "Markup", "autoescape"):
            assert marker not in text, f"{marker} in {path.name}"
