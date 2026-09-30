from genomic_platform.repositories.catalog import CatalogRepository
class CatalogService:
    def __init__(self, repo: CatalogRepository): self.repo=repo
    def get_variant(self, rsid: str): return self.repo.variant_by_rsid(rsid)
    def get_trait_associations(self, trait_name: str, limit: int=100):
        trait=self.repo.trait_by_name(trait_name); return [] if not trait else self.repo.associations_for_trait(trait.id, limit)
    def get_gene(self, symbol: str): return self.repo.gene_by_symbol(symbol)
