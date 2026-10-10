from dataclasses import dataclass
from decimal import Decimal

from genomic_platform.domain.schemas import AssociationDTO, VariantDTO


@dataclass
class QualityFinding:
    code: str
    severity: str
    message: str


def validate_variant(v: VariantDTO) -> list[QualityFinding]:
    out = []
    if not v.rsid:
        out.append(
            QualityFinding("VARIANT_MISSING_RSID", "error", "Variant has no normalized rsID.")
        )
    if v.position is not None and v.position <= 0:
        out.append(
            QualityFinding(
                "VARIANT_INVALID_POSITION", "error", "Genomic position must be positive."
            )
        )
    if v.assembly != "GRCh38":
        out.append(
            QualityFinding(
                "VARIANT_NONPRIMARY_ASSEMBLY", "warning", f"Expected GRCh38, found {v.assembly}."
            )
        )
    return out


def validate_association(a: AssociationDTO) -> list[QualityFinding]:
    out = [QualityFinding(f.code, f.severity, f.message) for f in a.normalization_findings]
    if not a.source_record_id:
        out.append(
            QualityFinding("ASSOC_MISSING_SOURCE_ID", "error", "Missing source association ID.")
        )
    variants = a.variants
    if not a.variant_rsid and (not variants or any(not v.rsid for v in variants)):
        out.append(QualityFinding("ASSOC_MISSING_VARIANT", "error", "Missing normalized rsID."))
    if len(variants) > 1:
        out.append(
            QualityFinding(
                "ASSOC_MULTIPLE_VARIANTS",
                "warning",
                "Multiple variant/allele mappings retained; resolve before canonical loading.",
            )
        )
    if (variants and any(not v.effect_allele for v in variants)) or (
        not variants and not a.effect_allele
    ):
        out.append(
            QualityFinding("ASSOC_MISSING_EFFECT_ALLELE", "warning", "Effect allele is unknown.")
        )
    if a.p_value is None:
        out.append(
            QualityFinding("ASSOC_MISSING_PVALUE", "warning", "P-value is missing or invalid.")
        )
    elif not a.p_value.is_finite() or not (Decimal(0) < a.p_value <= Decimal(1)):
        out.append(
            QualityFinding("ASSOC_INVALID_PVALUE", "error", f"Invalid exact p-value {a.p_value}.")
        )
    traits = a.traits or ([a.trait] if a.trait is not None else [])
    if not traits or any(not t.name for t in traits):
        out.append(QualityFinding("ASSOC_MISSING_TRAIT", "error", "Missing trait name."))
    if traits and any(not t.ontology_id for t in traits):
        out.append(
            QualityFinding("ASSOC_MISSING_ONTOLOGY", "warning", "Trait lacks an ontology ID.")
        )
    if len(traits) > 1:
        out.append(
            QualityFinding(
                "ASSOC_MULTIPLE_TRAITS",
                "warning",
                "Multiple ontology traits retained; no single canonical trait selected.",
            )
        )
    if a.study is None or not a.study.accession:
        out.append(QualityFinding("ASSOC_MISSING_STUDY", "warning", "Study accession is missing."))
    if a.effect_size is None:
        out.append(
            QualityFinding(
                "ASSOC_MISSING_EFFECT_SIZE", "warning", "Effect estimate is missing or unresolved."
            )
        )
    return out
