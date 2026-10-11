import hashlib
import logging
import re
from decimal import Decimal
from json import JSONDecodeError
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from genomic_platform.domain.models import (
    Association,
    EnrichmentObservation,
    Study,
)
from genomic_platform.etl.raw_store import encode_raw_json
from genomic_platform.etl.source_json import SourcePayloadError, decode_source_json
from genomic_platform.repositories.catalog import CatalogRepository
from genomic_platform.services import contracts as c

logger = logging.getLogger(__name__)


class QueryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fields(row, view):
    return {
        name: str(value) if isinstance(value := getattr(row, name), (UUID, Decimal)) else value
        for name in view.model_fields
        if hasattr(row, name)
    }


def _view(row, view):
    return view(**_fields(row, view))


def _page(rows, view, limit, offset, transform):
    more = len(rows) > limit
    return view(
        items=[transform(row) for row in rows[:limit]],
        limit=limit,
        offset=offset,
        has_more=more,
        next_offset=offset + limit if more else None,
    )


class CatalogService:
    def __init__(self, repo: CatalogRepository):
        self.repo = repo
        self.remaining_queries = 100
        self.remaining_rows = 1000

    def execute(self, operation: str, **arguments):
        try:
            if self.remaining_queries <= 0:
                raise QueryError("QUERY_LIMIT", "Request query budget exceeded.")
            self.remaining_queries -= 1
            self.remaining_rows -= (
                arguments.get("limit", 50)
                if operation
                in (
                    "variant_associations",
                    "trait_associations",
                    "gene_associations",
                    "get_trait_associations",
                    "association_sources",
                    "run_raw",
                    "run_processing",
                    "quality",
                    "variant_mappings",
                    "variant_enrichments",
                )
                else 1
            )
            if self.remaining_rows < 0:
                raise QueryError("QUERY_LIMIT", "Request row budget exceeded.")
            return getattr(self, operation)(**arguments)
        except QueryError:
            raise
        except ValidationError:
            logger.error("Persisted catalog data does not match the response contract.")
            raise QueryError("INTERNAL_ERROR", "Catalog data unavailable.") from None
        except (ValueError, TypeError):
            raise QueryError("INVALID_INPUT", "Invalid or ambiguous query arguments.") from None
        except SQLAlchemyError as error:
            logger.error("Catalog database query failed (%s).", type(error).__name__)
            raise QueryError("DATABASE_UNAVAILABLE", "Database query unavailable.") from None

    @staticmethod
    def pagination(limit: int, offset: int) -> None:
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 1 <= limit <= 100
            or not 0 <= offset <= 10000
        ):
            raise QueryError("INVALID_INPUT", "limit must be 1-100 and offset 0-10000.")

    @staticmethod
    def uuid(value: str) -> UUID:
        try:
            return UUID(value)
        except (ValueError, TypeError, AttributeError):
            raise QueryError("INVALID_INPUT", "Expected a UUID identifier.") from None

    @staticmethod
    def required(value, entity: str):
        if value is None:
            raise QueryError("NOT_FOUND", f"{entity} not found.")
        return value

    def get_variant(self, rsid: str) -> c.VariantView:
        if not isinstance(rsid, str) or not re.fullmatch(r"rs[0-9]{1,20}", rsid):
            raise QueryError("INVALID_INPUT", "Expected an rsID such as rs4988235.")
        variant = self.required(self.repo.variant_by_rsid(rsid), "Variant")
        return c.VariantView(
            **_fields(variant, c.VariantView),
            enrichment_status=self.repo.latest_enrichment_status(variant.id),
        )

    def get_trait(self, ontology_id: str) -> c.TraitView:
        if not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9]*[_:][A-Za-z0-9][A-Za-z0-9_.-]{0,90}", ontology_id
        ):
            raise QueryError(
                "INVALID_INPUT", "Expected an ontology identifier such as EFO_0004338."
            )
        return _view(
            self.required(self.repo.trait_by_identifier(ontology_id), "Trait"), c.TraitView
        )

    def get_gene(self, symbol: str) -> c.GeneView:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,49}", symbol):
            raise QueryError("INVALID_INPUT", "Invalid gene symbol.")
        return _view(self.required(self.repo.gene_by_symbol(symbol.upper()), "Gene"), c.GeneView)

    def get_study(self, study_id: str) -> c.StudyView:
        return _view(
            self.required(self.repo.by_id(Study, self.uuid(study_id)), "Study"), c.StudyView
        )

    def _association(self, row) -> c.AssociationView:
        association, rsid, trait, study, has_quality = row
        return c.AssociationView(
            **_fields(association, c.AssociationView),
            variant_rsid=rsid,
            trait=_view(trait, c.TraitView),
            study=_view(study, c.StudyView) if study else None,
            effect_size_text=association.metadata_json.get("effect_size_text"),
            effect_size_unit=association.metadata_json.get("effect_size_unit"),
            effect_direction=association.metadata_json.get("effect_direction"),
            quality_status="findings_present" if has_quality else "no_findings",
        )

    def get_association(self, association_id: str) -> c.AssociationView:
        rows = self.repo.page(
            self.repo.associations(association_id=self.uuid(association_id)), 1, 0
        )
        return self._association(self.required(rows[0] if rows else None, "Association"))

    def variant_associations(
        self, rsid: str, limit: int = 50, offset: int = 0
    ) -> c.AssociationPage:
        self.pagination(limit, offset)
        variant = self.get_variant(rsid)
        return _page(
            self.repo.page(self.repo.associations(variant_id=self.uuid(variant.id)), limit, offset),
            c.AssociationPage,
            limit,
            offset,
            self._association,
        )

    def trait_associations(
        self, ontology_id: str, limit: int = 50, offset: int = 0
    ) -> c.AssociationPage:
        self.pagination(limit, offset)
        trait = self.get_trait(ontology_id)
        return _page(
            self.repo.page(self.repo.associations(trait_id=self.uuid(trait.id)), limit, offset),
            c.AssociationPage,
            limit,
            offset,
            self._association,
        )

    def get_trait_associations(
        self, trait_name: str, limit: int = 50, offset: int = 0
    ) -> c.AssociationPage:
        self.pagination(limit, offset)
        if not 1 <= len(trait_name) <= 300:
            raise QueryError("INVALID_INPUT", "Invalid trait name.")
        trait = self.required(self.repo.trait_by_name(trait_name), "Trait")
        return _page(
            self.repo.page(self.repo.associations(trait_id=trait.id), limit, offset),
            c.AssociationPage,
            limit,
            offset,
            self._association,
        )

    def gene_associations(self, symbol: str, limit: int = 50, offset: int = 0) -> c.AssociationPage:
        self.pagination(limit, offset)
        gene = self.get_gene(symbol)
        return _page(
            self.repo.page(self.repo.associations(gene_id=self.uuid(gene.id)), limit, offset),
            c.AssociationPage,
            limit,
            offset,
            self._association,
        )

    def association_sources(
        self, association_id: str, limit: int = 50, offset: int = 0
    ) -> c.SourcePage:
        self.pagination(limit, offset)
        identifier = self.uuid(association_id)
        self.required(self.repo.by_id(Association, identifier), "Association")
        return _page(
            self.repo.page(self.repo.sources(identifier), limit, offset),
            c.SourcePage,
            limit,
            offset,
            lambda row: c.SourceView(
                **_fields(row[0], c.SourceView), source_code=row[1], sha256=row[2]
            ),
        )

    def get_run(self, run_id: str) -> c.RunView:
        row = self.required(self.repo.run(self.uuid(run_id)), "Run")
        return c.RunView(**_fields(row[0], c.RunView), source_code=row[1])

    @staticmethod
    def _raw(row) -> c.RawView:
        raw = row[0]
        reported = None
        if raw.source_record_type == "association" and raw.payload_text is not None:
            if hashlib.sha256(raw.payload_text.encode()).hexdigest() != raw.sha256:
                logger.error("Raw evidence checksum verification failed.")
                raise QueryError("INTERNAL_ERROR", "Evidence integrity verification failed.")
            try:
                payload = decode_source_json(raw.payload_text)
            except (JSONDecodeError, SourcePayloadError):
                logger.error("Stored raw evidence is not valid source JSON.")
                raise QueryError(
                    "INTERNAL_ERROR", "Evidence integrity verification failed."
                ) from None
            if isinstance(payload, dict) and "p_value" in payload:
                reported = (
                    encode_raw_json(payload["p_value"]) if payload["p_value"] is not None else None
                )
        return c.RawView(
            **_fields(raw, c.RawView), source_code=row[1], reported_p_value_text=reported
        )

    def get_raw(self, raw_id: str) -> c.RawView:
        return self._raw(self.required(self.repo.raw_record(self.uuid(raw_id)), "Raw evidence"))

    def run_raw(
        self, run_id: str, limit: int = 50, offset: int = 0, disposition: str | None = None
    ) -> c.RawPage:
        self.pagination(limit, offset)
        self.get_run(run_id)
        if disposition not in (None, "captured", "loaded", "partial", "quarantined"):
            raise QueryError("INVALID_INPUT", "Invalid raw disposition.")
        return _page(
            self.repo.page(self.repo.raw_records(self.uuid(run_id), disposition), limit, offset),
            c.RawPage,
            limit,
            offset,
            self._raw,
        )

    def run_processing(self, run_id: str, limit: int = 50, offset: int = 0) -> c.ProcessingPage:
        self.pagination(limit, offset)
        self.get_run(run_id)
        return _page(
            self.repo.page(self.repo.processing(self.uuid(run_id)), limit, offset),
            c.ProcessingPage,
            limit,
            offset,
            lambda row: _view(row[0], c.ProcessingView),
        )

    def quality(
        self, scope: str, identifier: str, limit: int = 50, offset: int = 0
    ) -> c.QualityPage:
        self.pagination(limit, offset)
        key = self.uuid(identifier)
        if scope == "raw":
            self.get_raw(identifier)
            statement = self.repo.quality(raw_id=key)
        elif scope == "association":
            self.required(self.repo.by_id(Association, key), "Association")
            statement = self.repo.quality(association_id=key)
        elif scope == "run":
            self.get_run(identifier)
            statement = self.repo.quality(run_id=key)
        elif scope == "enrichment":
            self.required(self.repo.by_id(EnrichmentObservation, key), "Enrichment observation")
            statement = self.repo.quality(observation_id=key)
        else:
            raise QueryError(
                "INVALID_INPUT", "Quality scope must be association, raw, run or enrichment."
            )
        return _page(
            self.repo.page(statement, limit, offset),
            c.QualityPage,
            limit,
            offset,
            lambda row: _view(row[0], c.QualityView),
        )

    def variant_mappings(
        self, rsid: str, limit: int = 50, offset: int = 0, assembly: str | None = None
    ) -> c.MappingPage:
        self.pagination(limit, offset)
        variant = self.get_variant(rsid)
        if assembly not in (None, "GRCh37", "GRCh38"):
            raise QueryError("INVALID_INPUT", "Assembly must be GRCh37 or GRCh38.")

        def transform(row):
            mapping, evidence, raw, source_code = row
            return c.MappingView(
                **_fields(mapping, c.MappingView),
                raw_object_id=str(raw.id),
                requested_assembly=evidence.requested_assembly,
                source_code=source_code,
                allele_string=mapping.payload_json.get("allele_string")
                if isinstance(mapping.payload_json.get("allele_string"), str)
                else None,
                ancestral_allele=mapping.payload_json.get("ancestral_allele")
                if isinstance(mapping.payload_json.get("ancestral_allele"), str)
                else None,
                sha256=raw.sha256,
            )

        return _page(
            self.repo.page(self.repo.mappings(self.uuid(variant.id), assembly), limit, offset),
            c.MappingPage,
            limit,
            offset,
            transform,
        )

    def variant_enrichments(self, rsid: str, limit: int = 50, offset: int = 0) -> c.EnrichmentPage:
        self.pagination(limit, offset)
        variant = self.get_variant(rsid)

        def transform(row):
            observation, _evidence, raw, source_code = row
            return c.EnrichmentView(
                **_fields(observation, c.EnrichmentView),
                source_code=source_code,
                raw_object_id=str(raw.id) if raw else None,
                sha256=raw.sha256 if raw else None,
            )

        return _page(
            self.repo.page(self.repo.enrichments(self.uuid(variant.id)), limit, offset),
            c.EnrichmentPage,
            limit,
            offset,
            transform,
        )
