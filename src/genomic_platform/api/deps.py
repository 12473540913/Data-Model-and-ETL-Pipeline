from collections.abc import Generator
from sqlalchemy.orm import Session
from genomic_platform.db import SessionLocal
from genomic_platform.repositories.catalog import CatalogRepository
from genomic_platform.services.catalog import CatalogService

def get_session() -> Generator[Session,None,None]:
    s=SessionLocal()
    try: yield s
    finally: s.close()

def build_catalog_service(session: Session) -> CatalogService: return CatalogService(CatalogRepository(session))
