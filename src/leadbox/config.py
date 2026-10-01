from functools import lru_cache
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

# Used when DATABASE_URL is empty: a local file, so `alembic upgrade head` followed by uvicorn
# gives a working app without Postgres. Tests never use it, they build their own in-memory engine.
LOCAL_SQLITE_URL = "sqlite+aiosqlite:///leadbox.db"


class Settings(BaseSettings):
    # TG_API_ID / TG_API_HASH sit in the same .env but belong to nothing here, hence extra="ignore".
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    dev_bot_token: str = ""
    manager_chat_id: int | None = None
    database_url: str = ""
    public_base_url: str = ""
    webhook_secret: str = ""
    session_secret: str = ""
    crm_user: str = ""
    crm_password: str = ""
    bot_mode: Literal["webhook", "polling"] = "webhook"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def async_database_url(raw: str) -> tuple[URL, dict[str, Any]]:
    """Turn DATABASE_URL into an async SQLAlchemy URL plus connect_args for the driver.

    Neon hands out `postgresql://...?sslmode=require&channel_binding=require`. asyncpg takes
    neither query parameter: sslmode moves into connect_args as `ssl`, which asyncpg reads with the
    same libpq semantics, and channel_binding is dropped because asyncpg cannot do it.
    """
    if not raw:
        return make_url(LOCAL_SQLITE_URL), {}
    url = make_url(raw)
    if url.drivername not in ("postgresql", "postgres", "postgresql+asyncpg"):
        return url, {}
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    url = url.set(drivername="postgresql+asyncpg", query=query)
    return url, ({"ssl": sslmode} if sslmode else {})
