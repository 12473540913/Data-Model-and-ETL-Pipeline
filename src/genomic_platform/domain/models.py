from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from genomic_platform.db import Base


class SourceSystem(Base):
    __tablename__ = "source_system"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DatasetRelease(Base):
    __tablename__ = "dataset_release"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_system_id: Mapped[UUID] = mapped_column(ForeignKey("source_system.id"))
    release_key: Mapped[str] = mapped_column(String(200))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint("source_system_id", "release_key"),)


class IngestionRun(Base):
    __tablename__ = "ingestion_run"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_system_id: Mapped[UUID] = mapped_column(ForeignKey("source_system.id"))
    dataset_release_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataset_release.id"))
    pipeline_name: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="running")
    metrics_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    originating_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ingestion_run.id", name="fk_ingestion_run_origin"), index=True
    )
    __table_args__ = (
        UniqueConstraint("id", "source_system_id", name="uq_ingestion_run_source"),
        CheckConstraint(
            "status IN ('running', 'completed', 'completed_with_issues', 'failed')",
            name="ck_ingestion_run_status",
        ),
        Index("ix_ingestion_run_source_started", "source_system_id", "started_at"),
    )


class RawObject(Base):
    __tablename__ = "raw_object"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ingestion_run_id: Mapped[UUID] = mapped_column(ForeignKey("ingestion_run.id"))
    source_record_type: Mapped[str] = mapped_column(String(100))
    source_record_id: Mapped[str | None] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    storage_uri: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    payload_text: Mapped[str | None] = mapped_column(Text)
    normalized_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    association_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("association.id", name="fk_raw_object_association"), index=True
    )
    disposition: Mapped[str] = mapped_column(
        String(30), default="captured", server_default="captured"
    )
    p_value_candidate: Mapped[Decimal | None] = mapped_column(Numeric())
    p_value_text: Mapped[str | None] = mapped_column(Text)
    p_value_mantissa: Mapped[str | None] = mapped_column(Text)
    p_value_exponent: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("id", "ingestion_run_id", name="uq_raw_object_run"),
        UniqueConstraint(
            "id", "ingestion_run_id", "source_record_id", name="uq_raw_object_source_run"
        ),
        UniqueConstraint(
            "id",
            "ingestion_run_id",
            "association_id",
            "source_record_id",
            name="uq_raw_object_association_source",
        ),
        UniqueConstraint(
            "ingestion_run_id",
            "source_record_type",
            "source_record_id",
            "sha256",
            name="uq_raw_object_run_payload",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "disposition IN ('captured', 'loaded', 'partial', 'quarantined')",
            name="ck_raw_object_disposition",
        ),
        Index("ix_raw_object_source_record", "source_record_type", "source_record_id"),
        Index("ix_raw_object_run_disposition", "ingestion_run_id", "disposition"),
    )


class Trait(Base):
    __tablename__ = "trait"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ontology_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))


class TraitSynonym(Base):
    __tablename__ = "trait_synonym"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    trait_id: Mapped[UUID] = mapped_column(ForeignKey("trait.id"))
    source_system_id: Mapped[UUID | None] = mapped_column(ForeignKey("source_system.id"))
    synonym: Mapped[str] = mapped_column(String(300))
    __table_args__ = (UniqueConstraint("trait_id", "source_system_id", "synonym"),)


class Gene(Base):
    __tablename__ = "gene"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ensembl_gene_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    symbol: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str | None] = mapped_column(String(300))
    chromosome: Mapped[str | None] = mapped_column(String(30))


