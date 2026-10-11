# Pre-deployment baseline (checkpoint 5.5)

This is local stabilization and infrastructure-planning guidance, not a cloud
deployment. No domains, public infrastructure, or new scientific features are
provisioned. Scientific normalization, enrichment, and reconciliation policies
remain unchanged. Commits and tags remain user-managed.

## Reproducible installation

Use Python **3.11**, the supported complete-application runtime. The tested Windows
patch is 3.11.9; the Docker base tracks maintained 3.11 patch releases. Do not use
the old Python 3.14 environment for SOAP.

```powershell
py -3.11 -m venv .venv311
& .\.venv311\Scripts\python.exe -m pip install -r requirements.lock
& .\.venv311\Scripts\python.exe -m pip install --no-deps -e .
& .\.venv311\Scripts\python.exe -m pip check
& .\.venv311\Scripts\python.exe -m ruff check src tests migrations
# Configure GENOMIC_TEST_DATABASE_URL for the isolated database.
& .\.venv311\Scripts\python.exe -m pytest -q
```

The application/development/data lock has exact package pins and contains no
editable paths or credential-bearing package URLs. Optional orchestration is
not installed. `make install` uses the same lock, not `[orchestration]`.
Editable-install egg-info is generated locally and ignored, not release source.
Virtual environments, build outputs, logs, caches, raw data, and `.env.*` files
are ignored; `.env.example` is intentionally tracked.

