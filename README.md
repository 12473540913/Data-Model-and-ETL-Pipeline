# Genomic Data Integration Platform

Data engineering and data architecture for ingesting heterogeneous public genomics and phenotype-association data, preserving raw provenance, normalizing it into a canonical model, validating quality, and exposing curated data over SQL, REST, GraphQL, and SOAP.

The first vertical slice is **lactose intolerance**. Later phenotypes can reuse this same platform.

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

Restricted individual-level cohorts are intentionally not assumed to be public. They can optionally be added later as separate licensed connectors.

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


## Development

### Example CLI Usage

```bash
cp .env.example .env
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,data]"
docker compose up -d db
python -m genomic_platform.cli init-db
python -m genomic_platform.cli seed
python -m genomic_platform.cli discover-traits "eye color" "iris pigmentation"
uvicorn genomic_platform.api.main:app --reload
```

REST docs: `http://localhost:8000/docs`  
GraphQL: `http://localhost:8000/graphql`  
SOAP WSDL after running the SOAP server: `http://localhost:8001/?wsdl`

### Database migrations and persistence

`init-db` and `seed` apply Alembic migrations instead of unmanaged `create_all`.
For an empty database, run `python -m alembic upgrade head`. Existing starter
databases must be verified against revision `0001` before `alembic stamp 0001`
and upgrade; do not blindly stamp an unknown schema.

Checkpoint 2 provides a transactional Python loading service, raw/run lineage,
persisted quality findings, and database quarantine. It does not yet wire the
source-discovery CLI into loading or implement replay. See
[the persistence policy](docs/data-model.md#postgresql-persistence-checkpoint-2).

### Reproducible PostgreSQL integration tests (PowerShell)

The dedicated test database is separate from the development database and binds
only to localhost. Tests create and remove their own isolated schemas. They
require a database named `genomics_test`; they never reset the development schema.

```powershell
docker compose -p genomic-etl-checkpoint2 -f docker-compose.test.yml up -d --wait db-test
$env:GENOMIC_TEST_DATABASE_URL = "postgresql+psycopg://genomics_test:local_test_only@127.0.0.1:55432/genomics_test"
& .\.venv\Scripts\python.exe -m pytest -q
```

Set `GENOMIC_TEST_PORT` before starting Compose if port 55432 is occupied, and
adjust the test URL accordingly. Without `GENOMIC_TEST_DATABASE_URL`, PostgreSQL
tests explicitly skip; that is not verification of persistence.

Stop only this dedicated test stack when finished:

```powershell
docker compose -p genomic-etl-checkpoint2 -f docker-compose.test.yml down
```

### Project layout

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