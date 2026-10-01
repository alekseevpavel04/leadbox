from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from leadbox.models import Base

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def test_upgrade_head_matches_models(tmp_path):
    db_path = (tmp_path / "migrated.db").as_posix()
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_downgrade_to_base_and_back(tmp_path):
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
