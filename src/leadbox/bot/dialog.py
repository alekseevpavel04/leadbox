import re

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.methods import SendMessage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession

from leadbox.bot.contact import normalize_contact, shared_phone
from leadbox.bot.transaction import Outbox
from leadbox.config import Settings
from leadbox.models import NAME_MAX_LEN, REQUEST_MAX_LEN, Channel, Direction, FormStep, Lead, Source
from leadbox.services.leads import SOURCE_TAGS, add_message, create_lead, get_open_lead_by_tg, update_lead_fields
from leadbox.services.tags import add_tag

REQUEST_HINT_LEN = 200

GREETING = (
    "Здравствуйте! Здесь можно оставить заявку агентству: три коротких вопроса, около минуты.\n"
    "Отправляя данные, вы соглашаетесь на их обработку для ответа на заявку."
)
ASK_NAME = "Как к вам обращаться? Напишите имя или нажмите кнопку."
ASK_CONTACT = "Как с вами связаться? Нажмите «Поделиться номером» или напишите телефон, @username или email."
ASK_REQUEST = "Что нужно? Опишите задачу в паре предложений."
CURRENT_VALUE = "Сейчас: {value}"
CONFIRM_SUMMARY = "Проверьте заявку:\n\nИмя: {name}\nКонтакт: {contact}\nЗапрос: {request}"
CONFIRM_HINT = "Чтобы закончить, нажмите «Отправить» или «Исправить»."
CONTACT_SAVED = "Записали контакт: {contact}"
DONE = "Заявка принята, менеджер ответит в рабочее время."
AFTER_DONE = "Мы получили, менеджер ответит."
ALREADY_DONE = "Заявка уже у менеджера, он ответит. Если хотите что-то добавить, напишите здесь."
RESUME = "Продолжим с того места, где остановились."
CANCELLED = "Хорошо, остановились. Если передумаете, нажмите /start."
CANCELLED_HINT = "Анкета остановлена. Чтобы продолжить, нажмите /start."
CANCEL_AFTER_DONE = "Заявка уже у менеджера. Если она больше не нужна, напишите об этом здесь."
NOTHING_TO_CANCEL = "Отменять нечего. Чтобы оставить заявку, нажмите /start."
NO_FORM = "Чтобы оставить заявку, нажмите /start."
TEXT_ONLY = "Пришлите, пожалуйста, текстом."
STALE_BUTTON = "Эта кнопка уже не действует."
UNKNOWN_COMMAND = "Такой команды нет."
NAME_TOO_LONG = f"Имя длинновато: уложитесь, пожалуйста, в {NAME_MAX_LEN} символа."
REQUEST_TOO_LONG = (
    f"Текст длиннее {REQUEST_MAX_LEN} символов (сейчас {{length}}). "
    "Сократите, пожалуйста, подробности можно обсудить с менеджером."
)
CONTACT_INVALID = "Не получилось распознать контакт. Напишите, например, +7 999 123-45-67, @username или name@mail.ru."
CONTACT_FOREIGN = "Это чужой контакт. Нажмите «Поделиться номером», чтобы отправить свой, или напишите контакт текстом."

BUTTON_PROFILE_NAME = "Да, так и зовите ({name})"
BUTTON_KEEP = "Оставить как есть"
BUTTON_SHARE_PHONE = "Поделиться номером"
BUTTON_SEND = "Отправить"
BUTTON_EDIT = "Исправить"

CB_PROFILE_NAME = "name:profile"
CB_KEEP_NAME = "keep:name"
CB_KEEP_REQUEST = "keep:request"
CB_SEND = "confirm:send"
CB_EDIT = "confirm:edit"

# Deep link alphabet; 55, not 64, so that the tag "кампания:<payload>" fits the 64-character tag limit.
PAYLOAD = re.compile(r"[A-Za-z0-9_-]{1,55}")
COMMAND = re.compile(r"/[A-Za-z0-9_]{1,32}(@\w+)?(\s|$)")

Markup = InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | None


