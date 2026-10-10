from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection


def migration_config() -> Config:
    root = Path(__file__).resolve().parents[2]
    return Config(str(root / "alembic.ini"))


def upgrade_database(connection: Connection | None = None) -> None:
    config = migration_config()
    if connection is not None:
        config.attributes["connection"] = connection
    command.upgrade(config, "head")
