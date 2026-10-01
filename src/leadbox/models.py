from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Dialect,
    Enum,
    ForeignKey,
    MetaData,
    String,
    Table,
    Text,
    TypeDecorator,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Source(StrEnum):
    BOT = "bot"
    TELEGRAM = "telegram"
    MANUAL = "manual"


class Status(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    WON = "won"
    LOST = "lost"


OPEN_STATUSES = (Status.NEW, Status.IN_PROGRESS)


class FormStep(StrEnum):
    NAME = "name"
    CONTACT = "contact"
    REQUEST = "request"
    CONFIRM = "confirm"
    DONE = "done"
    # After /cancel: the lead stays, but the next text must not be taken as an answer to a step.
    CANCELLED = "cancelled"


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class Channel(StrEnum):
    BOT = "bot"
    BUSINESS = "business"


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """timestamptz on Postgres; on SQLite, which has no time zones, stored as naive UTC.

    Values always come back timezone-aware, so `utcnow() - lead.created_at` works on both.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: pass an aware one, e.g. datetime.now(UTC)")
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


def _str_enum(enum_cls: type[StrEnum]) -> Enum:
    # Plain VARCHAR with the enum value, no native PG enum and no CHECK: adding a value
    # (as FormStep.CANCELLED was) then needs no migration.
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=16,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class Base(AsyncAttrs, DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime}


lead_tags = Table(
    "lead_tags",
    Base.metadata,
    Column("lead_id", ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(Text)
    request: Mapped[str | None] = mapped_column(Text)
    source: Mapped[Source] = mapped_column(_str_enum(Source))
    campaign: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[Status] = mapped_column(_str_enum(Status), default=Status.NEW)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    tg_username: Mapped[str | None] = mapped_column(String(64))
    form_step: Mapped[FormStep | None] = mapped_column(_str_enum(FormStep))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    first_response_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    tags: Mapped[list["Tag"]] = relationship(secondary=lead_tags, order_by="Tag.name")
    messages: Mapped[list["Message"]] = relationship(back_populates="lead", order_by="[Message.sent_at, Message.id]")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    direction: Mapped[Direction] = mapped_column(_str_enum(Direction))
    channel: Mapped[Channel] = mapped_column(_str_enum(Channel))
    text: Mapped[str | None] = mapped_column(Text)
    # Message date from Telegram, not the time the row was written.
    sent_at: Mapped[datetime]
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)

    lead: Mapped[Lead] = relationship(back_populates="messages")


class BusinessConnection(Base):
    __tablename__ = "business_connections"

    # business_connection_id from Telegram, an opaque string
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger)
    is_enabled: Mapped[bool]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ProcessedUpdate(Base):
    __tablename__ = "processed_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    processed_at: Mapped[datetime] = mapped_column(default=utcnow)
