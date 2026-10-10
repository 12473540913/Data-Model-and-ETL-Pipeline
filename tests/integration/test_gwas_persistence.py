import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from genomic_platform.domain.models import (
    Association,
    AssociationSource,
    Base,
    DatasetRelease,
    IngestionRun,
    QualityIssue,
    RawObject,
    SourceSystem,
    Study,
    Trait,
    TraitSynonym,
    Variant,
)
from genomic_platform.etl.loading import load_gwas_associations
from genomic_platform.etl.normalize import gwas_record_to_association
from genomic_platform.schema_migrations import migration_config, upgrade_database

pytestmark = pytest.mark.integration


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_fixture_twice_preserves_counts_lineage_and_precision(postgres_engine, gwas_saved_records):
    dtos = [gwas_record_to_association(r) for r in gwas_saved_records]
    first = load_gwas_associations(postgres_engine, dtos, release_key="saved-rs4988235-v1")
    with Session(postgres_engine) as session:
        first_ids = set(session.scalars(select(Association.id)))
        first_links = {
            (link.association_id, link.source_record_id, link.raw_object_id, link.ingestion_run_id)
            for link in session.scalars(select(AssociationSource))
        }
    second = load_gwas_associations(postgres_engine, dtos, release_key="saved-rs4988235-v1")
    assert first.run_id != second.run_id
    assert (
        first.metrics
        == second.metrics
        == {
            "seen": 68,
            "loaded": 55,
            "partial": 9,
            "quarantined": 4,
            "duplicates": 0,
        }
    )
    with Session(postgres_engine) as session:
        assert count(session, SourceSystem) == 1
        assert count(session, DatasetRelease) == 1
        assert count(session, Variant) == 1
        assert count(session, Trait) == 25
        assert count(session, Study) == 63
        assert count(session, TraitSynonym) == 47
        assert count(session, Association) == count(session, AssociationSource) == 64
        assert count(session, IngestionRun) == 2
        assert count(session, RawObject) == 136
        assert count(session, QualityIssue) == 44
        assert set(session.scalars(select(Association.id))) == first_ids
        assert {
            (link.association_id, link.source_record_id, link.raw_object_id, link.ingestion_run_id)
            for link in session.scalars(select(AssociationSource))
        } == first_links
        assert all(
            run.status == "completed_with_issues" and run.finished_at is not None
            for run in session.scalars(select(IngestionRun))
        )
        assert session.scalar(select(Variant)).assembly is None
        by_id = {str(r["association_id"]): r for r in gwas_saved_records}
        for link in session.scalars(select(AssociationSource)):
            association = session.get(Association, link.association_id)
            raw = session.get(RawObject, link.raw_object_id)
            record = by_id[link.source_record_id]
            assert raw.association_id == association.id
            assert raw.ingestion_run_id == link.ingestion_run_id == first.run_id
            assert raw.sha256 == hashlib.sha256(raw.payload_text.encode("utf-8")).hexdigest()
            assert json.loads(raw.payload_text, parse_float=Decimal) == record
            assert link.source_payload["payload_text"] == raw.payload_text
            assert (
                session.get(Trait, association.trait_id).ontology_id
                == record["efo_traits"][0]["efo_id"]
            )
            assert session.get(Study, association.study_id).accession == record["accession_id"]
            assert association.metadata_json["reported_traits"] == record["reported_trait"]
            expected = Decimal(f"{record['pvalue_mantissa']}e{record['pvalue_exponent']}")
            assert raw.p_value_candidate == expected > 0
            assert Decimal(raw.p_value_text) == expected
            assert raw.p_value_mantissa == str(record["pvalue_mantissa"])
            assert raw.p_value_exponent == str(record["pvalue_exponent"])
            if record["p_value"] != expected:
                assert association.p_value is None
                assert association.p_value_status == "unreconciled"
                assert raw.disposition == "partial"
            else:
                assert association.p_value == expected
                assert association.p_value_status == "reported"
        tiny = list(session.scalars(select(RawObject).where(RawObject.p_value_text == "3e-1451")))
        assert len(tiny) == 2
        assert all(raw.p_value_candidate == Decimal("3e-1451") > 0 for raw in tiny)
        assert all(json.loads(raw.payload_text)["p_value"] == 0 for raw in tiny)
        assert all(raw.normalized_json["p_value"] == "3E-1451" for raw in tiny)
        assert (
            session.scalar(
                select(func.count())
                .select_from(QualityIssue)
                .where(QualityIssue.rule_code == "ASSOC_CONFLICTING_PVALUE")
            )
            == 18
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawObject)
                .where(RawObject.disposition == "quarantined", RawObject.association_id.is_(None))
            )
            == 8
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawObject)
                .where(
                    RawObject.ingestion_run_id == second.run_id,
                    RawObject.association_id.in_(first_ids),
                )
            )
            == 64
        )


