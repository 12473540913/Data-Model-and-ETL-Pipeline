from dataclasses import dataclass
from decimal import Decimal
from genomic_platform.domain.schemas import AssociationDTO, VariantDTO

@dataclass
class QualityFinding:
    code: str; severity: str; message: str

def validate_variant(v: VariantDTO) -> list[QualityFinding]:
    out=[]
    if not v.rsid: out.append(QualityFinding("VARIANT_MISSING_RSID","error","Variant has no normalized rsID."))
    if v.position is not None and v.position <= 0: out.append(QualityFinding("VARIANT_INVALID_POSITION","error","Genomic position must be positive."))
    if v.assembly != "GRCh38": out.append(QualityFinding("VARIANT_NONPRIMARY_ASSEMBLY","warning",f"Expected GRCh38, found {v.assembly}."))
    return out

def validate_association(a: AssociationDTO) -> list[QualityFinding]:
    out=[]
    if not a.variant_rsid: out.append(QualityFinding("ASSOC_MISSING_VARIANT","warning","Association lacks normalized rsID."))
    if a.p_value is not None and not (Decimal("0") <= a.p_value <= Decimal("1")): out.append(QualityFinding("ASSOC_INVALID_PVALUE","error",f"Invalid p-value {a.p_value}."))
    if not a.trait.name: out.append(QualityFinding("ASSOC_MISSING_TRAIT","error","Association lacks normalized trait."))
    return out
