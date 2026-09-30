from sqlalchemy import select
from sqlalchemy.orm import Session
from genomic_platform.domain.models import Association, Gene, Trait, Variant

class CatalogRepository:
    def __init__(self, session: Session): self.session = session
    def variant_by_rsid(self, rsid: str): return self.session.scalar(select(Variant).where(Variant.rsid == rsid))
    def trait_by_name(self, name: str): return self.session.scalar(select(Trait).where(Trait.name == name))
    def gene_by_symbol(self, symbol: str): return self.session.scalar(select(Gene).where(Gene.symbol == symbol))
    def associations_for_trait(self, trait_id, limit: int = 100): return list(self.session.scalars(select(Association).where(Association.trait_id == trait_id).limit(limit)))
