import os

import pytest
from sqlalchemy.pool import StaticPool

from leadbox.config import Settings, get_settings
from leadbox.db import create_engine, create_sessionmaker
from leadbox.models import Base

# Every settings field is set here, so a developer's .env (which holds the real Neon URL and bot
# token) can never leak into a test: environment variables take precedence over the .env file.
TEST_ENV = {
    "BOT_TOKEN": "123456:TEST-bot-token",
    "DEV_BOT_TOKEN": "654321:TEST-dev-bot-token",
    "MANAGER_CHAT_ID": "-1001234567890",
    "DATABASE_URL": "",
    "PUBLIC_BASE_URL": "https://leadbox.test",
    "WEBHOOK_SECRET": "test-webhook-secret",
    "SESSION_SECRET": "test-session-secret",
    "CRM_USER": "demo",
    "CRM_PASSWORD": "test-password",
    "BOT_MODE": "webhook",
}
assert set(TEST_ENV) == {name.upper() for name in Settings.model_fields}, "TEST_ENV must cover every setting"
os.environ.update(TEST_ENV)
get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
async def engine():
    # In-memory SQLite lives as long as its connection; StaticPool hands every session that one
    # connection, so all sessions of a test see the same data. A fresh database per test.
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessionmaker(engine):
    return create_sessionmaker(engine)


@pytest.fixture
async def session(sessionmaker):
    async with sessionmaker() as session:
        yield session