def test_postgresql_numeric_exact_round_trip_and_limits(postgres_engine):
    with postgres_engine.begin() as connection:
        version = int(connection.scalar(text("SHOW server_version_num")))
        assert version // 10000 == 17
        for value in ["1e-65", "3e-1451", "1e-16383"]:
            returned = connection.scalar(text("SELECT CAST(:value AS NUMERIC)"), {"value": value})
            assert returned == Decimal(value) > 0
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(text("SELECT CAST(:value AS NUMERIC)"), {"value": "1e-16384"})
    columns = {c["name"]: c for c in inspect(postgres_engine).get_columns("association")}
    assert columns["p_value"]["type"].precision is None
    assert columns["p_value"]["type"].scale is None
    assert columns["effect_size"]["type"].scale is None


def test_valid_partial_records_and_out_of_range_numeric(postgres_engine):
    records = [
        {"association_id": "partial", "rs_id": "rs123", "reported_trait": ["Measured trait"]},
        {
            "association_id": "extreme",
            "rs_id": "rs123",
            "reported_trait": ["Measured trait"],
            "p_value": "1e-16384",
            "beta": "1.1234567890123456789 unit decrease",
        },
        {
            "association_id": "exact",
            "rs_id": "rs123",
            "reported_trait": ["Measured trait"],
            "p_value": "3e-1451",
        },
    ]
    result = load_gwas_associations(
        postgres_engine, [gwas_record_to_association(r) for r in records]
    )
    assert result.metrics["loaded"] == 2
    assert result.metrics["partial"] == 1
    with Session(postgres_engine) as session:
        assert count(session, Association) == 3
        assert count(session, Trait) == 1
        assert count(session, Study) == 0
        assert session.scalar(select(Trait)).ontology_id is None
        for raw in session.scalars(select(RawObject)):
            association = session.get(Association, raw.association_id)
            assert association.study_id is None
            assert association.effect_allele is None
            if raw.source_record_id == "extreme":
                assert raw.p_value_candidate is None
                assert raw.p_value_text == "1e-16384"
                assert association.p_value is None
                assert association.p_value_status == "unreconciled"
                assert association.effect_size == Decimal("1.1234567890123456789")
                assert association.metadata_json["effect_direction"] == "decrease"
            elif raw.source_record_id == "exact":
                assert association.p_value == Decimal("3e-1451") > 0
                assert association.p_value_status == "reported"


def test_invalid_and_compound_records_are_quarantined(postgres_engine, gwas_saved_records):
    multi = copy.deepcopy(gwas_saved_records[0])
    multi["association_id"] = "two-variants"
    multi["snp_allele"].append({"rs_id": "rs123", "effect_allele": "A"})
    multi["snp_effect_allele"].append("rs123-A")
    haplotype = {
        **gwas_saved_records[0],
        "association_id": "haplotype",
        "multi_snp_haplotype": True,
    }
    records = [
        {},
        {
            "association_id": "invalid-p",
            "rs_id": "rs123",
            "reported_trait": ["Trait"],
            "p_value": "NaN",
        },
        {"association_id": "invalid-rsid", "rs_id": "wrong", "reported_trait": ["Trait"]},
        multi,
        haplotype,
    ]
    result = load_gwas_associations(
        postgres_engine, [gwas_record_to_association(r) for r in records]
    )
    assert result.metrics["quarantined"] == 5
    with Session(postgres_engine) as session:
        assert count(session, Association) == count(session, AssociationSource) == 0
        assert count(session, Variant) == count(session, Trait) == count(session, Study) == 0
        assert count(session, RawObject) == 5
        assert all(
            raw.disposition == "quarantined" and raw.association_id is None
            for raw in session.scalars(select(RawObject))
        )
        assert {
            "ASSOC_MISSING_SOURCE_ID",
            "ASSOC_INVALID_NUMBER",
            "ASSOC_INVALID_RSID",
            "ASSOC_UNSUPPORTED_CARDINALITY",
            "ASSOC_UNSUPPORTED_COMPOUND",
        } <= set(session.scalars(select(QualityIssue.rule_code)))


