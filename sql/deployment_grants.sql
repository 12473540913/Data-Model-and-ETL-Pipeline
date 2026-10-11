-- Run AFTER migrations as genomic_migrator (or the database administrator).
\set ON_ERROR_STOP on
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM genomic_api, genomic_etl;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO genomic_api;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO genomic_etl;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO genomic_etl;

-- Schema bookkeeping is readable, not writable, by either application role.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON public.alembic_version FROM genomic_api, genomic_etl;
