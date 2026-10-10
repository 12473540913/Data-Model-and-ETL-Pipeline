import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from genomic_platform.schema_migrations import upgrade_database


@pytest.fixture
def postgres_engine():
    url = os.environ.get("GENOMIC_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set GENOMIC_TEST_DATABASE_URL to run real PostgreSQL integration tests.")
    parsed = make_url(url)
    if parsed.database != "genomics_test" or parsed.get_backend_name() != "postgresql":
        pytest.fail(
            "Integration tests require a dedicated PostgreSQL database named genomics_test."
        )
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with engine.begin() as connection:
            upgrade_database(connection)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
