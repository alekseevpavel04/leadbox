from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from leadbox.crm.auth import (
    check_credentials,
    current_user,
    is_htmx,
    log_in,
    log_out,
    require_csrf,
    require_user,
    safe_next,
)
from leadbox.crm.templating import render
from leadbox.db import get_session
from leadbox.models import Lead, Source, Status
from leadbox.services.leads import create_lead, get_lead, list_leads, set_status
from leadbox.services.tags import add_tag, canonical_tag, list_tags_with_counts, normalize_tag, remove_tag

# Name and request limits match the bot form (SPEC 6), so a lead looks the same whichever way it came in.
# The bot validates contacts by format; a manual contact is free text, capped only to keep the table readable.
NAME_MAX_LEN = 64
CONTACT_MAX_LEN = 100
REQUEST_MAX_LEN = 1000
# leads.id is a 32-bit integer on Postgres; a larger id from the URL would fail in the driver, not 404.
MAX_LEAD_ID = 2**31 - 1

E = TypeVar("E", bound=StrEnum)

Session = Annotated[AsyncSession, Depends(get_session)]
FormText = Annotated[str, Form()]

public = APIRouter()
router = APIRouter(dependencies=[Depends(require_user)])
csrf = [Depends(require_csrf)]


class LeadNotFound(Exception):
    def __init__(self, lead_id: int) -> None:
        super().__init__(lead_id)
        self.lead_id = lead_id


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@public.get("/login")
async def login_page(request: Request, next: str = "/") -> Response:
    if current_user(request):
        return redirect(safe_next(next))
    return render(request, "login.html", {"next": safe_next(next)})


@public.post("/login", dependencies=csrf)
async def login_submit(
    request: Request, username: FormText = "", password: FormText = "", next: FormText = "/"
) -> Response:
    if check_credentials(request.app.state.crm_settings, username, password):
        log_in(request, username)
        return redirect(safe_next(next))
    context = {"next": safe_next(next), "username": username, "error": "Неверный логин или пароль"}
    return render(request, "login.html", context, status_code=401)


@router.post("/logout", dependencies=csrf)
async def logout(request: Request) -> Response:
    log_out(request)
    return redirect("/login")


def parse_enum(enum_cls: type[E], value: str) -> E | None:
    return enum_cls(value) if value in {member.value for member in enum_cls} else None


@dataclass(frozen=True)
class Filters:
    tag: str
    status: Status | None
    source: Source | None

    @classmethod
    def parse(cls, tag: str, status: str, source: str) -> "Filters":
        # Unknown values are dropped rather than rejected: a mangled link still opens the list.
        return cls(
            tag=canonical_tag(tag),
            status=parse_enum(Status, status),
            source=parse_enum(Source, source),
        )

    @property
    def active(self) -> bool:
        return bool(self.tag or self.status or self.source)


async def _list_context(session: AsyncSession, request: Request, filters: Filters) -> dict[str, object]:
    leads = await list_leads(session, tag=filters.tag or None, status=filters.status, source=filters.source)
    query = request.url.query
    return {
        "leads": leads,
        "filters": filters,
        "table_url": "/leads/table" + (f"?{query}" if query else ""),
    }


@router.get("/")
async def leads_page(request: Request, session: Session, tag: str = "", status: str = "", source: str = "") -> Response:
    context = await _list_context(session, request, Filters.parse(tag, status, source))
    context["tag_options"] = [name for name, _ in await list_tags_with_counts(session)]
    return render(request, "leads.html", context)


# Polled by the list page every 10 seconds with the page's own query string.
@router.get("/leads/table")
async def leads_table(
    request: Request, session: Session, tag: str = "", status: str = "", source: str = ""
) -> Response:
    context = await _list_context(session, request, Filters.parse(tag, status, source))
    return render(request, "_leads_table.html", context)


def parse_tags(raw: str) -> list[str]:
    """Comma-separated tags, normalized and deduplicated; empty pieces between commas are skipped.

    Raises ValueError with a user-facing text if any tag is invalid, so nothing is saved half-way.
    """
    names: list[str] = []
    for piece in raw.split(","):
        if not piece.strip():
            continue
        try:
            name = normalize_tag(piece)
        except ValueError as exc:
            raise ValueError(f"{exc}: «{piece.strip()[:20]}...»") from exc
        if name not in names:
            names.append(name)
    return names


