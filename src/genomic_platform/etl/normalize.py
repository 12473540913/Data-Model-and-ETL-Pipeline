import re
from decimal import Decimal, InvalidOperation
from typing import Any

from genomic_platform.domain.schemas import (
    AssociationDTO,
    AssociationVariantDTO,
    NormalizationFinding,
    StudyDTO,
    TraitDTO,
    VariantDTO,
)
from genomic_platform.etl.ensembl_normalization import forward_snp_alleles, normalize_ensembl


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    value = " ".join(str(value).split()).strip()
    return value or None


def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "NA", "NR"):
        return None
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def normalize_rsid(value: Any) -> str | None:
    value = clean_text(value)
    if not value:
        return None
    lowered = value.lower()
    if lowered.startswith("rs") and lowered[2:].isdigit():
        return f"rs{int(lowered[2:])}"
    return None


def ensembl_to_variant(rsid: str, payload: dict, assembly: str = "GRCh38") -> VariantDTO:
    canonical = normalize_rsid(rsid)
    if canonical is None or assembly not in ("GRCh37", "GRCh38"):
        raise ValueError("Ensembl normalization requires a valid rsID and target assembly.")
    result = normalize_ensembl(payload, canonical, assembly)
    preferred = (
        result.candidates[0]
        if len(result.candidates) == 1
        and not any(
            not m.valid
            and (
                m.assembly is None
                or (m.assembly == assembly and m.coordinate_system == "chromosome")
            )
            for m in result.mappings
        )
        else None
    )
    alleles = forward_snp_alleles(preferred) if preferred is not None else None
    return VariantDTO(
        rsid=canonical,
        assembly=preferred.assembly if preferred else None,
        chromosome=preferred.chromosome if preferred else None,
        position=preferred.position if preferred else None,
        reference_allele=alleles[0] if alleles else None,
        alternate_alleles=alleles[1] if alleles else [],
        variant_class=result.annotations.get("var_class"),
        most_severe_consequence=result.annotations.get("most_severe_consequence"),
        metadata={
            "ensembl_evidence": payload.get("evidence") or [],
            "ancestral_allele": preferred.payload.get("ancestral_allele") if preferred else None,
            "all_mappings": payload.get("mappings", []),
            "enrichment_findings": result.findings,
        },
    )


_MISSING_TEXT = {"", "NA", "NR", "N/A", "?", "-"}
_BETA_PATTERN = re.compile(
    r"(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?:\s+(?P<unit>.+?)\s+(?P<direction>increase|decrease))?"
)


