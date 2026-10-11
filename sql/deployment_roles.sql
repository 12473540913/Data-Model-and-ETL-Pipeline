-- Bootstrap an EMPTY, dedicated PostgreSQL database as its administrator.
-- These NOLOGIN roles contain privileges, not credentials.
-- Stop on any failure; do not run this against an existing database blindly.
\set ON_ERROR_STOP on
SELECT current_database() AS database_name \gset

CREATE ROLE genomic_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE genomic_etl NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE genomic_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO genomic_migrator, genomic_etl, genomic_api;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO genomic_migrator;
GRANT USAGE ON SCHEMA public TO genomic_etl, genomic_api;

ALTER DEFAULT PRIVILEGES FOR ROLE genomic_migrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE genomic_migrator IN SCHEMA public
    GRANT SELECT ON TABLES TO genomic_api;
ALTER DEFAULT PRIVILEGES FOR ROLE genomic_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO genomic_etl;
ALTER DEFAULT PRIVILEGES FOR ROLE genomic_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO genomic_etl;

-- Objects must be created under genomic_migrator, not merely by a login that
-- inherits it. Use PGOPTIONS='-c role=genomic_migrator' for migration commands.
-- After migrations, run sql/deployment_grants.sql as genomic_migrator.
