from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from genomic_platform.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    pool_timeout=5,
    connect_args={"connect_timeout": 5},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
