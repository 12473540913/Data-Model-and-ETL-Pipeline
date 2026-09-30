# Source Catalog — Eye Color

## Tier 1

### GWAS Catalog
Primary curated variant-trait association, study, publication and ancestry metadata. Use targeted API ingestion first, then versioned bulk releases for reproducibility. Add full summary-statistics ingestion only for studies where files are available and licensing permits.

### Ensembl
Enrich observed rsIDs with GRCh38 mappings, alleles, variant class, consequence and optional population evidence. Do not ingest all Ensembl variation data initially.

### NCBI dbSNP / Variation
Cross-check RefSNP identity, merged IDs, placements and normalized representations. Keep this connector isolated because NCBI exposes multiple useful representations/services.

## Tier 2

### EFO / GWAS trait mappings
Normalize phenotype terminology and preserve ontology hierarchy.

### Open Targets
Secondary integrated view and cross-source enrichment. Preserve original source provenance.

## Conditional
Restricted individual-level cohorts require separate access/licensing/storage policy and should not be mixed into the public starter by assumption.
