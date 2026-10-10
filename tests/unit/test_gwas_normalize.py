from decimal import Decimal, localcontext

import pytest

from genomic_platform.etl.normalize import gwas_record_to_association
from genomic_platform.quality.rules import validate_association


def codes(association):
    return {f.code for f in validate_association(association)}


def test_missing_fields_remain_missing():
    association = gwas_record_to_association({})
    assert association.source_record_id is None
    assert association.variant_rsid is None
    assert association.variants == []
    assert association.trait is None
    assert association.traits == []
    assert association.reported_traits == []
    assert association.study is None
    assert association.p_value is None
    assert association.effect_size is None
    assert association.effect_allele is None
    assert {
        "ASSOC_MISSING_SOURCE_ID",
        "ASSOC_MISSING_VARIANT",
        "ASSOC_MISSING_TRAIT",
        "ASSOC_MISSING_PVALUE",
        "ASSOC_MISSING_STUDY",
    } <= codes(association)


def test_reported_only_trait_has_no_invented_ontology():
    association = gwas_record_to_association({"reported_trait": ["Lactase activity"]})
    assert association.trait.name == "Lactase activity"
    assert association.trait.source_name == "Lactase activity"
    assert association.trait.ontology_id is None
    assert "ASSOC_MISSING_ONTOLOGY" in codes(association)


def test_multiple_variants_and_reported_traits_are_not_flattened():
    association = gwas_record_to_association(
        {
            "snp_allele": [
                {"rs_id": "RS00123", "effect_allele": "A"},
                {"rs_id": "rs456", "effect_allele": "G"},
            ],
            "reported_trait": ["Body weight", "LDL cholesterol"],
        }
    )
    assert association.variant_rsid is None
    assert association.effect_allele is None
    assert [(v.rsid, v.effect_allele) for v in association.variants] == [
        ("rs123", "A"),
        ("rs456", "G"),
    ]
    assert association.trait is None
    assert association.reported_traits == ["Body weight", "LDL cholesterol"]
    assert all(t.source_name == t.name for t in association.traits)
    assert "ASSOC_MULTIPLE_VARIANTS" in codes(association)
    assert "ASSOC_MULTIPLE_TRAITS" in codes(association)
    assert "ASSOC_MISSING_VARIANT" not in codes(association)


def test_multiple_reported_traits_are_not_assigned_to_a_single_ontology_name():
    association = gwas_record_to_association(
        {
            "efo_traits": [{"efo_id": "EFO_0004338", "efo_trait": "body weight"}],
            "reported_trait": ["Weight minimum", "Weight maximum"],
        }
    )
    assert association.trait.name == "body weight"
    assert association.trait.source_name is None
    assert association.reported_traits == ["Weight minimum", "Weight maximum"]


def test_nested_study_and_risk_allele_string():
    association = gwas_record_to_association(
        {
            "snp_effect_allele": ["rs4988235-G"],
            "study": {"accession": "GCST123", "pubmedId": "123", "title": "Study title"},
        }
    )
    assert association.variant_rsid == "rs4988235"
    assert association.effect_allele == "G"
    assert association.study.accession == "GCST123"
    assert association.study.pubmed_id == "123"
    assert association.study.title == "Study title"


def test_partial_variant_representations_do_not_create_false_ambiguity():
    association = gwas_record_to_association(
        {
            "rs_id": "rs123",
            "snp_allele": [{"rs_id": "rs123", "effect_allele": "?"}],
            "snp_effect_allele": ["rs123-A"],
        }
    )
    assert association.variant_rsid == "rs123"
    assert association.effect_allele == "A"
    assert len(association.variants) == 1
    assert "ASSOC_CONFLICTING_FIELDS" not in codes(association)


def test_effect_allele_without_variant_is_still_retained():
    association = gwas_record_to_association({"effect_allele": "A"})
    assert association.effect_allele == "A"
    assert association.variant_rsid is None
    assert "ASSOC_MISSING_VARIANT" in codes(association)


@pytest.mark.parametrize("value", ["bad", "NaN", "Infinity", "-Infinity", [], {}])
def test_invalid_pvalue_is_explicit(value):
    association = gwas_record_to_association({"p_value": value})
    assert association.p_value is None
    assert "ASSOC_INVALID_NUMBER" in codes(association)


@pytest.mark.parametrize("value", ["0", "-1e-65", "1.01"])
def test_out_of_range_pvalue_is_not_accepted(value):
    association = gwas_record_to_association({"p_value": value})
    assert "ASSOC_INVALID_PVALUE" in codes(association)


@pytest.mark.parametrize("value", ["1e-65", "4e-85", "1e-400"])
def test_tiny_pvalues_are_exact(value):
    association = gwas_record_to_association({"p_value": value})
    assert association.p_value == Decimal(value) > 0
    assert association.p_value_text == value


def test_mantissa_exponent_does_not_round_under_low_decimal_precision():
    with localcontext() as context:
        context.prec = 3
        association = gwas_record_to_association(
            {
                "pvalue_mantissa": "1.234567890123456789",
                "pvalue_exponent": -400,
            }
        )
    assert association.p_value == Decimal("1.234567890123456789e-400")


