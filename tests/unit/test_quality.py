from decimal import Decimal

from genomic_platform.domain.schemas import AssociationDTO, TraitDTO
from genomic_platform.quality.rules import validate_association


def test_invalid_pvalue():
    a=AssociationDTO(source_record_id="x",variant_rsid="rs1",trait=TraitDTO(name="eye color"),p_value=Decimal("1.5"))
    assert any(f.code=="ASSOC_INVALID_PVALUE" for f in validate_association(a))