class Variant(Base):
    __tablename__ = "variant"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    rsid: Mapped[str | None] = mapped_column(String(50), unique=True, index=True)
    assembly: Mapped[str | None] = mapped_column(String(30))
    chromosome: Mapped[str | None] = mapped_column(String(30), index=True)
    position: Mapped[int | None] = mapped_column(BigInteger)
    reference_allele: Mapped[str | None] = mapped_column(Text)
    alternate_alleles: Mapped[list] = mapped_column(JSONB, default=list)
    variant_class: Mapped[str | None] = mapped_column(String(80))
    most_severe_consequence: Mapped[str | None] = mapped_column(String(150))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    projection_mapping_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = (
        Index("ix_variant_locus", "assembly", "chromosome", "position"),
        CheckConstraint(
            "(chromosome IS NULL AND position IS NULL) OR assembly IS NOT NULL",
            name="ck_variant_qualified_coordinates",
        ),
        CheckConstraint(
            "projection_mapping_id IS NULL OR "
            "(assembly IS NOT NULL AND chromosome IS NOT NULL AND position IS NOT NULL)",
            name="ck_variant_complete_projection",
        ),
        ForeignKeyConstraint(
            ["projection_mapping_id", "id", "assembly", "chromosome", "position"],
            [
                "variant_mapping.id",
                "variant_mapping.variant_id",
                "variant_mapping.assembly",
                "variant_mapping.chromosome",
                "variant_mapping.position",
            ],
            name="fk_variant_projection_mapping",
            use_alter=True,
        ),
    )


class VariantEnrichment(Base):
    __tablename__ = "variant_enrichment"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    variant_id: Mapped[UUID] = mapped_column(ForeignKey("variant.id"), index=True)
    raw_object_id: Mapped[UUID] = mapped_column(ForeignKey("raw_object.id"), unique=True)
    requested_assembly: Mapped[str] = mapped_column(String(30))
    sha256: Mapped[str] = mapped_column(String(64))
    normalized_json: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint(
            "variant_id", "requested_assembly", "sha256", name="uq_enrichment_evidence"
        ),
        UniqueConstraint("id", "variant_id", name="uq_enrichment_variant"),
        CheckConstraint(
            "requested_assembly IN ('GRCh37', 'GRCh38')", name="ck_enrichment_assembly"
        ),
    )


class VariantMapping(Base):
    __tablename__ = "variant_mapping"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    enrichment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    variant_id: Mapped[UUID] = mapped_column(ForeignKey("variant.id"))
    ordinal: Mapped[int] = mapped_column(BigInteger)
    assembly: Mapped[str | None] = mapped_column(Text)
    coordinate_system: Mapped[str | None] = mapped_column(Text)
    chromosome: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int | None] = mapped_column(BigInteger)
    end_position: Mapped[int | None] = mapped_column(BigInteger)
    strand: Mapped[int | None] = mapped_column(BigInteger)
    valid: Mapped[bool] = mapped_column(Boolean)
    payload_json: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(
            ["enrichment_id", "variant_id"],
            ["variant_enrichment.id", "variant_enrichment.variant_id"],
            name="fk_mapping_enrichment_variant",
        ),
        UniqueConstraint("enrichment_id", "ordinal", name="uq_mapping_ordinal"),
        UniqueConstraint(
            "id", "variant_id", "assembly", "chromosome", "position", name="uq_mapping_projection"
        ),
        Index("ix_variant_mapping_assembly", "variant_id", "assembly"),
    )


class EnrichmentObservation(Base):
    __tablename__ = "enrichment_observation"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ingestion_run_id: Mapped[UUID] = mapped_column(ForeignKey("ingestion_run.id"), index=True)
    variant_id: Mapped[UUID] = mapped_column(ForeignKey("variant.id"))
    enrichment_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    requested_assembly: Mapped[str] = mapped_column(String(30))
    lookup_identifier: Mapped[str] = mapped_column(String(50))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30))
    metadata_json: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(
            ["enrichment_id", "variant_id"],
            ["variant_enrichment.id", "variant_enrichment.variant_id"],
            name="fk_observation_enrichment_variant",
        ),
        UniqueConstraint("ingestion_run_id", "variant_id", name="uq_enrichment_run_variant"),
        UniqueConstraint("id", "ingestion_run_id", name="uq_enrichment_observation_run"),
        CheckConstraint(
            "status IN ('enriched', 'partial', 'ambiguous', 'conflict', 'not_found', 'failed')",
            name="ck_enrichment_observation_status",
        ),
        CheckConstraint(
            "requested_assembly IN ('GRCh37', 'GRCh38')", name="ck_observation_assembly"
        ),
        Index("ix_enrichment_selection", "variant_id", "requested_assembly", "status"),
    )