def test_dataset_transaction_rollback_retains_failed_run(
    postgres_engine, gwas_saved_records, caplog
):
    def interrupted_dataset():
        yield gwas_record_to_association(gwas_saved_records[0])
        raise RuntimeError("deliberate dataset failure")

    with pytest.raises(RuntimeError, match="deliberate dataset failure"):
        load_gwas_associations(postgres_engine, interrupted_dataset())
    with Session(postgres_engine) as session:
        assert count(session, IngestionRun) == 1
        run = session.scalar(select(IngestionRun))
        assert run.status == "failed"
        assert run.finished_at is not None
        assert count(session, Association) == count(session, AssociationSource) == 0
        assert count(session, RawObject) == count(session, Variant) == count(session, Trait) == 0
        assert count(session, QualityIssue) == 1
        assert session.scalar(select(QualityIssue)).rule_code == "INGESTION_FAILED"
    assert "dataset transaction rolled back" in caplog.text


def test_changed_source_record_never_overwrites_existing_evidence(
    postgres_engine, gwas_saved_records
):
    original = gwas_saved_records[0]
    first = load_gwas_associations(postgres_engine, [gwas_record_to_association(original)])
    changed = copy.deepcopy(original)
    changed["pvalue_exponent"] = -66
    changed["p_value"] = Decimal("1e-66")
    second = load_gwas_associations(postgres_engine, [gwas_record_to_association(changed)])
    assert second.metrics["quarantined"] == 1
    with Session(postgres_engine) as session:
        assert count(session, Association) == count(session, AssociationSource) == 1
        assert session.scalar(select(Association)).p_value == Decimal("1e-65")
        assert session.scalar(select(AssociationSource)).ingestion_run_id == first.run_id
        assert (
            session.scalar(
                select(RawObject).where(RawObject.ingestion_run_id == second.run_id)
            ).disposition
            == "quarantined"
        )
        assert "ASSOC_SOURCE_RECORD_CHANGED" in set(session.scalars(select(QualityIssue.rule_code)))


def test_duplicate_payload_within_run_has_no_duplicate_observations(
    postgres_engine, gwas_saved_records
):
    dto = gwas_record_to_association(gwas_saved_records[0])
    result = load_gwas_associations(postgres_engine, [dto, dto])
    assert result.metrics["seen"] == 2
    assert result.metrics["duplicates"] == 1
    with Session(postgres_engine) as session:
        assert (
            count(session, RawObject)
            == count(session, AssociationSource)
            == count(session, Association)
            == 1
        )


def test_relationship_constraints_reject_cross_run_lineage(postgres_engine, gwas_saved_records):
    dtos = [gwas_record_to_association(gwas_saved_records[0])]
    first = load_gwas_associations(postgres_engine, dtos)
    second = load_gwas_associations(postgres_engine, dtos)
    with Session(postgres_engine) as session:
        link = session.scalar(select(AssociationSource))
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                AssociationSource(
                    association_id=link.association_id,
                    source_system_id=link.source_system_id,
                    source_record_id="wrong-lineage",
                    ingestion_run_id=second.run_id,
                    raw_object_id=link.raw_object_id,
                    source_payload={},
                )
            )
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                QualityIssue(
                    ingestion_run_id=second.run_id,
                    raw_object_id=link.raw_object_id,
                    entity_type="association",
                    entity_key="wrong",
                    rule_code="INVALID_LINEAGE",
                    severity="error",
                    message="Wrong run",
                    details_json={},
                )
            )
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                AssociationSource(
                    association_id=link.association_id,
                    source_system_id=link.source_system_id,
                    source_record_id="not-the-raw-record",
                    ingestion_run_id=first.run_id,
                    raw_object_id=link.raw_object_id,
                    source_payload={},
                )
            )
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                Association(
                    variant_id=uuid4(),
                    trait_id=uuid4(),
                    p_value=Decimal("1e-8"),
                    p_value_status="reported",
                    metadata_json={},
                )
            )
            session.flush()
        assert count(session, AssociationSource) == 1
        assert link.ingestion_run_id == first.run_id


