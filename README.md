# Genomic Data Integration Platform

Portfolio-grade data engineering and data architecture for ingesting heterogeneous public genomics and phenotype-association data, preserving raw provenance, normalizing it into a canonical model, validating quality, and exposing curated data over SQL, REST, GraphQL, and SOAP.

The first vertical slice is **human eye color / iris pigmentation**. Later phenotypes (freckles, skin pigmentation, hair color, etc.) can reuse the same platform.

## Architecture

```text
Public sources
  GWAS Catalog ─┐
  Ensembl       ├─> ingestion -> raw/bronze -> normalize/validate -> canonical PostgreSQL
  NCBI/dbSNP    ┤                                                     |
  EFO           ┤                                           +---------+---------+
  Open Targets ─┘                                           |         |         |
                                                           SQL       REST    GraphQL
                                                                              |
                                                                             SOAP
                                                                              |
                                                               Phenotype-to-Genotype
```

## Initial source roles

- **GWAS Catalog**: primary curated association/study source; API + versioned bulk downloads + available summary statistics.
- **Ensembl**: variant mapping and annotation enrichment for observed rsIDs.
- **NCBI/dbSNP / Variation**: variant identity, merged identifiers, placements and normalization enrichment.
- **EFO / GWAS ontology mappings**: controlled trait normalization.
- **Open Targets**: optional integrated cross-check/enrichment source, never a replacement for original provenance.

Restricted individual-level cohorts are intentionally not assumed to be public. They can be added later as separate licensed connectors.

## API strategy

- **REST**: primary public interface and best default for most consumers.
- **GraphQL**: useful for flexible nested research queries.
- **SOAP**: included deliberately for enterprise/legacy integration practice; not the preferred modern interface.
- **SQL**: first-class internal analytical access for large scans.

All API protocols use one shared service layer.

## Data layers

- **Bronze/raw**: immutable source payloads/files + checksums + acquisition metadata.
- **Silver/normalized**: typed staging representations, normalized identifiers and controlled errors.
- **Gold/curated**: canonical relational model exposed to researchers and APIs.

## Canonical entities

`source_system`, `dataset_release`, `ingestion_run`, `raw_object`, `trait`, `trait_synonym`, `gene`, `variant`, `variant_alias`, `variant_gene`, `study`, `population`, `study_population`, `association`, `association_source`, `quality_issue`.

Key rule: **biological entities are not source records**. Multiple upstream records can support the same canonical entity or association.

## Eye-color milestone

1. Discover relevant GWAS Catalog ontology traits for eye color / iris pigmentation.
2. Human-review and approve the ontology IDs.
3. Ingest associations + study metadata.
4. Preserve raw responses.
5. Collect rsIDs and enrich with Ensembl and NCBI.
6. Normalize traits, variants, studies and populations.
7. Upsert canonical associations with provenance.
8. Expose REST + GraphQL + SOAP interfaces.
9. Let the separate Phenotype-to-Genotype notebooks consume the curated API/database.

## Quick start

```bash
cp .env.example .env
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,data]"
docker compose up -d db
python -m genomic_platform.cli init-db
python -m genomic_platform.cli seed
python -m genomic_platform.cli discover-eye-color-traits
uvicorn genomic_platform.api.main:app --reload
```

REST docs: `http://localhost:8000/docs`  
GraphQL: `http://localhost:8000/graphql`  
SOAP WSDL after running the SOAP server: `http://localhost:8001/?wsdl`

## Project layout

```text
src/genomic_platform/
  api/              REST + GraphQL
  soap/             SOAP facade
  connectors/       upstream source adapters
  domain/           canonical ORM + DTOs
  etl/              raw capture, normalization, pipelines
  services/         shared application services
  repositories/     database access
  quality/          validation / DQ rules
  orchestration/    optional scheduler assets
sql/
docs/
tests/
data/raw/
data/quarantine/
```

## Design rules

1. Preserve raw source data.
2. Never silently drop provenance.
3. Use platform-owned canonical IDs; external IDs are identifiers/aliases.
4. Prefer GRCh38 for the first curated coordinate while preserving source mappings.
5. Keep original source phenotype text alongside normalized ontology concepts.
6. Make ingestion idempotent.
7. Quarantine malformed records rather than silently discarding them.
8. Record release/run lineage for curated facts.
9. Keep protocol adapters thin.
10. Do not let the research notebooks become the ETL pipeline.

## Recommended build order

1. Run trait discovery and capture real GWAS v2 payloads.
2. Add contract fixtures/tests for the exact returned response shape.
3. Approve the eye-color ontology IDs.
4. Finish GWAS association/study upserts.
5. Enrich rsIDs through Ensembl.
6. Finish the NCBI Variation endpoint mapping you choose to standardize on.
7. Add bulk-release ingestion for reproducible snapshots.
8. Add dbt and/or Dagster only after the local pipeline is correct.
9. Connect the Phenotype-to-Genotype project after the curated contract stabilizes.
