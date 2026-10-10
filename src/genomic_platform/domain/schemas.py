from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class VariantDTO(BaseModel):
    rsid: str | None = None
    assembly: str = "GRCh38"
    chromosome: str | None = None
    position: int | None = None
    reference_allele: str | None = None
    alternate_alleles: list[str] = Field(default_factory=list)
    variant_class: str | None = None
    most_severe_consequence: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraitDTO(BaseModel):
    ontology_id: str | None = None
    name: str | None = None
    source_name: str | None = None


class StudyDTO(BaseModel):
    accession: str | None = None
    pubmed_id: str | None = None
    title: str | None = None
    author: str | None = None
    sample_description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizationFinding(BaseModel):
    code: str
    severity: str
    message: str


class AssociationVariantDTO(BaseModel):
    rsid: str | None = None
    effect_allele: str | None = None


class AssociationDTO(BaseModel):
    source_record_id: str | None = None
    variant_rsid: str | None = None
    variants: list[AssociationVariantDTO] = Field(default_factory=list)
    trait: TraitDTO | None = None
    traits: list[TraitDTO] = Field(default_factory=list)
    reported_traits: list[str] = Field(default_factory=list)
    study: StudyDTO | None = None
    p_value: Decimal | None = None
    p_value_text: str | None = None
    effect_allele: str | None = None
    other_allele: str | None = None
    effect_size: Decimal | None = None
    effect_size_type: str | None = None
    effect_size_text: str | None = None
    effect_size_unit: str | None = None
    effect_direction: str | None = None
    confidence_interval: str | None = None
    normalization_findings: list[NormalizationFinding] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
