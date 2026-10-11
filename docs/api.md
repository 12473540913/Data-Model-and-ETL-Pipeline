# Read-only catalog APIs (checkpoint 5)

All protocols use `CatalogRepository`, `CatalogService`, and the response
contracts in `services/contracts.py`. Transport code does not normalize,
reconcile, enrich, or write scientific records. Database sessions are
transaction-read-only with a five-second PostgreSQL statement timeout.

## Runtime and startup

The verified application runtime is **Python 3.11** (local verification: 3.11.9).
For checkpoint 5.5 health checks, role provisioning, clean-image verification and
current CI status, see [deployment preparation](deployment.md).
Released Spyne 2.14.0 imports and generates WSDL on this runtime; its bundled
import compatibility code fails on the previous Python 3.14 environment.
No installed package is patched and no upstream development revision is used.
Lxml supplies schema validation. The maintained `a2wsgi` adapter mounts the
existing Spyne WSGI facade into FastAPI without using Starlette's deprecated
WSGI adapter.

```powershell
py -3.11 -m venv .venv311
& .\.venv311\Scripts\python.exe -m pip install -r requirements.lock
& .\.venv311\Scripts\python.exe -m pip install --no-deps -e .
& .\.venv311\Scripts\python.exe -m pip check
# Configure DATABASE_URL in the environment or .env; never put it in source.
& .\.venv311\Scripts\python.exe -m genomic_platform.cli init-db
& .\.venv311\Scripts\python.exe -m uvicorn genomic_platform.api.main:app --host 127.0.0.1 --port 8000
```

The older `.venv` remains untouched. `.python-version` records the tested local
patch version. Newer patched 3.11 runtimes are appropriate; other Python minors
are not certified for the complete three-protocol application. Package metadata
retains its existing minimum (`>=3.11`) rather than claiming an unverified upper
bound. The runtime/development/data lock excludes optional Dagster orchestration.
Its attempted resolution with this lock fails dependency resolution; the
resolver's diagnostic does not establish the exact transitive incompatibility.
Orchestration needs coordinated dependency resolution before use and is not
enabled or verified by this milestone.

OpenAPI: `/docs`, `/openapi.json`. GraphQL: `/graphql` (HTTP queries; no mutations,
subscriptions, or browser IDE). Mounted SOAP endpoint: `/soap/`; WSDL:
`/soap/?wsdl`. Standalone SOAP remains available:

```powershell
& .\.venv311\Scripts\python.exe -m genomic_platform.soap.server
# http://127.0.0.1:8001/?wsdl
```

## Canonical operations

| REST GET | GraphQL field | SOAP operation |
|---|---|---|
| `/v1/variants/{rsid}` | `variant(rsid)` | `GetVariant` |
| `/v1/traits/{ontology_id}` | `trait(ontologyId)` | `GetTrait` |
| `/v1/genes/{symbol}` | `gene(symbol)` | `GetGene` |
| `/v1/studies/{study_id}` | `study(studyId)` | `GetStudy` |
| `/v1/associations/{association_id}` | `association(associationId)` | `GetAssociation` |
| `/v1/variants/{rsid}/associations` | `variantAssociations(rsid)` | `GetVariantAssociations` |
| `/v1/traits/{ontology_id}/associations` | `associationsForTrait(ontologyId)` | `GetTraitAssociations` |
| `/v1/genes/{symbol}/associations` | `geneAssociations(symbol)` | `GetGeneAssociations` |

Associations contain the persisted trait and optional study, not independently
resolved scientific interpretations. Gene associations use only `variant_gene`
relationships; the platform does not infer genes from coordinates. A missing
gene is distinct from an existing gene with no associations.

Compatibility: the historical REST trait-name association path still returns a
list for non-ontology names. New ontology-ID queries return a page envelope.
GraphQL `traitAssociations(traitName, limit)` and SOAP `GetTraitAssociationIds`
remain bounded compatibility operations; the hardcoded eye-color query default
is removed. Ambiguous exact-name lookups require an ontology ID instead of
silently choosing a trait.

## Evidence operations

| REST GET | GraphQL field | SOAP operation |
|---|---|---|
| `/v1/associations/{id}/sources` | `associationSources(associationId)` | `GetAssociationSources` |
| `/v1/variants/{rsid}/mappings` | `variantMappings(rsid, assembly)` | `GetVariantMappings` |
| `/v1/variants/{rsid}/enrichments` | `variantEnrichments(rsid)` | `GetVariantEnrichments` |
| `/v1/evidence/raw/{raw_id}` | `rawEvidence(rawId)` | `GetRawEvidence` |
| `/v1/evidence/runs/{run_id}` | `ingestionRun(runId)` | `GetIngestionRun` |
| `/v1/evidence/runs/{run_id}/raw` | `runRaw(runId, disposition)` | `GetRunRaw` |
| `/v1/evidence/runs/{run_id}/processing` | `runProcessing(runId)` | `GetRunProcessing` |
| `/v1/evidence/{scope}/{identifier}/quality` | `quality(scope, identifier)` | `GetQuality` |