def test_migrations_round_trip_and_metadata_parity(postgres_engine):
    config = migration_config()
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0001")
        columns = {c["name"]: c for c in inspect(connection).get_columns("association")}
        assert columns["p_value"]["type"].scale == 40
        upgrade_database(connection)
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
        assert len(Base.metadata.tables) == 18


def test_downgrade_refuses_to_discard_evidence(postgres_engine, gwas_saved_records):
    load_gwas_associations(postgres_engine, [gwas_record_to_association(gwas_saved_records[0])])
    with (
        pytest.raises(DBAPIError, match="discard provenance"),
        postgres_engine.begin() as connection,
    ):
        config = migration_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "0001")
    with Session(postgres_engine) as session:
        assert count(session, Association) == count(session, RawObject) == 1
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0003"


def test_database_write_failure_rolls_back_whole_dataset(postgres_engine, gwas_saved_records):
    with postgres_engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION reject_raw() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.source_record_id = 'reject' THEN
                    RAISE EXCEPTION 'deliberate database write failure';
                END IF;
                RETURN NEW;
            END $$;
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER reject_raw BEFORE INSERT ON raw_object
            FOR EACH ROW EXECUTE FUNCTION reject_raw();
        """)
        )
    rejected = {**gwas_saved_records[0], "association_id": "reject"}
    dtos = [gwas_record_to_association(gwas_saved_records[0]), gwas_record_to_association(rejected)]
    with pytest.raises(DBAPIError, match="deliberate database write failure"):
        load_gwas_associations(postgres_engine, dtos)
    with Session(postgres_engine) as session:
        assert (
            count(session, RawObject) == count(session, Association) == count(session, Variant) == 0
        )
        assert session.scalar(select(IngestionRun)).status == "failed"
        assert session.scalar(select(QualityIssue)).rule_code == "INGESTION_FAILED"


def test_removed_conflict_finding_cannot_reconcile_source_data(postgres_engine, gwas_saved_records):
    record = next(r for r in gwas_saved_records if r["p_value"] == 0)
    dto = gwas_record_to_association(record).model_copy(update={"normalization_findings": []})
    result = load_gwas_associations(postgres_engine, [dto])
    assert result.metrics["quarantined"] == 1
    with Session(postgres_engine) as session:
        assert count(session, Association) == 0
        assert session.scalar(select(RawObject)).p_value_candidate == Decimal("3e-1451")
        assert "ASSOC_DTO_EVIDENCE_MISMATCH" in set(session.scalars(select(QualityIssue.rule_code)))


def test_concurrent_reruns_do_not_duplicate_entities_or_links(postgres_engine, gwas_saved_records):
    barrier = Barrier(2)
    dto = gwas_record_to_association(gwas_saved_records[0])

    def ingest():
        barrier.wait(timeout=10)
        return load_gwas_associations(postgres_engine, [dto])

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(ingest) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert results[0].run_id != results[1].run_id
    with Session(postgres_engine) as session:
        assert count(session, IngestionRun) == count(session, RawObject) == 2
        assert count(session, Variant) == count(session, Trait) == count(session, Study) == 1
        assert count(session, Association) == count(session, AssociationSource) == 1


def test_study_upsert_fills_missing_fields_but_reports_conflicts(postgres_engine):
    base = {"rs_id": "rs123", "reported_trait": ["Trait"], "accession_id": "GCST123"}
    records = [
        {**base, "association_id": "one"},
        {**base, "association_id": "two", "pubmed_id": "123", "first_author": "Author A"},
        {**base, "association_id": "three", "pubmed_id": "456", "first_author": "Author B"},
    ]
    load_gwas_associations(postgres_engine, [gwas_record_to_association(r) for r in records])
    with Session(postgres_engine) as session:
        assert count(session, Study) == 1
        study = session.scalar(select(Study))
        assert study.pubmed_id == "123"
        assert study.author == "Author A"
        assert count(session, Association) == 3
        conflict = session.scalar(
            select(QualityIssue).where(QualityIssue.rule_code == "STUDY_METADATA_CONFLICT")
        )
        assert conflict is not None
        assert len(conflict.details_json["messages"]) == 2


def test_inconsistent_duplicate_dto_fails_without_partial_commit(
    postgres_engine, gwas_saved_records
):
    dto = gwas_record_to_association(gwas_saved_records[0])
    altered = dto.model_copy(update={"source_record_id": "invented"})
    with pytest.raises(ValueError, match="inconsistent normalized DTOs"):
        load_gwas_associations(postgres_engine, [dto, altered])
    with Session(postgres_engine) as session:
        assert count(session, Association) == count(session, RawObject) == 0
        assert session.scalar(select(IngestionRun)).status == "failed"


def test_variant_graphql_nullability_matches_unenriched_model():
    from genomic_platform.api.graphql_schema import schema

    assert "assembly: String\n" in str(schema)
    assert "assembly: String!" not in str(schema)


def test_existing_seeded_source_identity_is_reused(postgres_engine, gwas_saved_records):
    source_id = uuid4()
    with Session(postgres_engine) as session, session.begin():
        session.add(
            SourceSystem(
                id=source_id,
                code="GWAS_CATALOG",
                name="Previously seeded GWAS source",
                active=True,
            )
        )
    result = load_gwas_associations(
        postgres_engine,
        [gwas_record_to_association(gwas_saved_records[0])],
    )
    with Session(postgres_engine) as session:
        assert count(session, SourceSystem) == 1
        assert session.get(IngestionRun, result.run_id).source_system_id == source_id
        assert session.scalar(select(AssociationSource)).source_system_id == source_id


def test_float_only_pvalue_is_not_promoted_to_reconciled_numeric(postgres_engine):
    dto = gwas_record_to_association(
        {
            "association_id": "float-only",
            "rs_id": "rs123",
            "reported_trait": ["Trait"],
            "p_value": 1e-65,
        }
    )
    result = load_gwas_associations(postgres_engine, [dto])
    assert result.metrics["partial"] == 1
    with Session(postgres_engine) as session:
        association = session.scalar(select(Association))
        assert association.p_value is None
        assert association.p_value_status == "unreconciled"
        assert session.scalar(select(RawObject)).p_value_candidate == Decimal("1e-65")
        assert "ASSOC_FLOAT_PVALUE" in set(session.scalars(select(QualityIssue.rule_code)))


def test_upgrade_preserves_existing_baseline_data_and_lineage(postgres_engine):
    source_id, variant_id, trait_id, association_id, run_id, raw_id = [uuid4() for _ in range(6)]
    with postgres_engine.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "0001")
        metadata = MetaData()
        metadata.reflect(bind=connection)
        rows = [
            (
                "source_system",
                {"id": source_id, "code": "GWAS_CATALOG", "name": "Legacy source", "active": True},
            ),
            (
                "variant",
                {
                    "id": variant_id,
                    "rsid": "rs123",
                    "assembly": "GRCh38",
                    "alternate_alleles": [],
                    "metadata_json": {},
                },
            ),
            ("trait", {"id": trait_id, "ontology_id": "EFO_123", "name": "Trait"}),
            (
                "association",
                {
                    "id": association_id,
                    "variant_id": variant_id,
                    "trait_id": trait_id,
                    "p_value": Decimal("1e-30"),
                    "metadata_json": {},
                },
            ),
            (
                "ingestion_run",
                {
                    "id": run_id,
                    "source_system_id": source_id,
                    "pipeline_name": "legacy",
                    "started_at": datetime.now(UTC),
                    "status": "completed",
                    "metrics_json": {},
                },
            ),
            (
                "raw_object",
                {
                    "id": raw_id,
                    "ingestion_run_id": run_id,
                    "source_record_type": "association",
                    "source_record_id": "legacy-record",
                    "sha256": "a" * 64,
                    "storage_uri": "legacy:payload",
                    "metadata_json": {},
                    "created_at": datetime.now(UTC),
                },
            ),
            (
                "association_source",
                {
                    "association_id": association_id,
                    "source_system_id": source_id,
                    "source_record_id": "legacy-record",
                    "ingestion_run_id": run_id,
                    "raw_object_id": raw_id,
                    "source_payload": {"legacy": True},
                },
            ),
        ]
        for table_name, values in rows:
            connection.execute(metadata.tables[table_name].insert().values(**values))
        upgrade_database(connection)
    with Session(postgres_engine) as session:
        association = session.get(Association, association_id)
        assert association.p_value == Decimal("1e-30")
        assert association.p_value_status == "reported"
        assert session.get(RawObject, raw_id).association_id == association_id
        assert session.scalar(select(AssociationSource)).raw_object_id == raw_id