@router.get("/leads/new")
async def new_lead_page(request: Request) -> Response:
    return render(request, "lead_new.html", {"form": {}, "errors": []})


@router.post("/leads", dependencies=csrf)
async def new_lead_submit(
    request: Request,
    session: Session,
    name: FormText = "",
    contact: FormText = "",
    request_text: FormText = "",
    tags: FormText = "",
) -> Response:
    name, contact, request_text = name.strip(), contact.strip(), request_text.strip()
    errors = []
    if not name:
        errors.append("Укажите имя")
    elif len(name) > NAME_MAX_LEN:
        errors.append(f"Имя длиннее {NAME_MAX_LEN} символов")
    if len(contact) > CONTACT_MAX_LEN:
        errors.append(f"Контакт длиннее {CONTACT_MAX_LEN} символов")
    if len(request_text) > REQUEST_MAX_LEN:
        errors.append(f"Запрос длиннее {REQUEST_MAX_LEN} символов")
    try:
        tag_names = parse_tags(tags)
    except ValueError as exc:
        errors.append(str(exc))
    if errors:
        form = {"name": name, "contact": contact, "request_text": request_text, "tags": tags}
        return render(request, "lead_new.html", {"form": form, "errors": errors}, status_code=400)

    lead = await create_lead(
        session, source=Source.MANUAL, name=name, contact=contact or None, request=request_text or None
    )
    for tag_name in tag_names:
        await add_tag(session, lead, tag_name)
    await session.commit()
    return redirect(f"/leads/{lead.id}")


async def load_lead(session: AsyncSession, lead_id: int) -> Lead:
    lead = await get_lead(session, lead_id) if lead_id <= MAX_LEAD_ID else None
    if lead is None:
        raise LeadNotFound(lead_id)
    return lead


@router.get("/leads/{lead_id:int}")
async def lead_page(request: Request, session: Session, lead_id: int) -> Response:
    return render(request, "lead.html", {"lead": await load_lead(session, lead_id)})


@router.post("/leads/{lead_id:int}/status", dependencies=csrf)
async def lead_status(request: Request, session: Session, lead_id: int, status: FormText = "") -> Response:
    lead = await load_lead(session, lead_id)
    new_status = parse_enum(Status, status)
    if new_status is None:
        return render(
            request, "lead.html", {"lead": lead, "status_error": "Выберите статус из списка"}, status_code=400
        )
    await set_status(session, lead, new_status)
    await session.commit()
    return redirect(f"/leads/{lead.id}")


def _tags_response(request: Request, lead: Lead, *, error: str | None = None, value: str = "") -> Response:
    # htmx 2 does not swap 4xx responses by default, so its error comes back as 200 with the message inside.
    if is_htmx(request):
        context = {"lead": lead, "tag_error": error, "tag_value": value, "refocus": True}
        return render(request, "_tags.html", context)
    if error:
        return render(request, "lead.html", {"lead": lead, "tag_error": error, "tag_value": value}, status_code=400)
    return redirect(f"/leads/{lead.id}")


@router.post("/leads/{lead_id:int}/tags", dependencies=csrf)
async def lead_add_tags(request: Request, session: Session, lead_id: int, tag: FormText = "") -> Response:
    lead = await load_lead(session, lead_id)
    try:
        names = parse_tags(tag)
    except ValueError as exc:
        return _tags_response(request, lead, error=str(exc), value=tag)
    if not names:
        return _tags_response(request, lead, error="Введите тег", value=tag)
    for name in names:
        await add_tag(session, lead, name)
    await session.commit()
    return _tags_response(request, lead)


@router.post("/leads/{lead_id:int}/tags/delete", dependencies=csrf)
async def lead_remove_tag(request: Request, session: Session, lead_id: int, name: FormText = "") -> Response:
    lead = await load_lead(session, lead_id)
    await remove_tag(session, lead, name)
    await session.commit()
    return _tags_response(request, lead)


@router.get("/tags")
async def tags_page(request: Request, session: Session) -> Response:
    return render(request, "tags.html", {"tags": await list_tags_with_counts(session)})