def build_router() -> Router:
    # A fresh router per dispatcher: aiogram attaches a router to one parent only.
    router = Router(name="dialog")
    # The bot also sits in the managers' group; nothing said there is a lead.
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(on_start, Command("start"))
    router.message.register(on_cancel, Command("cancel"))
    router.message.register(on_message)
    router.callback_query.register(on_callback)
    return router


async def on_start(message: Message, command: CommandObject, session: AsyncSession, outbox: Outbox) -> None:
    user = message.from_user
    campaign = command.args if command.args and PAYLOAD.fullmatch(command.args) else None
    lead = await _open_lead(session, user)
    if lead is None:
        lead = await _new_lead(session, user, campaign)
        _send(outbox, user.id, *_prompt(lead, user, GREETING))
        return
    if campaign:
        await _attach_campaign(session, lead, campaign)
    prefix: str | None = RESUME
    if lead.form_step is None:
        await _join_form(session, lead)
        prefix = GREETING
    elif lead.form_step == FormStep.CANCELLED:
        await update_lead_fields(session, lead, form_step=_resume_step(lead))
    elif lead.form_step == FormStep.DONE:
        prefix = None
    _send(outbox, user.id, *_prompt(lead, user, prefix))


async def on_cancel(message: Message, session: AsyncSession, outbox: Outbox) -> None:
    user = message.from_user
    lead = await _open_lead(session, user)
    if lead is None or lead.form_step is None:
        _send(outbox, user.id, NOTHING_TO_CANCEL)
    elif lead.form_step == FormStep.DONE:
        _send(outbox, user.id, CANCEL_AFTER_DONE)
    else:
        await update_lead_fields(session, lead, form_step=FormStep.CANCELLED)
        _send(outbox, user.id, CANCELLED, ReplyKeyboardRemove())


async def on_message(message: Message, session: AsyncSession, outbox: Outbox) -> None:
    user = message.from_user
    is_command = message.text is not None and COMMAND.match(message.text) is not None
    lead = await _open_lead(session, user)
    if lead is None:
        # Whatever comes first, a sticker or "hello", starts a lead: a person who wrote is a lead.
        lead = await _new_lead(session, user, None)
        if not is_command:
            await _record(session, lead, message)
        _send(outbox, user.id, *_prompt(lead, user, GREETING))
        return
    if is_command:
        _send(outbox, user.id, *_prompt(lead, user, UNKNOWN_COMMAND))
        return
    await _record(session, lead, message)

    step = lead.form_step
    if step is None:
        await _join_form(session, lead)
        _send(outbox, user.id, *_prompt(lead, user, GREETING))
    elif step == FormStep.CANCELLED:
        _send(outbox, user.id, CANCELLED_HINT)
    elif step == FormStep.DONE:
        if message.text is None:
            _send(outbox, user.id, *_prompt(lead, user, TEXT_ONLY))
        else:
            _send(outbox, user.id, AFTER_DONE)
    elif step == FormStep.NAME:
        await _on_name(session, outbox, lead, message)
    elif step == FormStep.CONTACT:
        await _on_contact(session, outbox, lead, message)
    elif step == FormStep.REQUEST:
        await _on_request(session, outbox, lead, message)
    else:
        _send(outbox, user.id, *_prompt(lead, user, CONFIRM_HINT))