class VariantAlias(Base):
    __tablename__ = "variant_alias"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    variant_id: Mapped[UUID] = mapped_column(ForeignKey("variant.id"))
    source_system_id: Mapped[UUID | None] = mapped_column(ForeignKey("source_system.id"))
    alias_type: Mapped[str] = mapped_column(String(50))
    alias_value: Mapped[str] = mapped_column(String(500))
    __table_args__ = (UniqueConstraint("source_system_id", "alias_type", "alias_value"),)


class VariantGene(Base):
    __tablename__ = "variant_gene"
    variant_id: Mapped[UUID] = mapped_column(ForeignKey("variant.id"), primary_key=True)
    gene_id: Mapped[UUID] = mapped_column(ForeignKey("gene.id"), primary_key=True)
    relationship_type: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_system_id: Mapped[UUID | None] = mapped_column(ForeignKey("source_system.id"))


class Study(Base):
    __tablename__ = "study"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    accession: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    pubmed_id: Mapped[str | None] = mapped_column(String(30), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(300))
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sample_description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)


class Population(Base):
    __tablename__ = "population"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    label: Mapped[str] = mapped_column(String(300), index=True)
    ancestry_label: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)


class StudyPopulation(Base):
    __tablename__ = "study_population"
    study_id: Mapped[UUID] = mapped_column(ForeignKey("study.id"), primary_key=True)
    population_id: Mapped[UUID] = mapped_column(ForeignKey("population.id"), primary_key=True)
    stage: Mapped[str] = mapped_column(String(50), primary_key=True, default="unknown")
    sample_size: Mapped[int | None] = mapped_column(BigInteger)


class Association(Base):
    __tablename__ = "association"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    variant_id: Mapped[UUID] = mapped_column(ForeignKey("variant.id"), index=True)
    trait_id: Mapped[UUID] = mapped_column(ForeignKey("trait.id"), index=True)
    study_id: Mapped[UUID | None] = mapped_column(ForeignKey("study.id"), index=True)
    population_id: Mapped[UUID | None] = mapped_column(ForeignKey("population.id"))
    identity_key: Mapped[str | None] = mapped_column(String(64))
    p_value: Mapped[Decimal | None] = mapped_column(Numeric())
    p_value_text: Mapped[str | None] = mapped_column(Text)
    p_value_status: Mapped[str] = mapped_column(
        String(30), default="missing", server_default="missing"
    )
    effect_allele: Mapped[str | None] = mapped_column(Text)
    other_allele: Mapped[str | None] = mapped_column(Text)
    effect_size: Mapped[Decimal | None] = mapped_column(Numeric())
    effect_size_type: Mapped[str | None] = mapped_column(String(80))
    confidence_interval: Mapped[str | None] = mapped_column(String(200))
    sample_size: Mapped[int | None] = mapped_column(BigInteger)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint("identity_key", name="uq_association_identity"),
        CheckConstraint(
            "p_value IS NULL OR (p_value > 0 AND p_value <= 1)", name="ck_association_p_value"
        ),
        CheckConstraint(
            "p_value_status IN ('reported', 'missing', 'unreconciled')",
            name="ck_association_p_value_status",
        ),
        CheckConstraint(
            "p_value_status != 'unreconciled' OR p_value IS NULL",
            name="ck_association_unreconciled",
        ),
        CheckConstraint(
            "(p_value_status = 'reported' AND p_value IS NOT NULL) OR "
            "(p_value_status != 'reported' AND p_value IS NULL)",
            name="ck_association_p_value_consistency",
        ),
    )