@pytest.mark.parametrize(
    "record",
    [
        {"pvalue_mantissa": 1},
        {"pvalue_exponent": -65},
        {"pvalue_mantissa": 1, "pvalue_exponent": "-65.5"},
        {"pvalue_mantissa": 1, "pvalue_exponent": "invalid"},
    ],
)
def test_incomplete_or_invalid_scientific_pair_is_flagged(record):
    association = gwas_record_to_association(record)
    assert association.p_value is None
    assert "ASSOC_INVALID_PVALUE" in codes(association)


def test_conflicting_pvalues_are_flagged_not_silently_replaced():
    association = gwas_record_to_association(
        {
            "p_value": "1e-8",
            "pvalue_mantissa": 1,
            "pvalue_exponent": -65,
        }
    )
    assert association.p_value == Decimal("1e-65")
    assert "ASSOC_CONFLICTING_PVALUE" in codes(association)


def test_source_zero_with_scientific_pair_is_recovered_and_flagged():
    association = gwas_record_to_association(
        {
            "p_value": 0.0,
            "pvalue_mantissa": 3,
            "pvalue_exponent": -1451,
        }
    )
    assert association.p_value == Decimal("3e-1451") > 0
    assert "ASSOC_CONFLICTING_PVALUE" in codes(association)


def test_invalid_rsid_is_not_replaced_with_queried_variant():
    association = gwas_record_to_association(
        {
            "snp_allele": [{"rs_id": "invalid", "effect_allele": "A"}],
        }
    )
    assert association.variant_rsid is None
    assert "ASSOC_INVALID_RSID" in codes(association)
    assert "ASSOC_MISSING_VARIANT" in codes(association)


def test_float_precision_is_explicit_when_source_pair_is_unavailable():
    association = gwas_record_to_association({"p_value": 1e-65})
    assert association.p_value == Decimal("1e-65")
    assert "ASSOC_FLOAT_PVALUE" in codes(association)


@pytest.mark.parametrize("value", ["?", "NR", "NA", "-", ""])
def test_unknown_alleles_remain_missing(value):
    association = gwas_record_to_association(
        {
            "snp_allele": [{"rs_id": "rs4988235", "effect_allele": value}],
        }
    )
    assert association.effect_allele is None
    assert "ASSOC_MISSING_EFFECT_ALLELE" in codes(association)


def test_malformed_nested_fields_are_not_stringified():
    association = gwas_record_to_association(
        {
            "snp_allele": ["not an object"],
            "efo_traits": [{"efo_id": ["EFO_123"], "efo_trait": {"name": "weight"}}],
            "reported_trait": [{"name": "weight"}],
            "study": ["GCST123"],
        }
    )
    assert association.variant_rsid is None
    assert association.trait.name is None
    assert association.trait.ontology_id is None
    assert association.reported_traits == []
    assert association.study is None
    assert "ASSOC_INVALID_FIELD" in codes(association)
    assert "ASSOC_MISSING_TRAIT" in codes(association)


def test_conflicting_alleles_preserve_both_mappings():
    association = gwas_record_to_association(
        {
            "snp_allele": [{"rs_id": "rs123", "effect_allele": "A"}],
            "snp_effect_allele": ["rs123-G"],
        }
    )
    assert association.variant_rsid is None
    assert [v.effect_allele for v in association.variants] == ["A", "G"]
    assert "ASSOC_CONFLICTING_FIELDS" in codes(association)


def test_conflicting_study_aliases_are_flagged():
    association = gwas_record_to_association(
        {
            "accession_id": "GCST1",
            "study": {"accession": "GCST2"},
        }
    )
    assert "ASSOC_CONFLICTING_FIELDS" in codes(association)


@pytest.mark.parametrize(
    "record, expected, kind, unit, direction",
    [
        ({"beta": "0.05121 unit decrease"}, "0.05121", "beta", "unit", "decrease"),
        ({"beta": "7.4256573 z score increase"}, "7.4256573", "beta", "z score", "increase"),
        ({"beta": "0.016 kg/m2 increase"}, "0.016", "beta", "kg/m2", "increase"),
        ({"beta": "-0.02"}, "-0.02", "beta", None, None),
        ({"or_per_copy_num": "1.25"}, "1.25", "odds_ratio", None, None),
        ({"effect_size": "0.12", "effect_size_type": "beta"}, "0.12", "beta", None, None),
    ],
)
def test_effect_estimates_preserve_number_unit_and_direction(
    record, expected, kind, unit, direction
):
    association = gwas_record_to_association(record)
    assert association.effect_size == Decimal(expected)
    assert association.effect_size_type == kind
    assert association.effect_size_unit == unit
    assert association.effect_direction == direction


def test_ambiguous_or_unparseable_effects_are_explicit():
    association = gwas_record_to_association({"beta": "0.1", "or_per_copy_num": "1.2"})
    assert association.effect_size is None
    assert "ASSOC_AMBIGUOUS_EFFECT" in codes(association)
    association = gwas_record_to_association({"beta": "not a numeric estimate"})
    assert association.effect_size is None
    assert association.effect_size_text == "not a numeric estimate"
    assert "ASSOC_INVALID_NUMBER" in codes(association)
