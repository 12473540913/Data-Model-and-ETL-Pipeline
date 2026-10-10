from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from genomic_platform.config import get_settings
from genomic_platform.domain.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def migrate(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations():
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    if context.is_offline_mode():
        context.configure(
            url=url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    elif config.attributes.get("connection") is not None:
        migrate(config.attributes["connection"])
    else:
        engine = create_engine(url, poolclass=pool.NullPool)
        try:
            with engine.connect() as connection:
                migrate(connection)
        finally:
            engine.dispose()


run_migrations()
