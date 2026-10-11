from typing import Annotated

import strawberry
from fastapi import Depends
from graphql import GraphQLError
from sqlalchemy.orm import Session
from strawberry.extensions import MaskErrors, MaxAliasesLimiter, MaxTokensLimiter, QueryDepthLimiter
from strawberry.fastapi import BaseContext, GraphQLRouter
from strawberry.schema.config import StrawberryConfig
from strawberry.types import Info
from strawberry.types.scalar import ScalarDefinition

from genomic_platform.api.deps import build_catalog_service, get_session
from genomic_platform.services import contracts as c
from genomic_platform.services.catalog import CatalogService, QueryError


# The same Pydantic contracts drive REST, GraphQL and SOAP; no resolver interprets science.
@strawberry.experimental.pydantic.type(model=c.TraitView, all_fields=True)
class TraitType:
    pass


@strawberry.experimental.pydantic.type(model=c.StudyView, all_fields=True)
class StudyType:
    pass


@strawberry.experimental.pydantic.type(model=c.GeneView, all_fields=True)
class GeneType:
    pass


@strawberry.experimental.pydantic.type(model=c.VariantView, all_fields=True)
class VariantType:
    pass


@strawberry.experimental.pydantic.type(model=c.AssociationView, all_fields=True)
class AssociationType:
    pass


@strawberry.experimental.pydantic.type(model=c.SourceView, all_fields=True)
class SourceType:
    pass


@strawberry.experimental.pydantic.type(model=c.RawView, all_fields=True)
class RawType:
    pass


@strawberry.experimental.pydantic.type(model=c.QualityView, all_fields=True)
class QualityType:
    pass


@strawberry.experimental.pydantic.type(model=c.MappingView, all_fields=True)
class MappingType:
    pass


@strawberry.experimental.pydantic.type(model=c.EnrichmentView, all_fields=True)
class EnrichmentType:
    pass


@strawberry.experimental.pydantic.type(model=c.ProcessingView, all_fields=True)
class ProcessingType:
    pass


@strawberry.experimental.pydantic.type(model=c.RunView, all_fields=True)
class RunType:
    pass


@strawberry.experimental.pydantic.type(model=c.AssociationPage, all_fields=True)
class AssociationPage:
    pass


@strawberry.experimental.pydantic.type(model=c.SourcePage, all_fields=True)
class SourcePage:
    pass


@strawberry.experimental.pydantic.type(model=c.RawPage, all_fields=True)
class RawPage:
    pass


@strawberry.experimental.pydantic.type(model=c.QualityPage, all_fields=True)
class QualityPage:
    pass


@strawberry.experimental.pydantic.type(model=c.MappingPage, all_fields=True)
class MappingPage:
    pass


@strawberry.experimental.pydantic.type(model=c.EnrichmentPage, all_fields=True)
class EnrichmentPage:
    pass


@strawberry.experimental.pydantic.type(model=c.ProcessingPage, all_fields=True)
class ProcessingPage:
    pass


class CatalogContext(BaseContext):
    def __init__(self, service: CatalogService):
        super().__init__()
        self.service = service


def get_context(session: Annotated[Session, Depends(get_session)]) -> CatalogContext:
    return CatalogContext(build_catalog_service(session))


def query(info: Info[CatalogContext, None], result_type, operation: str, **kwargs):
    try:
        return result_type.from_pydantic(info.context.service.execute(operation, **kwargs))
    except QueryError as error:
        raise GraphQLError(str(error), extensions={"code": error.code}) from None


