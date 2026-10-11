from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer


class View(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", check_fields=False)
    def serialize_fields(self, value):
        return value.isoformat() if isinstance(value, datetime) else value


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok", "ready"]


class TraitView(View):
    id: str
    ontology_id: str | None
    name: str
    description: str | None
    category: str | None


class StudyView(View):
    id: str
    accession: str | None
    pubmed_id: str | None
    title: str | None
    author: str | None
    publication_date: datetime | None
    sample_description: str | None


class GeneView(View):
    id: str
    ensembl_gene_id: str | None
    symbol: str
    name: str | None
    chromosome: str | None


class VariantView(View):
    id: str
    rsid: str | None
    assembly: str | None
    chromosome: str | None
    position: int | None
    reference_allele: str | None
    alternate_alleles: list[str]
    variant_class: str | None
    most_severe_consequence: str | None
    projection_mapping_id: str | None
    enrichment_status: str | None


class AssociationView(View):
    id: str
    variant_id: str
    trait_id: str
    study_id: str | None
    variant_rsid: str | None
    trait: TraitView
    study: StudyView | None
    p_value: str | None
    p_value_status: str
    p_value_text: str | None
    effect_allele: str | None
    other_allele: str | None
    effect_size: str | None
    effect_size_type: str | None
    effect_size_text: str | None
    effect_size_unit: str | None
    effect_direction: str | None
    confidence_interval: str | None
    sample_size: int | None
    quality_status: str


class SourceView(View):
    association_id: str
    source_code: str
    source_record_id: str
    ingestion_run_id: str
    raw_object_id: str | None
    sha256: str | None


class RawView(View):
    id: str
    ingestion_run_id: str
    source_code: str
    source_record_type: str
    source_record_id: str | None
    created_at: datetime
    sha256: str
    disposition: str
    association_id: str | None
    p_value_candidate: str | None
    p_value_text: str | None
    p_value_mantissa: str | None
    p_value_exponent: str | None
    reported_p_value_text: str | None


class QualityView(View):
    id: str
    ingestion_run_id: str
    raw_object_id: str | None
    processing_observation_id: str | None
    enrichment_observation_id: str | None
    entity_type: str
    entity_key: str | None
    rule_code: str
    severity: str
    message: str


class MappingView(View):
    id: str
    variant_id: str
    enrichment_id: str
    raw_object_id: str
    source_code: str
    requested_assembly: str
    ordinal: int
    assembly: str | None
    coordinate_system: str | None
    chromosome: str | None
    position: int | None
    end_position: int | None
    strand: int | None
    valid: bool
    allele_string: str | None
    ancestral_allele: str | None
    sha256: str


class EnrichmentView(View):
    id: str
    ingestion_run_id: str
    variant_id: str
    enrichment_id: str | None
    raw_object_id: str | None
    source_code: str
    requested_assembly: str
    lookup_identifier: str
    retrieved_at: datetime
    status: str
    sha256: str | None


class ProcessingView(View):
    id: str
    ingestion_run_id: str
    raw_object_id: str
    association_id: str | None
    disposition: str
    created_at: datetime


class RunView(View):
    id: str
    source_code: str
    pipeline_name: str
    originating_run_id: str | None
    started_at: datetime
    finished_at: datetime | None
    status: str


class Page(View):
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None


class AssociationPage(Page):
    items: list[AssociationView]


class SourcePage(Page):
    items: list[SourceView]


class RawPage(Page):
    items: list[RawView]


class QualityPage(Page):
    items: list[QualityView]


class MappingPage(Page):
    items: list[MappingView]


class EnrichmentPage(Page):
    items: list[EnrichmentView]


class ProcessingPage(Page):
    items: list[ProcessingView]


CONTRACTS = (
    TraitView,
    StudyView,
    GeneView,
    VariantView,
    AssociationView,
    SourceView,
    RawView,
    QualityView,
    MappingView,
    EnrichmentView,
    ProcessingView,
    RunView,
    AssociationPage,
    SourcePage,
    RawPage,
    QualityPage,
    MappingPage,
    EnrichmentPage,
    ProcessingPage,
)
