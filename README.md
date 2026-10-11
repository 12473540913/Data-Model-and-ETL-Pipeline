# Genomic Data Integration Platform

Data engineering and data architecture for ingesting heterogeneous public genomics and phenotype-association data, preserving raw provenance, normalizing it into a canonical model, validating quality, and exposing curated data over SQL, REST, GraphQL, and SOAP.

The first vertical slice is **lactose intolerance**. Later phenotypes can reuse this same platform.
The verified implementation is trait-agnostic GWAS ingestion with optional
Ensembl enrichment. NCBI and Open Targets below are future architectural roles,
not completed ingestion integrations.

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

The complete REST/GraphQL/SOAP application is verified on **Python 3.11**.
See [API operations and reproducible runtime setup](docs/api.md) for the shared
read-only query layer, safe scientific evidence queries, Docker and CI.
See [pre-deployment preparation](docs/deployment.md) for health probes, separate
database roles, secrets, and the clean-container verification procedure.

### Example CLI Usage

```bash
cp .env.example .env
python3.11 -m venv .venv311
# Windows: py -3.11 -m venv .venv311; .venv311\Scripts\activate
# macOS/Linux: source .venv311/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
docker compose up -d db
python -m genomic_platform.cli init-db
python -m genomic_platform.cli seed
python -m genomic_platform.cli discover-traits "eye color" "iris pigmentation"
uvicorn genomic_platform.api.main:app --reload
```

REST docs: `http://localhost:8000/docs`  
GraphQL: `http://localhost:8000/graphql`  
SOAP WSDL: `http://localhost:8000/soap/?wsdl` (also standalone on port 8001).

### Database migrations and persistence

`init-db` and `seed` apply Alembic migrations instead of unmanaged `create_all`.
For an empty database, run `python -m alembic upgrade head`. Existing starter
databases must be verified against revision `0001` before `alembic stamp 0001`
and upgrade; do not blindly stamp an unknown schema.

Checkpoint 2 provides the whole-dataset atomic loading service. Checkpoint 3
adds bounded CLI acquisition, processing, replay, and resumable runs. See
[the persistence policy](docs/data-model.md#postgresql-persistence-checkpoint-2).

### GWAS ingestion and replay

Apply migrations first with `python -m genomic_platform.cli init-db`. Commands
use `DATABASE_URL` from the environment or local `.env`; database credentials
are never CLI arguments or displayed in ingestion failures.

```powershell
# Live acquisition: returned associations retain their individual traits.
& .\.venv311\Scripts\python.exe -m genomic_platform.cli ingest-gwas-trait EFO_0010507 --batch-size 500
& .\.venv311\Scripts\python.exe -m genomic_platform.cli ingest-gwas-variant rs4988235 --page-size 100 --timeout 30

# Saved JSON: no network needed.
& .\.venv311\Scripts\python.exe -m genomic_platform.cli ingest-gwas-file .\case-studies\rs4988235-associations.json --batch-size 500

# Substitute UUIDs from the JSON run summaries.
& .\.venv311\Scripts\python.exe -m genomic_platform.cli replay-gwas <original-run-uuid> --batch-size 500
& .\.venv311\Scripts\python.exe -m genomic_platform.cli resume-gwas <failed-or-interrupted-run-uuid>
```

Every command prints JSON with run/origin identifiers, status, captured/processed,
loaded/partial/quarantined, pending, failed-batch records, failure history, and
capture completion. Quarantine or unresolved science produces
`completed_with_issues`, not an execution failure. HTTP, checksum, malformed-source,
and database failures exit nonzero and never report a successful run.

Acquisition commits raw payloads in bounded batches before normalization begins.
Processing then commits atomic batches (default 500, configurable 1-10000).
Earlier committed batches survive later failures; the failed batch rolls back.
`resume-gwas` retries only missing processing observations, using persisted
run/raw identities. It does not create another run or duplicate lineage.

If live acquisition is incomplete, resumption repeats the query from the start
and checksum-deduplicates captured payloads. Stored page/request checkpoints are
audit evidence, not a promise that mutable upstream pages form a stable snapshot.
Different payload versions for the same source ID are quarantined, not silently
selected. Saved-file resumption requires the same file checksum. After capture
completes, resumption needs neither the file nor the source API.

Replay verifies stored checksums and creates a new linked processing run, **not
new raw source observations**. It preserves original payloads, findings, source
links, and quarantine; scientific reconciliation policies are unchanged. See
[bounded ingestion details](docs/data-model.md#bounded-ingestion-and-replay-checkpoint-3)
for recovery and provenance semantics.

### Independent Ensembl enrichment (checkpoint 4)

After applying migrations, enrich existing canonical variants without fetching
GWAS again:

```powershell
& .\.venv311\Scripts\python.exe -m genomic_platform.cli enrich-variant rs4988235
& .\.venv311\Scripts\python.exe -m genomic_platform.cli enrich-variant rs4988235 --assembly GRCh37
& .\.venv311\Scripts\python.exe -m genomic_platform.cli enrich-variants --limit 100 --requests-per-second 5 --timeout 30
& .\.venv311\Scripts\python.exe -m genomic_platform.cli enrich-variants --limit 100 --refresh
```

GRCh38 is the default target; GRCh37 uses its separate Ensembl endpoint. The
response, not the requested endpoint, supplies each mapping's assembly. Every
mapping is retained, including incomplete, non-chromosomal, and ambiguous entries.
Only exactly one valid target-assembly chromosome mapping may fill missing
canonical coordinates. Existing coordinates and annotations are never overwritten.
The canonical coordinate is a convenient projection of historical mapping evidence.

Repeated identical responses reuse evidence/mapping records while recording a
new lookup observation and run. Changed responses retain both versions and produce
quality findings, including representation-only differences such as synonym
ordering; these are not scientific reconciliation claims. Different assemblies
never share a projection. No GWAS raw
observations, associations, lineage, or scientific p-values are modified.

Selection is bounded and ordered; each variant commits atomically, so earlier
results survive later failures. Default selection skips variants whose latest
observation for the selected assembly is not failed (including missing and
ambiguous results), but retries failed lookups. Use a single-rsID command or
`--refresh` to recheck previous results.
Large refresh jobs should use explicit rsIDs for subsequent subsets; refresh
selects the first ordered `--limit` variants and is not a cursor-based traversal.
HTTP retries are limited to four transient attempts with request pacing and
`Retry-After` handling. Cooldowns exceeding 300 seconds are surfaced for later
retry rather than bypassed. Failures exit nonzero with safe JSON run summaries.

See [enrichment policy](docs/data-model.md#ensembl-enrichment-checkpoint-4).

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
migrations/
sql/
docs/
tests/
data/raw/
data/quarantine/
```