Quality scope is `association`, `raw`, `run`, or `enrichment` (an enrichment
observation UUID). Run IDs come from ingestion/enrichment CLI output.
Raw evidence exposes checksums, acquisition identifiers/times, disposition,
association links and exact p-value representations. It never exposes filesystem
paths, request metadata, credentials, arbitrary JSONB, or unrestricted payload
downloads. `reported_p_value_text` retains the original GWAS `p_value` JSON token,
including source zero; it is evidence, not a curated scientific value. When
reading this field, the stored raw checksum is verified.

Source links provide safe raw/run UUID references. Ensembl mapping rows carry
their own assembly, source, raw/evidence identifiers, alleles, validity, and
checksum. `projection_mapping_id` distinguishes the canonical projection from
the full mapping history. `enrichment_status=None` means no lookup observation;
`ambiguous`, `partial`, `not_found`, `conflict`, and `failed` retain their meanings.
The top-level status is the latest observation across assemblies; use the
assembly-specific enrichment history for detailed interpretation.

Quarantined records are not canonical associations. Query an acquisition run's
raw page with `disposition=quarantined` and then its raw quality findings.
Replay runs reference existing raw observations; use processing pages to inspect
their per-record outcomes instead of expecting newly acquired raw objects.

## Serialization, errors, and bounds

- P-values/effect sizes are decimal **strings**, not floating-point JSON/XML
  numbers. Unreconciled curated p-values stay null; status, exact representation,
  original source token, candidate, and findings remain separately queryable.
- Missing database fields are nullable. Dates use ISO 8601 with timezone offsets.
  SOAP uses XSD dateTime and signed 64-bit integers; GraphQL uses a bounded `Long`
  scalar so database positions/sample counts are not limited to 32-bit `Int`.
- Collection arguments: `limit=50` by default, range 1-100; `offset=0`, range
  0-10000. Pages contain `items`, `limit`, `offset`, `has_more`, `next_offset`
  (camelCase in GraphQL). Ordering uses persisted IDs or timestamp/ID pairs.
  Offset pages are deterministic for an unchanged dataset, not a snapshot across
  simultaneous writes.
- REST uses 404 for missing records, 422 for invalid/ambiguous inputs, 503 for
  database unavailability, and safe `{error: {code, message}}` responses.
  GraphQL returns nullable fields plus errors with `extensions.code`. SOAP
  declares `CatalogFault` in WSDL and returns Client/Server faults.
- Unknown internal resolver errors are masked; database exceptions are logged
  by class without returning credentials, SQL, or connection details.
- GraphQL limits: depth 8 for data selections, 20 aliases, 2000 parser tokens,
  100 service operations and a 1000-row budget per request. Collections are
  paginated at query roots; nested association trait/study objects are singular.
  Framework introspection uses GraphQL-core's separate maximum-introspection-depth
  validation rule and remains token-bounded.
- HTTP POST bodies are limited to 64 KiB, including chunked ASGI requests. SOAP
  rejects DTD declarations, disables entity resolution/network DTD access, and
  validates requests against its XSD. Only read-only operations are available.

## Examples

```graphql
query {
  variant(rsid: "rs4988235") {
    id rsid assembly chromosome position projectionMappingId enrichmentStatus
  }
  variantAssociations(rsid: "rs4988235", limit: 10, offset: 0) {
    hasMore nextOffset
    items {
      id pValue pValueStatus pValueText qualityStatus
      trait { ontologyId name }
      study { id accession pubmedId title }
    }
  }
  variantMappings(rsid: "rs4988235", assembly: "GRCh37", limit: 10) {
    items { assembly chromosome position rawObjectId sha256 }
  }
}
```

SOAP 1.1 request (`Content-Type: text/xml`, `SOAPAction: GetVariant`):

```xml
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:cat="genomic.platform.catalog">
  <soap:Body>
    <cat:GetVariant><cat:rsid>rs4988235</cat:rsid></cat:GetVariant>
  </soap:Body>
</soap:Envelope>
```

Paginated SOAP arguments follow the WSDL sequence: lookup identifier, `limit`,
`offset`, then optional `assembly` or `disposition`. Namespace and argument order
are schema-validated. All operations use typed structures, not JSON strings in XML.

