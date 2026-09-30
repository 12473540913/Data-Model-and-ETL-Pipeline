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
    name: str
    source_name: str | None = None

class StudyDTO(BaseModel):
    accession: str | None = None
    pubmed_id: str | None = None
    title: str | None = None
    author: str | None = None
    sample_description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class AssociationDTO(BaseModel):
    source_record_id: str
    variant_rsid: str | None = None
    trait: TraitDTO
    study: StudyDTO | None = None
    p_value: Decimal | None = None
    effect_allele: str | None = None
    other_allele: str | None = None
    effect_size: Decimal | None = None
    effect_size_type: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