async def on_callback(callback: CallbackQuery, session: AsyncSession, outbox: Outbox, settings: Settings) -> None:
    # Answered whatever the button, or the person's client spins until Telegram's timeout.
    outbox.replies.append(callback.answer())
    if isinstance(callback.message, Message) and callback.message.reply_markup is not None:
        outbox.replies.append(callback.message.edit_reply_markup(reply_markup=None))

    user = callback.from_user
    lead = await _open_lead(session, user)
    step = lead.form_step if lead is not None else None
    data = callback.data
    if lead is None:
        _send(outbox, user.id, STALE_BUTTON + "\n\n" + NO_FORM)
    elif step == FormStep.NAME and data == CB_PROFILE_NAME:
        await _accept_name(session, outbox, lead, user, _profile_name(user))
    elif step == FormStep.NAME and data == CB_KEEP_NAME and lead.name:
        await _accept_name(session, outbox, lead, user, lead.name)
    elif step == FormStep.REQUEST and data == CB_KEEP_REQUEST and lead.request:
        await _accept_request(session, outbox, lead, user, lead.request)
    elif step == FormStep.CONFIRM and data == CB_SEND:
        await update_lead_fields(session, lead, form_step=FormStep.DONE)
        outbox.notify(lead, settings.public_base_url)
        _send(outbox, user.id, DONE)
    elif step == FormStep.CONFIRM and data == CB_EDIT:
        await update_lead_fields(session, lead, form_step=FormStep.NAME)
        _send(outbox, user.id, *_prompt(lead, user))
    else:
        _send(outbox, user.id, *_prompt(lead, user, STALE_BUTTON))


async def _on_name(session: AsyncSession, outbox: Outbox, lead: Lead, message: Message) -> None:
    user = message.from_user
    if message.text is None:
        _send(outbox, user.id, *_prompt(lead, user, TEXT_ONLY))
        return
    name = _clean_name(message.text)
    if len(name) > NAME_MAX_LEN:
        _send(outbox, user.id, *_prompt(lead, user, NAME_TOO_LONG))
        return
    await _accept_name(session, outbox, lead, user, name)


async def _accept_name(session: AsyncSession, outbox: Outbox, lead: Lead, user: User, name: str) -> None:
    await update_lead_fields(session, lead, name=name, form_step=FormStep.CONTACT)
    _send(outbox, user.id, *_prompt(lead, user))


async def _on_contact(session: AsyncSession, outbox: Outbox, lead: Lead, message: Message) -> None:
    user = message.from_user
    if message.contact is not None:
        # A contact card can be anyone's from the phone book; only the sender's own number counts.
        if message.contact.user_id != user.id:
            _send(outbox, user.id, *_prompt(lead, user, CONTACT_FOREIGN))
            return
        contact = shared_phone(message.contact.phone_number)
    elif message.text is None:
        _send(outbox, user.id, *_prompt(lead, user, TEXT_ONLY))
        return
    elif message.text == BUTTON_KEEP and lead.contact:
        contact = lead.contact
    else:
        contact = normalize_contact(message.text)
        if contact is None:
            _send(outbox, user.id, *_prompt(lead, user, CONTACT_INVALID))
            return
    await update_lead_fields(session, lead, contact=contact, form_step=FormStep.REQUEST)
    # A separate message, because only it can take the phone keyboard away: the next prompt may
    # carry inline buttons, and a message has room for one keyboard.
    _send(outbox, user.id, CONTACT_SAVED.format(contact=contact), ReplyKeyboardRemove())
    _send(outbox, user.id, *_prompt(lead, user))


async def _on_request(session: AsyncSession, outbox: Outbox, lead: Lead, message: Message) -> None:
    user = message.from_user
    if message.text is None:
        _send(outbox, user.id, *_prompt(lead, user, TEXT_ONLY))
        return
    request = message.text.strip()
    if len(request) > REQUEST_MAX_LEN:
        _send(outbox, user.id, *_prompt(lead, user, REQUEST_TOO_LONG.format(length=len(request))))
        return
    await _accept_request(session, outbox, lead, user, request)


async def _accept_request(session: AsyncSession, outbox: Outbox, lead: Lead, user: User, request: str) -> None:
    await update_lead_fields(session, lead, request=request, form_step=FormStep.CONFIRM)
    _send(outbox, user.id, *_prompt(lead, user))