class AssociationSource(Base):
    __tablename__ = "association_source"
    association_id: Mapped[UUID] = mapped_column(ForeignKey("association.id"), primary_key=True)
    source_system_id: Mapped[UUID] = mapped_column(ForeignKey("source_system.id"), primary_key=True)
    source_record_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    ingestion_run_id: Mapped[UUID] = mapped_column(ForeignKey("ingestion_run.id"))
    raw_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint(
            "source_system_id", "source_record_id", name="uq_association_source_record"
        ),
        ForeignKeyConstraint(
            ["raw_object_id", "ingestion_run_id", "source_record_id"],
            [
                "raw_object.id",
                "raw_object.ingestion_run_id",
                "raw_object.source_record_id",
            ],
            name="fk_association_source_raw_run",
        ),
        ForeignKeyConstraint(
            ["ingestion_run_id", "source_system_id"],
            ["ingestion_run.id", "ingestion_run.source_system_id"],
            name="fk_association_source_run_source",
        ),
        Index("ix_association_source_raw", "raw_object_id"),
    )


class QualityIssue(Base):
    __tablename__ = "quality_issue"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ingestion_run_id: Mapped[UUID] = mapped_column(ForeignKey("ingestion_run.id"))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_key: Mapped[str | None] = mapped_column(String(300))
    rule_code: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    details_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    raw_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    processing_observation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    enrichment_observation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["raw_object_id", "ingestion_run_id"],
            ["raw_object.id", "raw_object.ingestion_run_id"],
            name="fk_quality_issue_raw_run",
        ),
        UniqueConstraint("raw_object_id", "rule_code", name="uq_quality_issue_raw_rule"),
        CheckConstraint("severity IN ('warning', 'error')", name="ck_quality_issue_severity"),
        ForeignKeyConstraint(
            ["processing_observation_id", "ingestion_run_id"],
            ["processing_observation.id", "processing_observation.ingestion_run_id"],
            name="fk_quality_issue_processing_run",
        ),
        UniqueConstraint(
            "processing_observation_id", "rule_code", name="uq_quality_issue_processing_rule"
        ),
        ForeignKeyConstraint(
            ["enrichment_observation_id", "ingestion_run_id"],
            ["enrichment_observation.id", "enrichment_observation.ingestion_run_id"],
            name="fk_quality_issue_enrichment_run",
        ),
        UniqueConstraint(
            "enrichment_observation_id", "rule_code", name="uq_quality_issue_enrichment_rule"
        ),
    )


class ProcessingObservation(Base):
    __tablename__ = "processing_observation"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ingestion_run_id: Mapped[UUID] = mapped_column(ForeignKey("ingestion_run.id"), index=True)
    raw_object_id: Mapped[UUID] = mapped_column(ForeignKey("raw_object.id"), index=True)
    association_id: Mapped[UUID | None] = mapped_column(ForeignKey("association.id"))
    disposition: Mapped[str] = mapped_column(String(30))
    normalized_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    __table_args__ = (
        UniqueConstraint("ingestion_run_id", "raw_object_id", name="uq_processing_run_raw"),
        UniqueConstraint("id", "ingestion_run_id", name="uq_processing_id_run"),
        CheckConstraint(
            "disposition IN ('loaded', 'partial', 'quarantined')", name="ck_processing_disposition"
        ),
    )


class IngestionBatch(Base):
    __tablename__ = "ingestion_batch"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ingestion_run_id: Mapped[UUID] = mapped_column(ForeignKey("ingestion_run.id"), index=True)
    sequence: Mapped[int] = mapped_column(BigInteger)
    phase: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    metrics_json: Mapped[dict] = mapped_column(JSONB)
    checkpoint_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    __table_args__ = (
        UniqueConstraint("ingestion_run_id", "sequence", name="uq_ingestion_batch_sequence"),
        CheckConstraint("phase IN ('capture', 'processing')", name="ck_ingestion_batch_phase"),
        CheckConstraint("status IN ('completed', 'failed')", name="ck_ingestion_batch_status"),
    )
