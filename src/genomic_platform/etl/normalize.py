from decimal import Decimal, InvalidOperation
from typing import Any
from genomic_platform.domain.schemas import AssociationDTO, StudyDTO, TraitDTO, VariantDTO

def clean_text(value: Any) -> str | None:
    if value is None: return None
    value = " ".join(str(value).split()).strip(); return value or None

def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "NA", "NR"): return None
    try: return Decimal(str(value))
    except (InvalidOperation, ValueError): return None

def normalize_rsid(value: Any) -> str | None:
    value = clean_text(value)
    if not value: return None
    lowered = value.lower()
    if lowered.startswith("rs") and lowered[2:].isdigit(): return f"rs{int(lowered[2:])}"
    return None

def ensembl_to_variant(rsid: str, payload: dict) -> VariantDTO:
    mappings = payload.get("mappings") or []
    preferred = next((m for m in mappings if m.get("assembly_name") == "GRCh38" and m.get("coord_system") == "chromosome"), mappings[0] if mappings else {})
    alleles = [a for a in (preferred.get("allele_string") or "").split("/") if a]
    return VariantDTO(rsid=normalize_rsid(rsid), assembly=preferred.get("assembly_name") or "GRCh38", chromosome=clean_text(preferred.get("seq_region_name")), position=preferred.get("start"), reference_allele=alleles[0] if alleles else None, alternate_alleles=alleles[1:] if len(alleles)>1 else [], variant_class=clean_text(payload.get("var_class")), most_severe_consequence=clean_text(payload.get("most_severe_consequence")), metadata={"ensembl_evidence":payload.get("evidence") or [],"ancestral_allele":preferred.get("ancestral_allele"),"all_mappings":mappings})

def gwas_record_to_association(record: dict, normalized_trait_name: str = "eye color") -> AssociationDTO:
    def first(*keys):
        for key in keys:
            if record.get(key) not in (None, ""): return record[key]
        return None
    rsid = first("rs_id","rsId","variant_id","variantId","snp")
    source_id = str(first("association_id","associationId","id") or f"unknown:{abs(hash(str(record)))}")
    trait_name = clean_text(first("efo_trait","efoTrait","trait","reported_trait")) or normalized_trait_name
    efo_id = clean_text(first("efo_id","efoId","trait_id"))
    study = StudyDTO(accession=clean_text(first("study_accession","studyAccession","accession")), pubmed_id=clean_text(first("pubmed_id","pubmedId")), title=clean_text(first("study_title","title")), author=clean_text(first("author","first_author")), sample_description=clean_text(first("initial_sample_description","sample_description")))
    return AssociationDTO(source_record_id=source_id, variant_rsid=normalize_rsid(rsid), trait=TraitDTO(ontology_id=efo_id,name=normalized_trait_name,source_name=trait_name), study=study, p_value=parse_decimal(first("p_value","pValue","pvalue")), effect_allele=clean_text(first("effect_allele","risk_allele")), other_allele=clean_text(first("other_allele")), effect_size=parse_decimal(first("effect_size","or_per_copy_num","beta")), effect_size_type=clean_text(first("effect_size_type","effect_type")), raw=record)
