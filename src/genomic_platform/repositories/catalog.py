from uuid import UUID

from sqlalchemy import Select, exists, or_, select
from sqlalchemy.orm import Session

from genomic_platform.domain.models import (
    Association,
    AssociationSource,
    EnrichmentObservation,
    Gene,
    IngestionRun,
    ProcessingObservation,
    QualityIssue,
    RawObject,
    SourceSystem,
    Study,
    Trait,
    Variant,
    VariantEnrichment,
    VariantGene,
    VariantMapping,
)


class CatalogRepository:
    def __init__(self, session: Session):
        self.session = session

    def variant_by_rsid(self, rsid: str) -> Variant | None:
        return self.session.scalar(select(Variant).where(Variant.rsid == rsid))

    def trait_by_identifier(self, identifier: str) -> Trait | None:
        return self.session.scalar(select(Trait).where(Trait.ontology_id == identifier))

    def trait_by_name(self, name: str) -> Trait | None:
        rows = list(self.session.scalars(select(Trait).where(Trait.name == name).limit(2)))
        if len(rows) > 1:
            raise ValueError("Trait name is ambiguous; use an ontology identifier.")
        return rows[0] if rows else None

    def gene_by_symbol(self, symbol: str) -> Gene | None:
        rows = list(self.session.scalars(select(Gene).where(Gene.symbol == symbol).limit(2)))
        if len(rows) > 1:
            raise ValueError("Gene symbol is ambiguous.")
        return rows[0] if rows else None

    def by_id(self, model, identifier: UUID):
        return self.session.get(model, identifier)

    def page(self, statement: Select, limit: int, offset: int):
        return list(self.session.execute(statement.limit(limit + 1).offset(offset)))

    def associations(self, *, variant_id=None, trait_id=None, gene_id=None, association_id=None):
        quality = (
            exists()
            .where(
                QualityIssue.entity_type == "association",
                or_(
                    QualityIssue.raw_object_id.in_(
                        select(RawObject.id)
                        .where(RawObject.association_id == Association.id)
                        .correlate(Association)
                    ),
                    QualityIssue.processing_observation_id.in_(
                        select(ProcessingObservation.id)
                        .where(ProcessingObservation.association_id == Association.id)
                        .correlate(Association)
                    ),
                ),
            )
            .correlate(Association)
        )
        statement = (
            select(Association, Variant.rsid, Trait, Study, quality.label("has_quality"))
            .join(Variant, Association.variant_id == Variant.id)
            .join(Trait, Association.trait_id == Trait.id)
            .outerjoin(Study, Association.study_id == Study.id)
            .order_by(Association.id)
        )
        if variant_id is not None:
            statement = statement.where(Association.variant_id == variant_id)
        if trait_id is not None:
            statement = statement.where(Association.trait_id == trait_id)
        if gene_id is not None:
            statement = statement.where(
                exists().where(
                    VariantGene.variant_id == Association.variant_id, VariantGene.gene_id == gene_id
                )
            )
        if association_id is not None:
            statement = statement.where(Association.id == association_id)
        return statement

    def sources(self, association_id: UUID):
        return (
            select(AssociationSource, SourceSystem.code, RawObject.sha256)
            .join(SourceSystem, SourceSystem.id == AssociationSource.source_system_id)
            .outerjoin(RawObject, RawObject.id == AssociationSource.raw_object_id)
            .where(AssociationSource.association_id == association_id)
            .order_by(AssociationSource.source_system_id, AssociationSource.source_record_id)
        )

    def raw_records(self, run_id: UUID, disposition: str | None):
        statement = (
            select(RawObject, SourceSystem.code)
            .join(IngestionRun, IngestionRun.id == RawObject.ingestion_run_id)
            .join(SourceSystem, SourceSystem.id == IngestionRun.source_system_id)
            .where(RawObject.ingestion_run_id == run_id)
            .order_by(RawObject.id)
        )
        return statement.where(RawObject.disposition == disposition) if disposition else statement

    def raw_record(self, raw_id: UUID):
        return self.session.execute(
            select(RawObject, SourceSystem.code)
            .join(IngestionRun, IngestionRun.id == RawObject.ingestion_run_id)
            .join(SourceSystem, SourceSystem.id == IngestionRun.source_system_id)
            .where(RawObject.id == raw_id)
        ).first()

    def run(self, run_id: UUID):
        return self.session.execute(
            select(IngestionRun, SourceSystem.code)
            .join(SourceSystem, SourceSystem.id == IngestionRun.source_system_id)
            .where(IngestionRun.id == run_id)
        ).first()

    def processing(self, run_id: UUID):
        return (
            select(ProcessingObservation)
            .where(ProcessingObservation.ingestion_run_id == run_id)
            .order_by(ProcessingObservation.id)
        )

    def quality(self, *, raw_id=None, association_id=None, run_id=None, observation_id=None):
        statement = select(QualityIssue).order_by(QualityIssue.id)
        if raw_id is not None:
            statement = statement.where(
                or_(
                    QualityIssue.raw_object_id == raw_id,
                    QualityIssue.processing_observation_id.in_(
                        select(ProcessingObservation.id).where(
                            ProcessingObservation.raw_object_id == raw_id
                        )
                    ),
                    QualityIssue.enrichment_observation_id.in_(
                        select(EnrichmentObservation.id)
                        .join(
                            VariantEnrichment,
                            VariantEnrichment.id == EnrichmentObservation.enrichment_id,
                        )
                        .where(VariantEnrichment.raw_object_id == raw_id)
                    ),
                )
            )
        if association_id is not None:
            statement = statement.where(
                or_(
                    QualityIssue.raw_object_id.in_(
                        select(RawObject.id).where(RawObject.association_id == association_id)
                    ),
                    QualityIssue.processing_observation_id.in_(
                        select(ProcessingObservation.id).where(
                            ProcessingObservation.association_id == association_id
                        )
                    ),
                )
            )
        if run_id is not None:
            statement = statement.where(QualityIssue.ingestion_run_id == run_id)
        if observation_id is not None:
            statement = statement.where(QualityIssue.enrichment_observation_id == observation_id)
        return statement

    def mappings(self, variant_id: UUID, assembly: str | None):
        statement = (
            select(VariantMapping, VariantEnrichment, RawObject, SourceSystem.code)
            .join(VariantEnrichment, VariantEnrichment.id == VariantMapping.enrichment_id)
            .join(RawObject, RawObject.id == VariantEnrichment.raw_object_id)
            .join(IngestionRun, IngestionRun.id == RawObject.ingestion_run_id)
            .join(SourceSystem, SourceSystem.id == IngestionRun.source_system_id)
            .where(VariantMapping.variant_id == variant_id)
            .order_by(VariantMapping.enrichment_id, VariantMapping.ordinal)
        )
        return statement.where(VariantMapping.assembly == assembly) if assembly else statement

    def enrichments(self, variant_id: UUID):
        return (
            select(EnrichmentObservation, VariantEnrichment, RawObject, SourceSystem.code)
            .join(IngestionRun, IngestionRun.id == EnrichmentObservation.ingestion_run_id)
            .join(SourceSystem, SourceSystem.id == IngestionRun.source_system_id)
            .outerjoin(
                VariantEnrichment, VariantEnrichment.id == EnrichmentObservation.enrichment_id
            )
            .outerjoin(RawObject, RawObject.id == VariantEnrichment.raw_object_id)
            .where(EnrichmentObservation.variant_id == variant_id)
            .order_by(EnrichmentObservation.retrieved_at, EnrichmentObservation.id)
        )

    def latest_enrichment_status(self, variant_id: UUID) -> str | None:
        return self.session.scalar(
            select(EnrichmentObservation.status)
            .where(EnrichmentObservation.variant_id == variant_id)
            .order_by(EnrichmentObservation.retrieved_at.desc(), EnrichmentObservation.id.desc())
            .limit(1)
        )
