from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from leadbox.config import Settings
from leadbox.crm.auth import CsrfFailed, LoginRequired, is_htmx
from leadbox.crm.routes import LeadNotFound, public, router
from leadbox.crm.templating import render, render_without_session

STATIC_DIR = Path(__file__).parent / "static"


def install_crm(app: FastAPI, settings: Settings) -> None:
    """Session cookie, /static, CRM routes and Russian error pages."""
    missing = [name.upper() for name in ("session_secret", "crm_user", "crm_password") if not getattr(settings, name)]
    if missing:
        # An empty secret signs cookies with "", and an empty password would let anyone in.
        raise RuntimeError(f"CRM needs these settings: {', '.join(missing)}")
    app.state.crm_settings = settings
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="leadbox_session",
        same_site="lax",
        https_only=settings.public_base_url.startswith("https://"),
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(public)
    app.include_router(router)
    app.add_exception_handler(LoginRequired, _login_required)
    app.add_exception_handler(CsrfFailed, _csrf_failed)
    app.add_exception_handler(LeadNotFound, _lead_not_found)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _server_error)


def _login_url(path_and_query: str) -> str:
    return "/login" if path_and_query == "/" else "/login?" + urlencode({"next": path_and_query})


async def _login_required(request: Request, _exc: Exception) -> Response:
    if is_htmx(request):
        # A 302 would be followed by the XHR and the login form swapped into the table. 401 plus
        # HX-Redirect makes htmx load the login page as a whole, back to the page that was open.
        current = urlsplit(request.headers.get("HX-Current-URL", "/"))
        page = current.path + (f"?{current.query}" if current.query else "")
        return Response(status_code=401, headers={"HX-Redirect": _login_url(page)})
    if request.method == "GET":
        page = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(_login_url(page), status_code=303)
    return RedirectResponse("/login", status_code=303)


async def _csrf_failed(request: Request, _exc: Exception) -> Response:
    if is_htmx(request):
        # htmx 2 does not swap a 403, so the click would do nothing. The token is stale (a new login in
        # another tab rotates it), and a reload brings the current one, or the login page.
        return Response(status_code=403, headers={"HX-Refresh": "true"})
    message = "Форма устарела. Обновите страницу и повторите."
    return render(request, "error.html", {"title": "Запрос отклонён", "message": message}, status_code=403)


async def _lead_not_found(request: Request, exc: LeadNotFound) -> Response:
    message = f"Лида с номером {exc.lead_id} нет. Возможно, ссылка неточная."
    return render(request, "error.html", {"title": "Лид не найден", "message": message}, status_code=404)


async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.status_code == 404:
        context = {"title": "Страница не найдена", "message": "Такой страницы нет. Проверьте адрес."}
    elif exc.status_code == 405:
        context = {"title": "Страница для формы", "message": "Эта страница открывается только из формы."}
    else:
        return await http_exception_handler(request, exc)
    return render(request, "error.html", context, status_code=exc.status_code)


async def _server_error(request: Request, exc: Exception) -> Response:
    # Starlette logs the traceback itself after this handler; the browser gets no details.
    context = {"title": "Ошибка на сервере", "message": "Что-то сломалось. Обновите страницу через минуту."}
    return render_without_session(request, "error.html", context, status_code=500)
