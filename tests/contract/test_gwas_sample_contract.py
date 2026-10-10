from decimal import Decimal

import pytest

from genomic_platform.etl.normalize import gwas_record_to_association
from genomic_platform.quality.rules import validate_association


def test_gwas_transformer_accepts_sample_shape():
    record = {
        "id": "assoc-1",
        "rsId": "rs12913832",
        "efoTrait": "eye color",
        "pValue": "1e-20",
        "studyAccession": "GCST-DEMO",
    }
    association = gwas_record_to_association(record)
    assert association.variant_rsid == "rs12913832"
    assert association.trait.name == "eye color"
    assert association.trait.source_name is None
    assert association.p_value == Decimal("1e-20")


def test_saved_dataset_shape(gwas_saved_records):
    assert len(gwas_saved_records) == 68
    assert len({r["reported_trait"][0] for r in gwas_saved_records}) == 51
    assert sum(len(r["efo_traits"]) > 1 for r in gwas_saved_records) == 4


@pytest.mark.parametrize("index", range(68))
def test_every_saved_association_preserves_source_fields(gwas_saved_records, index):
    record = gwas_saved_records[index]
    association = gwas_record_to_association(record)
    assert association.raw == record
    assert association.source_record_id == str(record["association_id"])
    assert association.reported_traits == record["reported_trait"]
    assert [(t.ontology_id, t.name) for t in association.traits] == [
        (t["efo_id"], t["efo_trait"]) for t in record["efo_traits"]
    ]
    assert all(t.source_name == record["reported_trait"][0] for t in association.traits)
    if len(record["efo_traits"]) == 1:
        assert association.trait == association.traits[0]
    else:
        assert association.trait is None
    assert association.variant_rsid == "rs4988235"
    allele = record["snp_allele"][0]["effect_allele"]
    assert association.effect_allele == (None if allele == "?" else allele)
    assert association.variants[0].effect_allele == association.effect_allele
    assert association.other_allele is None
    assert association.study.accession == record["accession_id"]
    assert association.study.pubmed_id == record["pubmed_id"]
    assert association.study.author == record["first_author"]
    assert association.study.title is None
    exact = Decimal(f"{record['pvalue_mantissa']}e{record['pvalue_exponent']}")
    assert association.p_value == exact
    assert association.p_value > 0
    assert Decimal(association.p_value_text) == exact
    if record["beta"] == "-":
        assert association.effect_size is None
        assert association.effect_size_type is None
    else:
        number, remainder = record["beta"].split(" ", 1)
        unit, direction = remainder.rsplit(" ", 1)
        assert association.effect_size == Decimal(number)
        assert association.effect_size_type == "beta"
        assert association.effect_size_text == record["beta"]
        assert association.effect_size_unit == unit
        assert association.effect_direction == direction
    assert association.confidence_interval == (
        None if record["range"] in ("", "-") else record["range"]
    )
    findings = validate_association(association)
    assert {f.code for f in findings if f.severity == "error"} == (
        {"ASSOC_CONFLICTING_PVALUE"} if record["p_value"] != exact else set()
    )
    codes = {f.code for f in findings}
    assert ("ASSOC_MULTIPLE_TRAITS" in codes) == (len(record["efo_traits"]) > 1)
    assert ("ASSOC_MISSING_EFFECT_ALLELE" in codes) == (allele == "?")
    assert ("ASSOC_MISSING_EFFECT_SIZE" in codes) == (record["beta"] == "-")


def test_saved_pvalue_inconsistencies_are_explicit(gwas_saved_records):
    associations = [gwas_record_to_association(r) for r in gwas_saved_records]
    assert (
        sum(
            any(f.code == "ASSOC_CONFLICTING_PVALUE" for f in validate_association(a))
            for a in associations
        )
        == 9
    )
    underflowed = [
        (r, a) for r, a in zip(gwas_saved_records, associations, strict=True) if r["p_value"] == 0
    ]
    assert len(underflowed) == 1
    record, association = underflowed[0]
    assert association.p_value == Decimal("3e-1451") > 0
    assert association.raw["p_value"] == record["p_value"] == 0
    assert association.p_value_text == "3e-1451"
