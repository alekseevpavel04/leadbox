import secrets
from urllib.parse import urlsplit

from fastapi import Request

from leadbox.config import Settings

USER_KEY = "user"
CSRF_KEY = "csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"


class LoginRequired(Exception):
    pass


class CsrfFailed(Exception):
    pass


def _same(a: str, b: str) -> bool:
    # compare_digest refuses non-ASCII str, and a password may well be Cyrillic.
    return secrets.compare_digest(a.encode(), b.encode())


def check_credentials(settings: Settings, username: str, password: str) -> bool:
    # Both comparisons always run, so the response time does not tell a wrong login from a wrong password.
    user_ok = _same(username, settings.crm_user)
    password_ok = _same(password, settings.crm_password)
    return user_ok and password_ok


def current_user(request: Request) -> str | None:
    return request.session.get(USER_KEY)


def csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_KEY] = token
    return token


def log_in(request: Request, username: str) -> None:
    # A new session and a new CSRF token: nothing issued before login survives it.
    request.session.clear()
    request.session[USER_KEY] = username
    csrf_token(request)


def log_out(request: Request) -> None:
    request.session.clear()


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def safe_next(raw: str | None) -> str:
    """Where to go after login: only a path on this site, never another host."""
    if not raw or not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    # Browsers drop tabs and newlines from URLs, so "/\t/evil.com" would become "//evil.com".
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        return "/"
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return "/"
    return raw


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise LoginRequired
    return user


async def require_csrf(request: Request) -> None:
    sent = request.headers.get(CSRF_HEADER)
    if sent is None:
        sent = (await request.form()).get(CSRF_FIELD)
    expected = request.session.get(CSRF_KEY)
    if not expected or not isinstance(sent, str) or not _same(sent, expected):
        raise CsrfFailed