Local `.env.example` credentials and Compose passwords are development-only
placeholders. In deployment inject `DATABASE_URL` into each process with that
process's own login. Never bake `.env` into an image, commit credentials, pass
database passwords as command-line arguments, or print connection environments.
The application needs an SQLAlchemy `postgresql+psycopg://` URL; `psql` uses
`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, and `PGOPTIONS` instead.
Encode special URL characters in passwords before building `DATABASE_URL`.

## Health checks

| Probe | Purpose | Healthy | Unhealthy |
|---|---|---|---|
| `GET /v1/health` | Process liveness; no database dependency | 200 `{"status":"ok"}` | Process unavailable |
| `GET /v1/ready` | Database connection and canonical association SELECT access | 200 `{"status":"ready"}` | Sanitized 503 error |

Readiness works with an empty but migrated database; it never requires a
particular biological record. Missing tables, denied SELECT, or unavailable
PostgreSQL fail readiness. It does not inspect every migration/index or external
source: run `alembic check` as the migration role before release.
Connection establishment and pool acquisition are bounded at five seconds;
API PostgreSQL statements are bounded at five seconds. The Docker health check
uses readiness. Orchestrators should use liveness for restart decisions and
readiness to decide whether to route traffic.

## Database roles and least privilege

Do not use the PostgreSQL owner/superuser as the API connection.

| Privilege role (NOLOGIN) | Allowed | Not allowed |
|---|---|---|
| `genomic_api` | CONNECT, schema USAGE, table SELECT | Writes, DDL, CREATE schema, TEMP, ownership, migration membership |
| `genomic_etl` | CONNECT, schema USAGE, SELECT/INSERT/UPDATE on application tables | DELETE/TRUNCATE, DDL, ownership, migration bookkeeping writes |
| `genomic_migrator` | Own application schema/objects; Alembic DDL | Superuser, role/database creation, replication, bypass-RLS |

API queries currently need SELECT on all application tables, including the
checksummed raw evidence used by scoped evidence queries. This database role is
read-only, not a row/column-redaction boundary. The API contract, not a raw SQL
connection, defines publicly visible evidence. Never expose the database port
publicly.

### Bootstrap an empty, dedicated database

An administrator creates the database. Then execute these repository scripts
using `psql` with credentials injected via its environment:

```powershell
psql -v ON_ERROR_STOP=1 -f sql\deployment_roles.sql
```

The script removes PUBLIC database/schema privileges, creates three NOLOGIN
privilege roles, assigns schema ownership to the migrator, and configures default
table grants for objects created **by genomic_migrator**. It is intentionally
not an idempotent existing-database takeover script: stop if roles already exist,
and review ownership rather than discarding data or silently changing it.
For an existing database, an administrator must review current grants and
transfer application-object ownership explicitly in an approved maintenance
window. Do not run broad `REASSIGN OWNED` on a shared database.

Provision separate LOGIN roles using your infrastructure's secret manager or a
trusted administrative `psql` session. Example role membership (no passwords in
SQL files):

```sql
CREATE ROLE genomic_api_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE genomic_etl_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE genomic_migration_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT genomic_api TO genomic_api_login;
GRANT genomic_etl TO genomic_etl_login;
GRANT genomic_migrator TO genomic_migration_login;
ALTER ROLE genomic_api_login SET default_transaction_read_only = on;
```

Assign unique strong passwords with `psql`'s interactive `\password` command
(or a trusted secret-provisioning channel), not literals in tracked SQL.
Never grant `genomic_migrator`, the migration login, or ETL membership to the API
login. Only administrators provision role memberships. Non-superuser ownership
is enough for application migrations; the migrator has no role/database creation.

### Migrate, apply grants, ingest, then serve

The migration process uses its own `DATABASE_URL` and explicitly selects the
owner role before opening connections:

```powershell
$env:PGOPTIONS='-c role=genomic_migrator'
& .\.venv311\Scripts\python.exe -m genomic_platform.cli init-db
& .\.venv311\Scripts\python.exe -m alembic check
# With the same migration identity in PGUSER/PGPASSWORD:
psql -v ON_ERROR_STOP=1 -f sql\deployment_grants.sql
Remove-Item Env:\PGOPTIONS
```

`deployment_grants.sql` also covers existing application tables and removes
write privileges on `alembic_version` from ETL/API. UUID-based persistence does
not require API sequence permissions. Default grants apply to future objects
created under the migration owner; reapply/verify explicit grants after upgrades.

Run ingestion/enrichment/replay with the ETL login in a separate administrative
process. Never run `init-db` or `seed` as API/ETL identities: `seed` also invokes
migrations. For a populated model use `ingest-gwas-file` or existing acquisition
commands, not starter biological seed defaults. The API startup does not migrate,
seed, ingest, enrich, replay, or create roles.

Use only the API login in the serving process; **do not** propagate migration
`PGOPTIONS`, administrative passwords, or ETL credentials into it.

## Protocol and runtime boundaries

- REST under `/v1` is GET-only. The HTTP middleware rejects mutation verbs.
- GraphQL permits POST for read-only queries but has no mutation/subscription
  schema. Queries remain token/depth/alias/row-budget bounded.
- SOAP has typed query operations and declared faults, no administrative methods.
  XML DTDs/entities are blocked and bodies are bounded.
- All facades call the same catalog service, use read-only transactions and return
  sanitized database errors. The SELECT-only login enforces this independently
  of middleware and transaction configuration.
- Raw evidence is scoped to safe identifiers, checksums, status and scientific
  representations; no filesystem route or unrestricted JSON payload download.
- CLI administration remains an internal process, not an HTTP operation.
- OpenAPI, GraphQL schema, SOAP WSDL and examples are documented in [api.md](api.md).
  The advertised API/package version is shared; `/v1` is the resource-contract
  version, not a claimed deployment release/tag.

## Clean Docker verification procedure

Build from tracked source, without a host virtual environment or `.env`:

```powershell
docker build --no-cache -t genomic-api:checkpoint55 .
```

The image installs pinned dependencies, installs the application non-editably,
and runs as a non-root user. Its `/app` includes Alembic configuration/migrations,
so administrative migration commands can run as separate one-shot containers:

```powershell
# Inject migration DATABASE_URL and PGOPTIONS ONLY into this one-shot process.
docker run --rm --env DATABASE_URL --env PGOPTIONS genomic-api:checkpoint55 python -m genomic_platform.cli init-db
docker run --rm --env DATABASE_URL --env PGOPTIONS genomic-api:checkpoint55 python -m alembic check
# Switch the environment to the API login; remove PGOPTIONS before serving.
docker run --rm -p 127.0.0.1:8000:8000 --env DATABASE_URL genomic-api:checkpoint55
```

For fixture ingestion mount only the required saved file read-only at a known
container path and run the existing CLI using the ETL login. If acquisition
commands need filesystem raw storage, mount a writable raw directory owned by
the container's non-root user; the public API container does not need that mount.

On a separate disposable PostgreSQL 17 database, bootstrap the roles, migrate
with the migration login, apply grants, and ingest the 68-association fixture
with the ETL login. Then serve using the API login and verify:

1. Liveness/readiness, OpenAPI and SOAP WSDL return 200.
2. REST, GraphQL and SOAP variant UUIDs agree; association pages return 64 records,
   nine unreconciled numeric p-values remain null, and four raws are quarantined.
3. Enrichment is optional: after fixture-only ingestion coordinates remain null.
4. A direct API database INSERT and CREATE TABLE fail. API/ETL cannot update
   `alembic_version`; role privileges and table ownership match the table above.
5. Stored scientific/lineage records are unchanged by protocol requests.

Use a disposable database/container and a private network; never reset the
existing dedicated regression database or a shared database for this procedure.
Stop only verification containers you created; do not remove shared volumes.

## CI release gate

The GitHub Actions workflow uses Python 3.11, an isolated PostgreSQL 17 service
with its own test-only credentials, dependency pins, `pip check`, explicit
`init-db`, `alembic check`, Ruff (`src tests migrations`), pytest, and Docker build.
Integration tests create disposable per-test schemas and refuse a database not
named `genomics_test`. CI has read-only repository permissions and a job timeout.
The PostgreSQL administrator in CI exists solely for isolated schema/role tests;
it is not a deployment API credential.

## Deployment blockers versus optional improvements

Before public exposure:

- Push the checkpoint 5.5 changes and rerun CI. The latest pushed checkpoint 5
  [Actions run](https://github.com/12473540913/Data-Model-and-ETL-Pipeline/actions/runs/38143359303)
  failed because Alembic's Windows-only migration paths did not exist on Linux.
  Those paths and non-editable-install migration configuration are fixed and
  tested locally on Linux; the updated workflow has not yet run remotely.
- Provision/review actual least-privilege logins and secrets. Development Compose
  credentials must not be reused.
- Decide infrastructure/networking, TLS, gateway throttling, evidence access
  policy, backups/restore tests, and operational monitoring. These are planning
  prerequisites, not application features added by this checkpoint.

Optional improvements, not blockers for the supported local baseline:

- Pin the resolved Docker base digest and package build tooling/hashes for
  byte-reproducible supply-chain builds (runtime dependencies are already pinned).
- Address the existing Spyne `cgi` and Starlette TestClient deprecation warnings
  in a later dependency upgrade; Python 3.11 remains the supported runtime.
- Resolve optional orchestration in a separate future task only if it is needed.
- Add richer observability/global load testing when infrastructure is chosen.

## Verification record

The initial worktree was clean at checkpoint 5 commit `461a470`; all source files
were tracked. Review of the checkpoint commit series and tracked-file audit found
six generated egg-info files, now removed and ignored. No obvious embedded
deployment secrets or private-key/token patterns were identified in tracked
source; database passwords found there are explicit local/test placeholders.
The local `.env` remains ignored and untouched. This is a release-boundary check,
not a claim of comprehensive security certification or dependency vulnerability
auditing.

Completed locally:

- **304 passed** in the final Python 3.11.9 Windows PostgreSQL regression.
- **304 passed** in the final clean Python 3.11 Linux image against a separate
  disposable PostgreSQL 17 database.
- Full `python -m ruff check src tests migrations` passed. Seven pre-existing
  import-order findings and eleven single-line formatting findings received
  lint/format-only fixes; no connector/orchestration
  functionality was added. The project explicitly configures Ruff's standard
  syntax/undefined-name checks plus import ordering, avoiding environment-specific
  rule selection and executable-bit findings from Windows Docker bind mounts.
- `pip check` passed, edited Python files reported no editor diagnostics, and
  `git diff --check` passed.
- A no-cache image build installed the pinned dependency lock successfully.
- Image-based `init-db` applied revisions `0001` through `0005` using a separate
  non-superuser migration login with `PGOPTIONS=-c role=genomic_migrator`.
  Image-based `alembic check` found no pending model/schema differences.
- All application tables were owned by `genomic_migrator`. The API, ETL, and
  migration logins had no superuser, database-creation, or role-creation privileges.
- Fixture ingestion ran inside the image using the ETL-only login:
  68 captured/processed, 55 loaded, nine partial, four quarantined, zero failed.
- The non-root image served REST/GraphQL/SOAP using only the API login. Liveness,
  readiness, OpenAPI and WSDL returned 200, and Docker reported healthy.
- The three protocols returned the same variant UUID and 64 association IDs.
  Nine unreconciled curated p-values stayed null. Without optional enrichment,
  coordinates/enrichment remained explicitly null. No external APIs were called.
- Documented GraphQL and SOAP examples executed successfully against that image.
  Contract tests check the examples, migration paths and advertised version.
- Full-row hashes across variants, associations, source links, raw objects and
  quality findings were unchanged by API requests.
- Direct API INSERT, CREATE TABLE and alembic-version UPDATE failed on privilege
  grounds **even after disabling the default read-only transaction setting**.
  ETL alembic-version UPDATE was also denied.
- Administrative HTTP routes were absent (404); API errors remained sanitized.
- Three health tests cover success, connection failure and missing canonical
  tables; liveness remains healthy when readiness fails.

Both final suites report only the two existing dependency deprecation warnings.
An earlier non-root Linux run also reported a harmless pytest cache write warning
because `/app` is not writable. The final Linux run used
`-o cache_dir=/tmp/pytest-cache` and had no cache warning.
Use Ruff's `--no-cache` flag inside the non-root image for the same reason.

Verification used only specifically named containers on the private
`genomic-checkpoint55-verify` network, with the API bound to localhost port 58007
and the disposable database on localhost port 55435. Verification containers and
network were stopped/removed afterward. The existing PostgreSQL regression
database on port 55432 was not reset or granted new roles.

Representative commands executed (database secrets injected via environment):

```powershell
docker build --no-cache -t genomic-api-checkpoint55 .
docker run --rm --network genomic-checkpoint55-verify --env DATABASE_URL --env PGOPTIONS genomic-api-checkpoint55 python -m genomic_platform.cli init-db
docker run --rm --network genomic-checkpoint55-verify --env DATABASE_URL --env PGOPTIONS genomic-api-checkpoint55 python -m alembic check
& .\.venv311\Scripts\python.exe -m ruff check src tests migrations
& .\.venv311\Scripts\python.exe -m pytest -q
# Linux tests mount only tests, case-studies and docs read-only, not the host .env:
docker run --rm --network genomic-checkpoint55-verify --env GENOMIC_TEST_DATABASE_URL --mount "type=bind,source=<absolute-tests>,target=/app/tests,readonly" --mount "type=bind,source=<absolute-case-studies>,target=/app/case-studies,readonly" --mount "type=bind,source=<absolute-docs>,target=/app/docs,readonly" genomic-api-checkpoint55 python -m pytest -q -o cache_dir=/tmp/pytest-cache
```

No dependency pins, scientific policies, or new migrations were needed. No Git
commit, tag, push, domain registration or public deployment was performed.