@strawberry.type
class Query:
    @strawberry.field
    def variant(self, info: Info[CatalogContext, None], rsid: str) -> VariantType | None:
        return query(info, VariantType, "get_variant", rsid=rsid)

    @strawberry.field
    def trait(self, info: Info[CatalogContext, None], ontology_id: str) -> TraitType | None:
        return query(info, TraitType, "get_trait", ontology_id=ontology_id)

    @strawberry.field
    def gene(self, info: Info[CatalogContext, None], symbol: str) -> GeneType | None:
        return query(info, GeneType, "get_gene", symbol=symbol)

    @strawberry.field
    def study(self, info: Info[CatalogContext, None], study_id: str) -> StudyType | None:
        return query(info, StudyType, "get_study", study_id=study_id)

    @strawberry.field
    def association(
        self, info: Info[CatalogContext, None], association_id: str
    ) -> AssociationType | None:
        return query(info, AssociationType, "get_association", association_id=association_id)

    @strawberry.field
    def variant_associations(
        self, info: Info[CatalogContext, None], rsid: str, limit: int = 50, offset: int = 0
    ) -> AssociationPage | None:
        return query(
            info, AssociationPage, "variant_associations", rsid=rsid, limit=limit, offset=offset
        )

    @strawberry.field
    def associations_for_trait(
        self, info: Info[CatalogContext, None], ontology_id: str, limit: int = 50, offset: int = 0
    ) -> AssociationPage | None:
        return query(
            info,
            AssociationPage,
            "trait_associations",
            ontology_id=ontology_id,
            limit=limit,
            offset=offset,
        )

    @strawberry.field
    def trait_associations(
        self, info: Info[CatalogContext, None], trait_name: str, limit: int = 50
    ) -> list[AssociationType] | None:
        page = query(
            info, AssociationPage, "get_trait_associations", trait_name=trait_name, limit=limit
        )
        return page.items

    @strawberry.field
    def gene_associations(
        self, info: Info[CatalogContext, None], symbol: str, limit: int = 50, offset: int = 0
    ) -> AssociationPage | None:
        return query(
            info, AssociationPage, "gene_associations", symbol=symbol, limit=limit, offset=offset
        )

    @strawberry.field
    def association_sources(
        self,
        info: Info[CatalogContext, None],
        association_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> SourcePage | None:
        return query(
            info,
            SourcePage,
            "association_sources",
            association_id=association_id,
            limit=limit,
            offset=offset,
        )

    @strawberry.field
    def variant_mappings(
        self,
        info: Info[CatalogContext, None],
        rsid: str,
        limit: int = 50,
        offset: int = 0,
        assembly: str | None = None,
    ) -> MappingPage | None:
        return query(
            info,
            MappingPage,
            "variant_mappings",
            rsid=rsid,
            limit=limit,
            offset=offset,
            assembly=assembly,
        )

    @strawberry.field
    def variant_enrichments(
        self, info: Info[CatalogContext, None], rsid: str, limit: int = 50, offset: int = 0
    ) -> EnrichmentPage | None:
        return query(
            info, EnrichmentPage, "variant_enrichments", rsid=rsid, limit=limit, offset=offset
        )

    @strawberry.field
    def raw_evidence(self, info: Info[CatalogContext, None], raw_id: str) -> RawType | None:
        return query(info, RawType, "get_raw", raw_id=raw_id)

    @strawberry.field
    def ingestion_run(self, info: Info[CatalogContext, None], run_id: str) -> RunType | None:
        return query(info, RunType, "get_run", run_id=run_id)

    @strawberry.field
    def run_raw(
        self,
        info: Info[CatalogContext, None],
        run_id: str,
        limit: int = 50,
        offset: int = 0,
        disposition: str | None = None,
    ) -> RawPage | None:
        return query(
            info,
            RawPage,
            "run_raw",
            run_id=run_id,
            limit=limit,
            offset=offset,
            disposition=disposition,
        )

    @strawberry.field
    def run_processing(
        self, info: Info[CatalogContext, None], run_id: str, limit: int = 50, offset: int = 0
    ) -> ProcessingPage | None:
        return query(
            info, ProcessingPage, "run_processing", run_id=run_id, limit=limit, offset=offset
        )

    @strawberry.field
    def quality(
        self,
        info: Info[CatalogContext, None],
        scope: str,
        identifier: str,
        limit: int = 50,
        offset: int = 0,
    ) -> QualityPage | None:
        return query(
            info,
            QualityPage,
            "quality",
            scope=scope,
            identifier=identifier,
            limit=limit,
            offset=offset,
        )


def parse_long(value):
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise GraphQLError("Expected a signed 64-bit integer.")
    return value


Long = ScalarDefinition(
    name="Long",
    description="Signed 64-bit integer.",
    serialize=int,
    parse_value=parse_long,
    specified_by_url=None,
    parse_literal=None,
)
schema = strawberry.Schema(
    query=Query,
    config=StrawberryConfig(scalar_map={int: Long}),
    extensions=[
        lambda: QueryDepthLimiter(max_depth=8),
        lambda: MaxAliasesLimiter(max_alias_count=20),
        lambda: MaxTokensLimiter(max_token_count=2000),
        lambda: MaskErrors(
            should_mask_error=lambda error: (
                error.original_error is not None
                and not isinstance(error.original_error, QueryError)
                and not (error.extensions or {}).get("code")
            )
        ),
    ],
)
graphql_router = GraphQLRouter(schema, context_getter=get_context, graphql_ide=None)