## Docker, CI and verification

`Dockerfile` uses Python 3.11, installs the full pinned dependency lock, runs as a
non-root user, and serves all three protocols on port 8000. `python:3.11-slim`
tracks security patch releases; pin its resolved digest in a deployment when
byte-reproducible base images are required. The GitHub workflow uses Python 3.11,
PostgreSQL 17, the same lock, the complete test suite, and a Docker build.

```powershell
docker build -t genomic-api .
# Set DATABASE_URL for a database reachable from inside the container.
docker run --rm -p 8000:8000 --env DATABASE_URL genomic-api
```

Apply migrations using an administrative connection before serving. For a public
deployment, configure a dedicated SELECT-only PostgreSQL role, transport TLS,
gateway request throttling, and operational access policies. Read-only transaction
settings and per-request budgets are not authentication or global rate limiting.

PostgreSQL tests use the dedicated test database and isolated disposable schemas:

```powershell
# Set GENOMIC_TEST_DATABASE_URL as documented in the root README.
& .\.venv311\Scripts\python.exe -m pytest -q tests\integration\test_catalog_protocols.py
& .\.venv311\Scripts\python.exe -m pytest -q
```

## Checkpoint 5 verification record

- Original regression under Python 3.11 before API changes: **269 passed**.
- Complete expanded regression: **297 passed**, no skipped tests
  (`python -m pytest -q` with `GENOMIC_TEST_DATABASE_URL` set).
- Final protocol suite, including GraphQL-core introspection limits:
  **28 passed** (`python -m pytest -q tests\integration\test_catalog_protocols.py`).
- Two non-failing warnings: Spyne's deprecated standard-library `cgi` import and
  Starlette's deprecated httpx TestClient adapter.
- `python -m pip check`: no broken requirements. The optional orchestration
  dry-run failed as described above; it was not installed.
- `python -m genomic_platform.cli init-db`: upgraded the persistent dedicated
  PostgreSQL 17 database from `0004` to `0005`.
- `python -m alembic check`: no new upgrade operations detected.
- Changed API/repository/service/SOAP modules, protocol tests and migration passed
  Ruff; the edited Python API modules reported no editor diagnostics.
- `docker build -t genomic-api-checkpoint5 .`: successful final image build.
- Actual Uvicorn HTTP requests on port 58005 and final Docker-image requests on
  port 58006 returned HTTP 200 for REST variant lookup, GraphQL queries, SOAP XML,
  and the 44,290-byte WSDL. All three returned the same canonical variant UUID
  and GRCh38 chromosome 2 / 135851076. Historical GRCh37 / 136608646 remained
  queryable. Test servers/containers were stopped after verification.
- Real GraphQL pagination returned 64 associations, including all nine
  unreconciled p-values as null. Integration fixtures exposed four quarantined
  records through raw/quality pages, not invented canonical associations, and
  retained the exact candidate `3e-1451` alongside its reported source zero.
- Before/after full-row database hashes across variants, associations, source
  links, raw objects, mappings, enrichment observations and quality findings were
  identical around live protocol requests.
- Persistent counts remained: 64 associations, 64 source links, 207 raw objects,
  three mappings, three enrichment observations, and 94 quality findings.
- The GitHub workflow is configured but has not been executed remotely.

### Exact changed-file inventory

Paths below are relative to the repository root. Existing generated egg-info
metadata was restored after editable installs; it is not part of this change.

New:

- `.python-version`, `requirements.lock`, `Dockerfile`, `.dockerignore`
- `.github/workflows/tests.yml`
- `docs/api.md`
- `migrations/versions/0005_catalog_query_indexes.py`
- `src/genomic_platform/api/limits.py`
- `src/genomic_platform/services/contracts.py`
- `tests/integration/test_catalog_protocols.py`

Modified:

- `.gitignore`, `pyproject.toml`, `README.md`
- `docs/data-model.md`, `migrations/README`
- `src/genomic_platform/api/deps.py`
- `src/genomic_platform/api/graphql_schema.py`
- `src/genomic_platform/api/main.py`
- `src/genomic_platform/api/rest.py`
- `src/genomic_platform/repositories/catalog.py`
- `src/genomic_platform/services/catalog.py`
- `src/genomic_platform/soap/server.py`
- `src/genomic_platform/domain/models.py` (indexes only)
- `src/genomic_platform/etl/ingestion.py` (migration-version guard only)
- `src/genomic_platform/etl/enrichment.py` (migration-version guard only)
- `tests/integration/test_gwas_persistence.py` (migration-version assertions only)

No external-source ingestion, normalization, reconciliation, or enrichment policy
was expanded. No commit was created.
