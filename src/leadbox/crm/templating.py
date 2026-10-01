from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from leadbox.crm.auth import csrf_token, current_user
from leadbox.models import SOURCE_LABELS, Channel, Direction, FormStep, Lead, Source, Status, utcnow

# Moscow has had no DST since 2014. A fixed offset also spares Windows the tzdata package.
MSK = timezone(timedelta(hours=3), "MSK")

STATUS_LABELS = {Status.NEW: "новый", Status.IN_PROGRESS: "в работе", Status.WON: "успех", Status.LOST: "отказ"}
CHANNEL_LABELS = {Channel.BOT: "бот", Channel.BUSINESS: "Telegram"}
DIRECTION_LABELS = {Direction.IN: "клиент", Direction.OUT: "менеджер"}


def msk(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def waiting(lead: Lead, now: datetime | None = None) -> str | None:
    """Text for the "waiting" badge, or None once someone has answered."""
    if lead.status != Status.NEW or lead.first_response_at is not None:
        return None
    minutes = max(0, int(((now or utcnow()) - lead.created_at).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин"
    days, hours = divmod(hours, 24)
    return f"{days} дн {hours} ч"


def form_incomplete(lead: Lead) -> bool:
    return lead.source == Source.BOT and lead.form_step != FormStep.DONE


def tag_url(name: str) -> str:
    return "/?" + urlencode({"tag": name})


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["msk"] = msk
templates.env.globals.update(
    waiting=waiting,
    form_incomplete=form_incomplete,
    tag_url=tag_url,
    source_labels=SOURCE_LABELS,
    status_labels=STATUS_LABELS,
    channel_labels=CHANNEL_LABELS,
    direction_labels=DIRECTION_LABELS,
)


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    base = {"user": current_user(request), "csrf_token": csrf_token(request)}
    return templates.TemplateResponse(request, name, base | (context or {}), status_code=status_code, headers=headers)


def render_without_session(request: Request, name: str, context: dict[str, Any], *, status_code: int) -> Response:
    # For the 500 page: it is rendered outside SessionMiddleware, where request.session does not exist.
    return templates.TemplateResponse(
        request, name, {"user": None, "csrf_token": None} | context, status_code=status_code
    )
