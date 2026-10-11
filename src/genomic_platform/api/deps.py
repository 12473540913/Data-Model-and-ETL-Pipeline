from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from genomic_platform.db import SessionLocal
from genomic_platform.repositories.catalog import CatalogRepository
from genomic_platform.services.catalog import CatalogService


@contextmanager
def read_session(factory=SessionLocal):
    with factory() as session:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
        yield session
        session.rollback()


def get_session() -> Generator[Session, None, None]:
    with read_session() as session:
        yield session


def build_catalog_service(session: Session) -> CatalogService:
    return CatalogService(CatalogRepository(session))


def get_service(session: Session) -> CatalogService:
    return build_catalog_service(session)
