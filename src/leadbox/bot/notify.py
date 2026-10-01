import logging
from html import escape

from aiogram import Bot
from aiogram.types import LinkPreviewOptions

from leadbox.models import SOURCE_LABELS, Lead

logger = logging.getLogger(__name__)

REQUEST_PREVIEW_LEN = 300


def lead_notification(lead: Lead, public_base_url: str) -> str:
    """HTML for the managers' group. Every user-supplied value goes through html.escape."""
    request = lead.request or ""
    # Cut before escaping, so the cut can never land inside an entity like "&lt;".
    if len(request) > REQUEST_PREVIEW_LEN:
        request = request[:REQUEST_PREVIEW_LEN].rstrip() + "…"
    url = f"{public_base_url.rstrip('/')}/leads/{lead.id}"
    lines = [
        f"<b>Новый лид: {SOURCE_LABELS[lead.source]}</b>",
        f"Имя: {escape(lead.name or '-')}",
        f"Контакт: {escape(lead.contact or '-')}",
        f"Запрос: {escape(request or '-')}",
        f"Теги: {escape(', '.join(tag.name for tag in lead.tags) or '-')}",
        f'<a href="{escape(url)}">Открыть в CRM</a>',
    ]
    return "\n".join(lines)


async def send_notification(bot: Bot, chat_id: int | None, text: str) -> bool:
    if chat_id is None:
        logger.warning("MANAGER_CHAT_ID is not set, lead notification skipped")
        return False
    # Called after commit: the lead is saved and must not depend on the group being reachable,
    # so any failure is logged and dropped. Raising would only lose the notifications queued after it.
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception:
        logger.exception("failed to send lead notification to chat %s", chat_id)
        return False
    return True
