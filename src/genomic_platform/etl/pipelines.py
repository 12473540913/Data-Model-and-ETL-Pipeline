from collections import Counter
from collections.abc import Iterable
from genomic_platform.connectors.gwas_catalog import GWASCatalogConnector
from genomic_platform.etl.normalize import gwas_record_to_association
from genomic_platform.etl.raw_store import persist_raw_json
from genomic_platform.quality.rules import validate_association

# Phenotype-specific terms live here only as defaults for the legacy ingest entry point;
# the discovery pipeline itself is trait-agnostic.
EYE_COLOR_DISCOVERY_TERMS = ["eye color", "eye colour", "iris pigmentation", "iris color", "iris colour"]
APPROVED_EYE_COLOR_EFO_IDS: list[str] = []

def _trait_key(trait: dict) -> str:
    return str(trait.get("efo_id") or trait.get("efoId") or trait.get("id") or trait)

async def discover_traits(search_terms: Iterable[str]) -> list[dict]:
    """Discover GWAS Catalog ontology traits matching any of the given search terms."""
    connector=GWASCatalogConnector(); seen: dict[str, dict]={}
    for term in search_terms:
        term=term.strip()
        if not term: continue
        for trait in await connector.discover_traits(term):
            seen.setdefault(_trait_key(trait), trait)
    return list(seen.values())

async def discover_associations_by_trait(efo_id: str) -> list[dict]:
    """Discover GWAS Catalog associations matching an EFO trait ID."""
    connector=GWASCatalogConnector()
    return [record async for record in connector.iter_associations_for_trait(efo_id)]

async def discover_associations_by_variant(rsid: str) -> list[dict]:
    """Discover GWAS Catalog associations matching an rsID."""
    connector=GWASCatalogConnector()
    return [record async for record in connector.iter_associations_for_variant(rsid)]

async def ingest_gwas_eye_color(dry_run: bool=True) -> dict:
    if dry_run:
        return {"mode":"dry-run","candidate_traits":await discover_traits(EYE_COLOR_DISCOVERY_TERMS),"message":"Review candidates and add approved ontology IDs to APPROVED_EYE_COLOR_EFO_IDS."}
    if not APPROVED_EYE_COLOR_EFO_IDS: raise RuntimeError("No approved eye-color ontology IDs configured yet.")
    gwas=GWASCatalogConnector(); metrics=Counter()
    for efo_id in APPROVED_EYE_COLOR_EFO_IDS:
        async for record in gwas.iter_associations_for_trait(efo_id):
            source_id=str(record.get("association_id") or record.get("associationId") or record.get("id") or metrics["seen"])
            persist_raw_json("gwas_catalog","association",source_id,record); metrics["seen"] += 1
            dto=gwas_record_to_association(record); findings=validate_association(dto)
            if any(f.severity=="error" for f in findings): metrics["quarantined"] += 1; continue
            # TODO: canonical upserts + association_source lineage rows.
            metrics["normalized"] += 1
    return dict(metrics)