def _prompt(lead: Lead, user: User, prefix: str | None = None) -> tuple[str, Markup]:
    """What to say for the lead's current step; a re-ask after any odd input is this same text."""
    step = lead.form_step
    markup: Markup = None
    if step == FormStep.NAME:
        text = _with_current(ASK_NAME, lead.name)
        markup = _name_keyboard(lead, user)
    elif step == FormStep.CONTACT:
        text = _with_current(ASK_CONTACT, lead.contact)
        markup = _contact_keyboard(lead)
    elif step == FormStep.REQUEST:
        text = _with_current(ASK_REQUEST, _shorten(lead.request, REQUEST_HINT_LEN))
        if lead.request:
            markup = _inline([(BUTTON_KEEP, CB_KEEP_REQUEST)])
    elif step == FormStep.CONFIRM:
        text = CONFIRM_SUMMARY.format(name=lead.name, contact=lead.contact, request=lead.request)
        markup = _inline([(BUTTON_SEND, CB_SEND), (BUTTON_EDIT, CB_EDIT)])
    elif step == FormStep.DONE:
        text = ALREADY_DONE
    elif step == FormStep.CANCELLED:
        text = CANCELLED_HINT
    else:
        text = NO_FORM
    return (f"{prefix}\n\n{text}" if prefix else text), markup


def _name_keyboard(lead: Lead, user: User) -> InlineKeyboardMarkup:
    profile_name = _profile_name(user)
    buttons = [(BUTTON_PROFILE_NAME.format(name=profile_name), CB_PROFILE_NAME)]
    if lead.name and lead.name != profile_name:
        buttons.append((BUTTON_KEEP, CB_KEEP_NAME))
    return _inline(buttons)


def _contact_keyboard(lead: Lead) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BUTTON_SHARE_PHONE, request_contact=True)]]
    if lead.contact:
        rows.append([KeyboardButton(text=BUTTON_KEEP)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _inline(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data)] for text, data in buttons]
    )


def _send(outbox: Outbox, chat_id: int, text: str, markup: Markup = None) -> None:
    outbox.replies.append(SendMessage(chat_id=chat_id, text=text, reply_markup=markup))


async def _open_lead(session: AsyncSession, user: User) -> Lead | None:
    lead = await get_open_lead_by_tg(session, user.id)
    # People change usernames; the CRM should show the one that works now.
    if lead is not None and lead.tg_username != user.username:
        await update_lead_fields(session, lead, tg_username=user.username)
    return lead


async def _new_lead(session: AsyncSession, user: User, campaign: str | None) -> Lead:
    lead = await create_lead(
        session,
        source=Source.BOT,
        campaign=campaign,
        tg_user_id=user.id,
        tg_username=user.username,
        form_step=FormStep.NAME,
    )
    if campaign:
        await add_tag(session, lead, f"кампания:{campaign}")
    return lead


async def _attach_campaign(session: AsyncSession, lead: Lead, campaign: str) -> None:
    # The field keeps the first campaign that brought the person; every later one is still a tag.
    if lead.campaign is None:
        await update_lead_fields(session, lead, campaign=campaign)
    await add_tag(session, lead, f"кампания:{campaign}")


async def _join_form(session: AsyncSession, lead: Lead) -> None:
    """An open lead that did not come from the form (Business chat) gets the form from its first gap."""
    await add_tag(session, lead, SOURCE_TAGS[Source.BOT])
    await update_lead_fields(session, lead, form_step=_resume_step(lead))


def _resume_step(lead: Lead) -> FormStep:
    # The step before /cancel is not stored; the first empty field is the same step in the normal
    # forward pass, and with everything filled the summary lets the person check and send.
    if not lead.name:
        return FormStep.NAME
    if not lead.contact:
        return FormStep.CONTACT
    if not lead.request:
        return FormStep.REQUEST
    return FormStep.CONFIRM


async def _record(session: AsyncSession, lead: Lead, message: Message) -> None:
    if message.text is None:
        return
    await add_message(
        session,
        lead,
        direction=Direction.IN,
        channel=Channel.BOT,
        text=message.text,
        sent_at=message.date,
        tg_message_id=message.message_id,
    )


def _with_current(question: str, value: str | None) -> str:
    return f"{question}\n{CURRENT_VALUE.format(value=value)}" if value else question


def _shorten(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _clean_name(raw: str) -> str:
    return " ".join(raw.split())


def _profile_name(user: User) -> str:
    return _clean_name(user.first_name)[:NAME_MAX_LEN]