class _GWASFields:
    def __init__(self) -> None:
        self.findings: list[NormalizationFinding] = []

    def issue(self, code: str, message: str, severity: str = "error") -> None:
        self.findings.append(NormalizationFinding(code=code, severity=severity, message=message))

    def text(self, value: Any, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
            self.issue("ASSOC_INVALID_FIELD", f"{field}: expected scalar text, got {value!r}.")
            return None
        text = clean_text(value)
        return None if text is None or text.upper() in _MISSING_TEXT else text

    def first(self, record: dict, *keys: str) -> Any:
        values = [
            (key, record[key])
            for key in keys
            if key in record and record[key] is not None and record[key] != ""
        ]
        if not values:
            return None
        if any(value != values[0][1] for _, value in values[1:]):
            self.issue("ASSOC_CONFLICTING_FIELDS", f"Conflicting aliases: {values!r}.")
        return values[0][1]

    def items(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else ([] if value is None else [value])

    def decimal(self, value: Any, field: str) -> Decimal | None:
        if value is None or (isinstance(value, str) and value.strip().upper() in _MISSING_TEXT):
            return None
        parsed = parse_decimal(value)
        if parsed is None:
            self.issue("ASSOC_INVALID_NUMBER", f"{field}: invalid finite decimal {value!r}.")
        return parsed


def _gwas_variants(record: dict, fields: _GWASFields) -> list[AssociationVariantDTO]:
    variants: list[AssociationVariantDTO] = []

    def add(rsid_value: Any, allele_value: Any, field: str) -> None:
        text = fields.text(rsid_value, f"{field}.rsid")
        rsid = normalize_rsid(text)
        if text and rsid is None:
            fields.issue("ASSOC_INVALID_RSID", f"{field}: invalid rsID {text!r}.")
        allele = fields.text(allele_value, f"{field}.effect_allele")
        variant = AssociationVariantDTO(rsid=rsid, effect_allele=allele)
        matching = [v for v in variants if rsid is not None and v.rsid == rsid]
        if allele is None and matching:
            return
        if len(matching) == 1 and matching[0].effect_allele is None:
            variants[variants.index(matching[0])] = variant
            return
        if variant not in variants:
            variants.append(variant)

    for item in fields.items(record.get("snp_allele")):
        if not isinstance(item, dict):
            fields.issue("ASSOC_INVALID_FIELD", f"snp_allele: expected object, got {item!r}.")
            continue
        add(fields.first(item, "rs_id", "rsId"), item.get("effect_allele"), "snp_allele")

    for item in fields.items(record.get("snp_effect_allele")):
        text = fields.text(item, "snp_effect_allele")
        if text is None:
            continue
        rsid, separator, allele = text.partition("-")
        candidate = AssociationVariantDTO(
            rsid=normalize_rsid(rsid),
            effect_allele=fields.text(allele, "snp_effect_allele") if separator else None,
        )
        matching = [v for v in variants if v.rsid == candidate.rsid]
        if (
            matching
            and candidate.effect_allele is not None
            and all(
                v.effect_allele is not None and v.effect_allele != candidate.effect_allele
                for v in matching
            )
        ):
            fields.issue("ASSOC_CONFLICTING_FIELDS", f"Conflicting SNP alleles for {rsid}.")
        add(rsid, allele if separator else None, "snp_effect_allele")

    flat = fields.first(record, "rs_id", "rsId", "variant_id", "variantId", "snp")
    allele = fields.first(record, "effect_allele", "risk_allele")
    for item in fields.items(flat) or ([None] if allele is not None else []):
        add(item, allele, "variant")
    return variants


def _gwas_traits(record: dict, fields: _GWASFields) -> tuple[list[TraitDTO], list[str]]:
    reported = []
    for value in fields.items(fields.first(record, "reported_trait", "reportedTrait")):
        text = fields.text(value, "reported_trait")
        if text and text not in reported:
            reported.append(text)
    traits: list[TraitDTO] = []
    mapped = fields.first(record, "efo_traits", "efoTraits")
    if mapped is None:
        name = fields.first(record, "efo_trait", "efoTrait", "trait")
        ontology_id = fields.first(record, "efo_id", "efoId", "trait_id")
        if name is not None or ontology_id is not None:
            mapped = [{"efo_trait": name, "efo_id": ontology_id}]
    for item in fields.items(mapped):
        if not isinstance(item, dict):
            fields.issue("ASSOC_INVALID_FIELD", f"efo_traits: expected object, got {item!r}.")
            continue
        trait = TraitDTO(
            ontology_id=fields.text(fields.first(item, "efo_id", "efoId"), "efo_id"),
            name=fields.text(fields.first(item, "efo_trait", "efoTrait", "name"), "efo_trait"),
            source_name=reported[0] if len(reported) == 1 else None,
        )
        if trait not in traits:
            traits.append(trait)
    if not traits:
        traits = [TraitDTO(name=name, source_name=name) for name in reported]
    return traits, reported


def _gwas_pvalue(record: dict, fields: _GWASFields) -> tuple[Decimal | None, str | None]:
    direct = fields.first(record, "p_value", "pValue", "pvalue")
    value = fields.decimal(direct, "p_value")
    mantissa_raw = fields.first(record, "pvalue_mantissa", "pValueMantissa")
    exponent_raw = fields.first(record, "pvalue_exponent", "pValueExponent")
    if mantissa_raw is not None or exponent_raw is not None:
        mantissa = fields.decimal(mantissa_raw, "pvalue_mantissa")
        exponent = fields.decimal(exponent_raw, "pvalue_exponent")
        if mantissa is None or exponent is None or exponent != exponent.to_integral_value():
            fields.issue(
                "ASSOC_INVALID_PVALUE", "P-value requires a mantissa and integer exponent."
            )
            return None, None
        parts = mantissa.as_tuple()
        # Construct the Decimal directly: arithmetic would use the active precision context.
        try:
            exact = Decimal((parts.sign, parts.digits, int(parts.exponent) + int(exponent)))
        except (InvalidOperation, OverflowError, ValueError):
            fields.issue("ASSOC_INVALID_PVALUE", "P-value exponent exceeds Decimal representation.")
            return None, None
        if value is not None and value != exact:
            fields.issue(
                "ASSOC_CONFLICTING_PVALUE", "P-value differs from source mantissa/exponent."
            )
        return exact, f"{mantissa}e{int(exponent)}"
    if isinstance(direct, float):
        fields.issue(
            "ASSOC_FLOAT_PVALUE",
            "P-value arrived as a float; original precision is unverified. "
            "Decode source JSON with parse_float=Decimal.",
            "warning",
        )
    return value, str(direct) if direct is not None else None


def _gwas_effect(record: dict, fields: _GWASFields) -> dict[str, Any]:
    candidates = []
    for key, kind in (("effect_size", None), ("or_per_copy_num", "odds_ratio"), ("beta", "beta")):
        raw = record.get(key)
        if raw is not None and not (isinstance(raw, str) and raw.strip().upper() in _MISSING_TEXT):
            candidates.append((key, kind, raw))
    if len(candidates) > 1:
        fields.issue(
            "ASSOC_AMBIGUOUS_EFFECT", "Multiple effect estimates supplied; no single estimate."
        )
        return {}
    if not candidates:
        return {}
    key, kind, raw = candidates[0]
    text = str(raw)
    unit = direction = None
    if key == "beta":
        match = _BETA_PATTERN.fullmatch(text.strip())
        if not match:
            fields.issue("ASSOC_INVALID_NUMBER", f"beta: unsupported estimate {raw!r}.")
            return {"effect_size_text": text}
        value = fields.decimal(match["number"], key)
        unit, direction = match["unit"], match["direction"]
        if value is not None and value < 0 and direction is not None:
            fields.issue(
                "ASSOC_AMBIGUOUS_EFFECT", "Negative beta magnitude also specifies direction."
            )
    else:
        value = fields.decimal(raw, key)
    explicit_kind = fields.text(
        fields.first(record, "effect_size_type", "effect_type"), "effect_size_type"
    )
    if explicit_kind and kind and explicit_kind != kind:
        fields.issue("ASSOC_CONFLICTING_FIELDS", f"{key} conflicts with effect_size_type.")
    return {
        "effect_size": value,
        "effect_size_type": explicit_kind or kind,
        "effect_size_text": text,
        "effect_size_unit": unit,
        "effect_direction": direction,
    }


def gwas_record_to_association(record: dict) -> AssociationDTO:
    """Preserve one source association without flattening ambiguous traits or variants."""
    fields = _GWASFields()
    variants = _gwas_variants(record, fields)
    traits, reported = _gwas_traits(record, fields)
    pvalue, pvalue_text = _gwas_pvalue(record, fields)
    study_record = record.get("study")
    if study_record is not None and not isinstance(study_record, dict):
        fields.issue("ASSOC_INVALID_FIELD", "study: expected an object.")
        study_record = None
    study_values = {}
    for target, aliases in {
        "accession": ("accession_id", "study_accession", "studyAccession", "accession"),
        "pubmed_id": ("pubmed_id", "pubmedId"),
        "title": ("study_title", "title"),
        "author": ("author", "first_author"),
        "sample_description": ("initial_sample_description", "sample_description"),
    }.items():
        flat = fields.text(fields.first(record, *aliases), f"study.{target}")
        nested = fields.text(fields.first(study_record or {}, *aliases), f"study.{target}")
        if flat and nested and flat != nested:
            fields.issue("ASSOC_CONFLICTING_FIELDS", f"Conflicting study.{target}.")
        study_values[target] = nested or flat
    return AssociationDTO(
        source_record_id=fields.text(
            fields.first(record, "association_id", "associationId", "id"), "association_id"
        ),
        variants=variants,
        variant_rsid=variants[0].rsid if len(variants) == 1 else None,
        effect_allele=variants[0].effect_allele if len(variants) == 1 else None,
        traits=traits,
        trait=traits[0] if len(traits) == 1 else None,
        reported_traits=reported,
        study=StudyDTO(**study_values) if any(study_values.values()) else None,
        p_value=pvalue,
        p_value_text=pvalue_text,
        other_allele=fields.text(record.get("other_allele"), "other_allele"),
        confidence_interval=fields.text(record.get("range"), "range"),
        **_gwas_effect(record, fields),
        normalization_findings=fields.findings,
        raw=record,
    )
