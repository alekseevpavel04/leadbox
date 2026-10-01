import pytest

from leadbox.config import Settings, async_database_url


def test_neon_url_is_converted_for_asyncpg():
    url, connect_args = async_database_url(
        "postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    assert url.drivername == "postgresql+asyncpg"
    assert url.host == "ep-x.eu-central-1.aws.neon.tech"
    assert url.username == "u"
    assert url.password == "p"
    assert url.database == "neondb"
    assert dict(url.query) == {}
    assert connect_args == {"ssl": "require"}


def test_other_query_parameters_survive():
    url, connect_args = async_database_url("postgresql://u:p@host/db?sslmode=verify-full&application_name=leadbox")
    assert dict(url.query) == {"application_name": "leadbox"}
    assert connect_args == {"ssl": "verify-full"}


def test_postgres_url_without_sslmode_gets_no_ssl_args():
    url, connect_args = async_database_url("postgresql://u:p@localhost/db")
    assert url.drivername == "postgresql+asyncpg"
    assert connect_args == {}


def test_empty_url_is_refused():
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        async_database_url("")


def test_sqlite_url_is_kept_for_local_work():
    url, connect_args = async_database_url("sqlite+aiosqlite:///leadbox.db")
    assert url.drivername == "sqlite+aiosqlite"
    assert connect_args == {}


def test_unused_telegram_client_keys_are_ignored(monkeypatch):
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "abc")
    settings = Settings(_env_file=None)
    assert not hasattr(settings, "tg_api_id")


def test_settings_in_tests_do_not_come_from_dotenv(settings):
    assert settings.database_url == ""
    assert settings.bot_token.startswith("123456:TEST")


def test_empty_manager_chat_id_means_no_notifications(monkeypatch):
    monkeypatch.setenv("MANAGER_CHAT_ID", "")
    assert Settings(_env_file=None).manager_chat_id is None
