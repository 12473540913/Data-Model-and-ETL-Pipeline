# Architecture

The platform uses source-specific connectors, immutable raw capture, normalization/validation, a canonical PostgreSQL model, explicit lineage, and protocol-thin APIs.

## Why PostgreSQL
The domain is relational and benefits from constraints, joins, transactional upserts, indexing, JSONB for source-specific extras, and direct notebook/BI access.

## Why platform UUIDs
rsIDs can merge, trait names vary, coordinates depend on assembly, and study IDs are source-owned. Platform UUIDs prevent one upstream namespace from becoming the global identity system.

## Why preserve raw payloads
Normalization rules will change. Checksummed raw material makes reprocessing reproducible without depending on current upstream responses.

## Future evolution
S3/MinIO for raw objects; dbt for SQL-first transformations; Dagster/Airflow for orchestration; OpenTelemetry for observability; Parquet/DuckDB/Spark or a warehouse only when scale justifies it.
