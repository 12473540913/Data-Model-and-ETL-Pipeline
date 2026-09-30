import strawberry
from strawberry.fastapi import GraphQLRouter
from genomic_platform.db import SessionLocal
from genomic_platform.repositories.catalog import CatalogRepository
from genomic_platform.services.catalog import CatalogService

@strawberry.type
class VariantType:
    rsid: str | None; assembly: str; chromosome: str | None; position: int | None; reference_allele: str | None; alternate_alleles: list[str]

@strawberry.type
class AssociationType:
    id: str; p_value: str | None; effect_allele: str | None; effect_size: str | None; effect_size_type: str | None

@strawberry.type
class Query:
    @strawberry.field
    def variant(self, rsid: str) -> VariantType | None:
        with SessionLocal() as s:
            v=CatalogService(CatalogRepository(s)).get_variant(rsid)
            if not v: return None
            return VariantType(rsid=v.rsid,assembly=v.assembly,chromosome=v.chromosome,position=v.position,reference_allele=v.reference_allele,alternate_alleles=v.alternate_alleles or [])
    @strawberry.field
    def trait_associations(self, trait_name: str="eye color", limit: int=100) -> list[AssociationType]:
        with SessionLocal() as s:
            rows=CatalogService(CatalogRepository(s)).get_trait_associations(trait_name,limit)
            return [AssociationType(id=str(a.id),p_value=str(a.p_value) if a.p_value is not None else None,effect_allele=a.effect_allele,effect_size=str(a.effect_size) if a.effect_size is not None else None,effect_size_type=a.effect_size_type) for a in rows]

schema=strawberry.Schema(query=Query)
graphql_router=GraphQLRouter(schema)
