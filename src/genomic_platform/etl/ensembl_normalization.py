import re
from dataclasses import dataclass
from typing import Any

from genomic_platform.etl.source_json import SourcePayloadError


@dataclass(frozen=True)
class Mapping:
    ordinal: int
    assembly: str | None
    coordinate_system: str | None
    chromosome: str | None
    position: int | None
    end_position: int | None
    strand: int | None
    valid: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class EnsemblResult:
    mappings: list[Mapping]
    candidates: list[Mapping]
    annotations: dict[str, str]
    findings: dict[str, str]


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _position(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= 9223372036854775807 else None


def normalize_ensembl(payload: Any, rsid: str, assembly: str) -> EnsemblResult:
    if not isinstance(payload, dict) or payload.get("name") != rsid:
        raise SourcePayloadError("Ensembl response does not identify the requested rsID.")
    source_mappings = payload.get("mappings", [])
    if not isinstance(source_mappings, list):
        raise SourcePayloadError("Ensembl mappings must be an array.")
    mappings = []
    findings: dict[str, str] = {}
    for ordinal, item in enumerate(source_mappings):
        source = item if isinstance(item, dict) else {"source_value": item}
        mapped_assembly = _text(source.get("assembly_name"))
        system = _text(source.get("coord_system"))
        chromosome = _text(source.get("seq_region_name"))
        start = _position(source.get("start"))
        end = _position(source.get("end"))
        strand = source.get("strand")
        strand = strand if type(strand) is int and strand in (-1, 1) else None
        valid = (
            mapped_assembly is not None
            and len(mapped_assembly) <= 30
            and system == "chromosome"
            and chromosome is not None
            and len(chromosome) <= 30
            and start is not None
            and end is not None
            and end >= start - 1
        )
        mappings.append(
            Mapping(ordinal, mapped_assembly, system, chromosome, start, end, strand, valid, source)
        )
        if not valid:
            findings["ENSEMBL_INCOMPLETE_MAPPING"] = (
                "Non-chromosomal or incomplete mappings are preserved but not projected."
            )
    candidates = [m for m in mappings if m.valid and m.assembly == assembly]
    annotations = {}
    for name, limit in (("var_class", 80), ("most_severe_consequence", 150)):
        value = _text(payload.get(name))
        if value is not None and len(value) <= limit:
            annotations[name] = value
        else:
            findings["ENSEMBL_MISSING_ANNOTATION"] = (
                "Missing or invalid annotations remain missing."
            )
    if not candidates:
        findings["ENSEMBL_NO_TARGET_MAPPING"] = (
            "No valid chromosome mapping for the requested assembly."
        )
    elif len(candidates) > 1:
        findings["ENSEMBL_AMBIGUOUS_MAPPING"] = (
            "Multiple target-assembly chromosome mappings; no coordinates projected."
        )
    return EnsemblResult(mappings, candidates, annotations, findings)


def forward_snp_alleles(mapping: Mapping) -> tuple[str, list[str]] | None:
    # Allele order is reference-first only in the mapping's reported orientation.
    # Indels and reverse/unknown strand require additional reference evidence.
    alleles = mapping.payload.get("allele_string")
    if (
        mapping.strand != 1
        or mapping.position != mapping.end_position
        or not isinstance(alleles, str)
    ):
        return None
    parts = alleles.split("/")
    if len(parts) < 2 or not all(re.fullmatch("[ACGT]", value) for value in parts):
        return None
    return parts[0], list(dict.fromkeys(parts[1:]))
