import hashlib
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from genomic_platform import cli
from genomic_platform.connectors.gwas_catalog import GWASCatalogConnector
from genomic_platform.domain.models import (
    Association,
    AssociationSource,
    IngestionBatch,
    IngestionRun,
    ProcessingObservation,
    QualityIssue,
    RawObject,
)
from genomic_platform.etl import ingestion
from genomic_platform.etl.ingestion import IngestionError, ingest_gwas, replay_gwas, resume_gwas
from genomic_platform.etl.loading import load_gwas_associations
from genomic_platform.etl.normalize import gwas_record_to_association
from genomic_platform.etl.raw_store import encode_raw_json

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).resolve().parents[2] / "case-studies" / "rs4988235-associations.json"


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def raw_snapshot(session):
    return {
        r.id: (
            r.payload_text,
            r.sha256,
            r.ingestion_run_id,
            r.disposition,
            r.association_id,
            r.normalized_json,
            r.metadata_json,
        )
        for r in session.scalars(select(RawObject))
    }


def mock_connector(monkeypatch, handler):
    connectors = []

    def factory(timeout=None):
        connector = GWASCatalogConnector(timeout=timeout)
        # Replace only the transport, leaving actual request/pagination/retry code in use.
        connector.http.client = httpx.AsyncClient(
            base_url=connector.base_url,
            transport=httpx.MockTransport(handler),
            timeout=timeout or 30,
        )
        connectors.append(connector)
        return connector

    monkeypatch.setattr(ingestion, "GWASCatalogConnector", factory)
    return connectors


@pytest.mark.parametrize("empty_alias", [False, True])
def test_capture_source_identifier_matches_normalizer(
    postgres_engine, gwas_saved_records, monkeypatch, empty_alias
):
    record = dict(gwas_saved_records[0])
    identifier = record["association_id"]
    record["association_id"] = "" if empty_alias else f"  {identifier}  "
    if empty_alias:
        record["associationId"] = identifier
    mock_connector(
        monkeypatch,
        lambda request: httpx.Response(200, text=encode_raw_json({"content": [record]})),
    )
    result = ingest_gwas(postgres_engine, mode="variant", identifier="rs4988235")
    assert result["loaded"] + result["partial"] == 1
    with Session(postgres_engine) as session:
        raw = session.scalar(select(RawObject))
        assert raw.source_record_id == str(identifier)
        assert raw.sha256 == hashlib.sha256(raw.payload_text.encode()).hexdigest()
        assert count(session, AssociationSource) == 1


def test_file_cli_replay_cli_and_idempotence(postgres_engine, monkeypatch):
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kwargs: postgres_engine)
    runner = CliRunner()
    first = runner.invoke(cli.app, ["ingest-gwas-file", str(FIXTURE), "--batch-size", "17"])
    assert first.exit_code == 0, first.output
    summary = json.loads(first.stdout)
    assert (summary["loaded"], summary["partial"], summary["quarantined"]) == (55, 9, 4)
    assert summary["processed"] == summary["captured"] == 68
    assert summary["pending"] == summary["failed"] == 0
    origin = UUID(summary["run_id"])
    with Session(postgres_engine) as session:
        snapshot = raw_snapshot(session)
        links = {
            (a.association_id, a.raw_object_id, a.ingestion_run_id)
            for a in session.scalars(select(AssociationSource))
        }
        assert count(session, IngestionBatch) == 8
        for raw in session.scalars(select(RawObject)):
            assert raw.metadata_json["acquisition"] == "file"
            assert len(raw.metadata_json["file_sha256"]) == 64
    # Replay is network-independent, including replay of a replay.
    monkeypatch.setattr(
        ingestion, "GWASCatalogConnector", lambda **kwargs: pytest.fail("Replay attempted network")
    )
    result = runner.invoke(cli.app, ["replay-gwas", str(origin), "--batch-size", "13"])
    assert result.exit_code == 0, result.output
    replay = json.loads(result.stdout)
    assert replay["originating_run_id"] == str(origin)
    assert (replay["loaded"], replay["partial"], replay["quarantined"]) == (55, 9, 4)
    again = runner.invoke(cli.app, ["replay-gwas", replay["run_id"]])
    assert again.exit_code == 0, again.output
    with Session(postgres_engine) as session:
        assert raw_snapshot(session) == snapshot
        assert count(session, RawObject) == 68
        assert count(session, Association) == count(session, AssociationSource) == 64
        assert count(session, IngestionRun) == 3
        assert count(session, ProcessingObservation) == 204
        assert {
            (a.association_id, a.raw_object_id, a.ingestion_run_id)
            for a in session.scalars(select(AssociationSource))
        } == links
        assert (
            session.scalar(
                select(func.count())
                .select_from(Association)
                .where(Association.p_value_status == "unreconciled", Association.p_value.is_(None))
            )
            == 9
        )
        tiny = session.scalar(select(RawObject).where(RawObject.p_value_text == "3e-1451"))
        assert tiny.p_value_candidate == Decimal("3e-1451") > 0
        assert tiny.sha256 == hashlib.sha256(tiny.payload_text.encode()).hexdigest()
        assert "ASSOC_HISTORICAL_QUARANTINE" in set(session.scalars(select(QualityIssue.rule_code)))
    repeated = runner.invoke(cli.app, ["ingest-gwas-file", str(FIXTURE)])
    assert repeated.exit_code == 0, repeated.output
    with Session(postgres_engine) as session:
        assert count(session, RawObject) == 136
        assert count(session, Association) == 64


@pytest.mark.parametrize(
    "mode, identifier, expected_filter",
    [
        ("trait", "EFO_0004338", "efo_trait"),
        ("variant", "rs4988235", "rs_id"),
    ],
)
def test_live_ingestion_with_real_pagination_and_metadata(
    postgres_engine, gwas_saved_records, monkeypatch, mode, identifier, expected_filter
):
    calls = []

    def handler(request):
        calls.append(request)
        if "page" not in request.url.params:
            assert request.url.params[expected_filter] == identifier
            return httpx.Response(
                200,
                text=encode_raw_json(
                    {
                        "content": gwas_saved_records[:34],
                        "_links": {"next": {"href": "/associations?page=2"}},
                    }
                ),
            )
        return httpx.Response(200, text=encode_raw_json({"results": gwas_saved_records[34:]}))

    connectors = mock_connector(monkeypatch, handler)
    summary = ingest_gwas(postgres_engine, mode=mode, identifier=identifier, batch_size=20)
    assert len(calls) == 2
    assert connectors[0].http.client.is_closed
    assert (summary["loaded"], summary["partial"], summary["quarantined"]) == (55, 9, 4)
    with Session(postgres_engine) as session:
        assert {r.metadata_json["request"]["page"] for r in session.scalars(select(RawObject))} == {
            1,
            2,
        }
        assert all(
            r.metadata_json["query"]["identifier"] == identifier
            for r in session.scalars(select(RawObject))
        )
        assert (
            len(
                {
                    a.metadata_json["reported_traits"][0]
                    for a in session.scalars(select(Association))
                }
            )
            > 1
        )
        assert count(session, RawObject) == 68


def test_capture_failure_resumes_from_query_without_skipping_pages(
    postgres_engine, gwas_saved_records, monkeypatch
):
    fail = True
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "page" in request.url.params:
            if fail:
                return httpx.Response(400, text="private-token-secret-must-not-leak")
            return httpx.Response(200, text=encode_raw_json({"content": gwas_saved_records[34:]}))
        return httpx.Response(
            200,
            text=encode_raw_json(
                {"content": gwas_saved_records[:34], "_links": {"next": "/associations?page=2"}}
            ),
        )

    mock_connector(monkeypatch, handler)
    with pytest.raises(IngestionError) as failure:
        ingest_gwas(postgres_engine, mode="variant", identifier="rs4988235", batch_size=20)
    summary = failure.value.summary
    assert summary["status"] == "failed"
    assert summary["captured"] == summary["pending"] == 34
    assert summary["processed"] == 0
    assert not summary["capture_complete"]
    assert "private-token" not in str(failure.value)
    origin = UUID(summary["run_id"])
    with Session(postgres_engine) as session:
        prior = {r.id: (r.sha256, r.payload_text) for r in session.scalars(select(RawObject))}
        assert count(session, Association) == 0
        assert (
            session.get(IngestionRun, origin).metadata_json["source_checkpoint"]["request"]["page"]
            == 1
        )
    fail = False
    resumed = resume_gwas(postgres_engine, origin)
    assert resumed["attempt"] == 2
    assert resumed["pending"] == resumed["failed"] == 0
    assert resumed["captured"] == resumed["processed"] == 68
    assert resumed["failed_batches"] == 1
    with Session(postgres_engine) as session:
        assert count(session, RawObject) == 68
        assert count(session, IngestionRun) == 1
        assert all(
            (session.get(RawObject, key).sha256, session.get(RawObject, key).payload_text) == value
            for key, value in prior.items()
        )
    assert len(calls) == 4  # complete query is safely repeated


def test_processing_failure_committed_batches_survive_and_resume(postgres_engine, monkeypatch):
    original = ingestion._process_batch
    calls = 0

    def fail_second(engine, run_id, batch_size):
        nonlocal calls
        calls += 1
        if calls == 2:
            with engine.begin() as connection:
                connection.execute(text("SELECT 1 / 0"))
        return original(engine, run_id, batch_size)

    monkeypatch.setattr(ingestion, "_process_batch", fail_second)
    with pytest.raises(IngestionError) as error:
        ingest_gwas(postgres_engine, mode="file", path=FIXTURE, batch_size=17)
    summary = error.value.summary
    assert summary["status"] == "failed"
    assert summary["processed"] == 17
    assert summary["pending"] == 51
    assert summary["failed"] == 17
    origin = UUID(summary["run_id"])
    with Session(postgres_engine) as session:
        assert count(session, ProcessingObservation) == 17
        assert count(session, RawObject) == 68
        committed_ids = set(session.scalars(select(ProcessingObservation.id)))
        snapshot = {(r.id, r.sha256) for r in session.scalars(select(RawObject))}
    monkeypatch.setattr(ingestion, "_process_batch", original)
    # No file or source access is required once capture is complete.
    monkeypatch.setattr(
        ingestion, "_capture_file", lambda *args: pytest.fail("Resume refetched captured input")
    )
    completed = resume_gwas(postgres_engine, origin)
    assert completed["status"] == "completed_with_issues"
    assert completed["processed"] == 68 and completed["pending"] == completed["failed"] == 0
    again = resume_gwas(postgres_engine, origin)
    assert again == completed
    with Session(postgres_engine) as session:
        assert count(session, IngestionRun) == 1
        assert count(session, Association) == 64
        assert count(session, ProcessingObservation) == 68
        assert committed_ids <= set(session.scalars(select(ProcessingObservation.id)))
        assert {(r.id, r.sha256) for r in session.scalars(select(RawObject))} == snapshot
        assert count(session, IngestionBatch) == 9  # 4 capture, 4 successful processing, 1 failure


def test_actual_write_failure_rolls_back_entire_processing_batch(postgres_engine):
    with postgres_engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION reject_processing() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'sensitive-database-detail'; END $$;
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER reject_processing BEFORE INSERT ON processing_observation
            FOR EACH ROW EXECUTE FUNCTION reject_processing();
        """)
        )
    with pytest.raises(IngestionError) as failure:
        ingest_gwas(postgres_engine, mode="file", path=FIXTURE, batch_size=17)
    assert "sensitive-database-detail" not in str(failure.value)
    run_id = UUID(failure.value.summary["run_id"])
    with Session(postgres_engine) as session:
        assert count(session, RawObject) == 68
        assert count(session, ProcessingObservation) == count(session, Association) == 0
        assert all(r.disposition == "captured" for r in session.scalars(select(RawObject)))
    with postgres_engine.begin() as connection:
        connection.execute(text("DROP TRIGGER reject_processing ON processing_observation"))
    result = resume_gwas(postgres_engine, run_id)
    assert result["processed"] == 68


def test_replay_legacy_atomic_run_without_new_raw(postgres_engine, gwas_saved_records):
    original = load_gwas_associations(
        postgres_engine, [gwas_record_to_association(r) for r in gwas_saved_records]
    )
    with Session(postgres_engine) as session:
        snapshot = raw_snapshot(session)
    result = replay_gwas(postgres_engine, original.run_id, batch_size=19)
    assert result["processed"] == 68
    assert result["quarantined"] == 4
    with Session(postgres_engine) as session:
        assert raw_snapshot(session) == snapshot
        assert count(session, RawObject) == 68
        assert count(session, ProcessingObservation) == 68
        assert count(session, AssociationSource) == 64


def test_replay_checksums_and_resume_after_integrity_repair(postgres_engine):
    first = ingest_gwas(postgres_engine, mode="file", path=FIXTURE)
    with Session(postgres_engine) as session, session.begin():
        raw = session.scalar(select(RawObject).order_by(RawObject.id))
        raw_id, payload = raw.id, raw.payload_text
        raw.payload_text = '{"tampered":true}'
    with pytest.raises(IngestionError, match="checksum") as failure:
        replay_gwas(postgres_engine, UUID(first["run_id"]))
    replay_id = UUID(failure.value.summary["run_id"])
    with Session(postgres_engine) as session:
        assert count(session, RawObject) == 68
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProcessingObservation)
                .where(ProcessingObservation.ingestion_run_id == replay_id)
            )
            == 0
        )
        assert session.get(IngestionRun, replay_id).status == "failed"
    with Session(postgres_engine) as session, session.begin():
        session.get(RawObject, raw_id).payload_text = payload
    assert resume_gwas(postgres_engine, replay_id)["processed"] == 68


@pytest.mark.parametrize(
    "body",
    ['{"unexpected": []}', '{"content":[null]}', '{"content":[],"content":[]}', '{"content": ['],
)
def test_malformed_http_is_a_failed_run(postgres_engine, monkeypatch, body):
    mock_connector(monkeypatch, lambda request: httpx.Response(200, text=body))
    with pytest.raises(IngestionError) as failure:
        ingest_gwas(postgres_engine, mode="variant", identifier="rs4988235")
    assert failure.value.summary["status"] == "failed"
    with Session(postgres_engine) as session:
        assert count(session, Association) == count(session, RawObject) == 0


def test_cli_invalid_inputs_database_and_http_errors_are_safe(postgres_engine, monkeypatch):
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kwargs: postgres_engine)
    runner = CliRunner()
    invalid = runner.invoke(cli.app, ["ingest-gwas-variant", "wrong"])
    assert invalid.exit_code == 1 and "Expected rs" in invalid.output
    invalid_uuid = runner.invoke(cli.app, ["replay-gwas", "not-a-uuid"])
    assert invalid_uuid.exit_code != 0
    mock_connector(monkeypatch, lambda request: httpx.Response(401, text="password=hidden"))
    failed = runner.invoke(cli.app, ["ingest-gwas-trait", "EFO_123"])
    assert failed.exit_code == 1 and '"status": "failed"' in failed.output
    assert "password=hidden" not in failed.output
    assert "local_test_only" not in failed.output
    unknown = runner.invoke(cli.app, ["replay-gwas", str(uuid4())])
    assert unknown.exit_code == 1 and "not found" in unknown.output
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(
        cli,
        "create_engine",
        lambda *a, **kw: (_ for _ in ()).throw(
            OperationalError("credential-secret", {}, Exception("password=secret"))
        ),
    )
    failed_db = runner.invoke(cli.app, ["ingest-gwas-file", str(FIXTURE)])
    assert failed_db.exit_code == 1 and "PostgreSQL operation failed" in failed_db.output
    assert "credential-secret" not in failed_db.output and "password=secret" not in failed_db.output


def test_capture_file_malformed_tail_is_not_success_and_preserves_captured(
    postgres_engine, tmp_path, gwas_saved_records
):
    path = tmp_path / "broken.json"
    path.write_text("[" + encode_raw_json(gwas_saved_records[0]) + ", invalid]", encoding="utf-8")
    with pytest.raises(IngestionError) as failure:
        ingest_gwas(postgres_engine, mode="file", path=path, batch_size=17)
    assert failure.value.summary["captured"] == 1
    assert failure.value.summary["status"] == "failed"
    path.write_text(encode_raw_json(gwas_saved_records), encoding="utf-8")
    with pytest.raises(IngestionError, match="changed"):
        resume_gwas(postgres_engine, UUID(failure.value.summary["run_id"]))
    replay = replay_gwas(postgres_engine, UUID(failure.value.summary["run_id"]))
    assert replay["processed"] == 1
    with Session(postgres_engine) as session:
        assert count(session, RawObject) == 1
        assert count(session, AssociationSource) == count(session, Association) == 1


def test_large_dataset_is_bounded_and_idempotent(postgres_engine, tmp_path):
    path = tmp_path / "large.json"
    path.write_text(
        encode_raw_json(
            [
                {
                    "association_id": f"scale-{i}",
                    "rs_id": "rs123",
                    "reported_trait": ["Trait"],
                    "p_value": "1e-65",
                }
                for i in range(1201)
            ]
        ),
        encoding="utf-8",
    )
    result = ingest_gwas(postgres_engine, mode="file", path=path, batch_size=500)
    assert result["processed"] == result["loaded"] == 1201
    with Session(postgres_engine) as session:
        assert count(session, IngestionBatch) == 6
        processing_counts = [
            b.metrics_json["processed"]
            for b in session.scalars(
                select(IngestionBatch).where(IngestionBatch.phase == "processing")
            )
        ]
        assert sorted(processing_counts) == [201, 500, 500]
    assert replay_gwas(postgres_engine, UUID(result["run_id"]), batch_size=500)["processed"] == 1201
    with Session(postgres_engine) as session:
        assert (
            count(session, RawObject)
            == count(session, Association)
            == count(session, AssociationSource)
            == 1201
        )


def test_changed_source_versions_in_one_capture_are_both_quarantined(postgres_engine, tmp_path):
    base = {"association_id": "same-id", "rs_id": "rs123", "reported_trait": ["Trait"]}
    path = tmp_path / "changed.json"
    path.write_text(
        encode_raw_json(
            [
                {**base, "p_value": "1e-65"},
                {**base, "p_value": "1e-66"},
            ]
        ),
        encoding="utf-8",
    )
    result = ingest_gwas(postgres_engine, mode="file", path=path, batch_size=1)
    assert result["quarantined"] == 2
    with Session(postgres_engine) as session:
        assert count(session, RawObject) == 2
        assert count(session, Association) == 0
        assert set(session.scalars(select(QualityIssue.rule_code))) >= {
            "ASSOC_SOURCE_RECORD_CHANGED"
        }
    assert replay_gwas(postgres_engine, UUID(result["run_id"]))["quarantined"] == 2


def test_run_lock_rejects_concurrent_resume(postgres_engine):
    result = ingest_gwas(postgres_engine, mode="file", path=FIXTURE)
    run_id = UUID(result["run_id"])
    with (
        ingestion._run_lock(postgres_engine, run_id),
        pytest.raises(IngestionError, match="already being processed"),
    ):
        resume_gwas(postgres_engine, run_id)
    assert resume_gwas(postgres_engine, run_id)["processed"] == 68


@pytest.mark.parametrize(
    "body",
    [
        '{"content":[],"_links":{"next":{"unexpected":"link"}}}',
        '{"content":[],"_links":{"next":"https://other.example/associations"}}',
        '{"content":[],"_links":{"next":"/associations"}}',
        '{"content":[],"_links":{"next":42}}',
        '{"content":[],"_links":["bad"]}',
    ],
)
def test_invalid_pagination_fails_run(postgres_engine, monkeypatch, body):
    mock_connector(monkeypatch, lambda request: httpx.Response(200, text=body))
    with pytest.raises(IngestionError) as failure:
        ingest_gwas(postgres_engine, mode="variant", identifier="rs4988235")
    assert failure.value.summary["status"] == "failed"


def test_transient_request_retries_are_bounded_and_idempotent(postgres_engine, monkeypatch):
    from tenacity import wait_none

    from genomic_platform.connectors.http import ResilientHttpClient

    monkeypatch.setattr(ResilientHttpClient.get.retry, "wait", wait_none())
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return (
            httpx.Response(503)
            if calls < 3
            else httpx.Response(
                200,
                text='{"content":[{"association_id":"retry","rs_id":"rs123","reported_trait":["Trait"]}]}',
            )
        )

    mock_connector(monkeypatch, handler)
    result = ingest_gwas(postgres_engine, mode="variant", identifier="rs123")
    assert calls == 3 and result["loaded"] == 1
    with Session(postgres_engine) as session:
        assert count(session, RawObject) == count(session, AssociationSource) == 1


def test_exhausted_timeout_fails_explicitly(postgres_engine, monkeypatch):
    from tenacity import wait_none

    from genomic_platform.connectors.http import ResilientHttpClient

    monkeypatch.setattr(ResilientHttpClient.get.retry, "wait", wait_none())
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("secret-token", request=request)

    mock_connector(monkeypatch, handler)
    with pytest.raises(IngestionError) as failure:
        ingest_gwas(postgres_engine, mode="variant", identifier="rs123")
    assert calls == 4
    assert "secret-token" not in str(failure.value)
    assert failure.value.summary["status"] == "failed"


def test_capture_batch_write_failure_and_resume(postgres_engine, tmp_path):
    path = tmp_path / "capture.json"
    path.write_text(
        encode_raw_json(
            [
                {"association_id": str(i), "rs_id": "rs123", "reported_trait": ["Trait"]}
                for i in range(3)
            ]
        ),
        encoding="utf-8",
    )
    with postgres_engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION reject_capture() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.source_record_id = '2' THEN RAISE EXCEPTION 'write rejected'; END IF;
            RETURN NEW; END $$;
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER reject_capture BEFORE INSERT ON raw_object
            FOR EACH ROW EXECUTE FUNCTION reject_capture();
        """)
        )
    with pytest.raises(IngestionError) as failure:
        ingest_gwas(postgres_engine, mode="file", path=path, batch_size=2)
    assert failure.value.summary["captured"] == 2
    assert not failure.value.summary["capture_complete"]
    run_id = UUID(failure.value.summary["run_id"])
    with postgres_engine.begin() as connection:
        connection.execute(text("DROP TRIGGER reject_capture ON raw_object"))
    resumed = resume_gwas(postgres_engine, run_id)
    assert resumed["captured"] == resumed["processed"] == 3
    with Session(postgres_engine) as session:
        assert count(session, RawObject) == count(session, Association) == 3
        assert count(session, IngestionRun) == 1


def test_abrupt_interruption_can_resume_running_run(postgres_engine, monkeypatch):
    original = ingestion._process_batch
    calls = 0

    def interrupted(engine, run_id, batch_size):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        return original(engine, run_id, batch_size)

    monkeypatch.setattr(ingestion, "_process_batch", interrupted)
    with pytest.raises(IngestionError) as failure:
        ingest_gwas(postgres_engine, mode="file", path=FIXTURE, batch_size=17)
    run_id = UUID(failure.value.summary["run_id"])
    assert failure.value.summary["processed"] == 17
    with Session(postgres_engine) as session, session.begin():
        session.get(IngestionRun, run_id).status = "running"
    monkeypatch.setattr(ingestion, "_process_batch", original)
    assert resume_gwas(postgres_engine, run_id)["processed"] == 68


def test_large_paginated_capture_commits_between_requests(postgres_engine, monkeypatch):
    observed_captures = []

    def handler(request):
        page = int(request.url.params.get("page", 1))
        with Session(postgres_engine) as session:
            observed_captures.append(count(session, RawObject))
            assert count(session, Association) == 0
        records = [
            {"association_id": f"page-{page}-{i}", "rs_id": "rs123", "reported_trait": ["Trait"]}
            for i in range(50)
        ]
        payload = {"content": records}
        if page < 3:
            payload["_links"] = {"next": f"/associations?page={page + 1}"}
        return httpx.Response(200, text=encode_raw_json(payload))

    mock_connector(monkeypatch, handler)
    result = ingest_gwas(postgres_engine, mode="variant", identifier="rs123", batch_size=50)
    assert observed_captures == [0, 50, 100]
    assert result["processed"] == 150


def test_replay_selection_does_not_grow_when_origin_later_resumes(postgres_engine, monkeypatch):
    fail = True

    def handler(request):
        if "page" not in request.url.params:
            return httpx.Response(
                200,
                text='{"content":[{"association_id":"one","rs_id":"rs123","reported_trait":["Trait"]}],"_links":{"next":"/associations?page=2"}}',
            )
        return (
            httpx.Response(400)
            if fail
            else httpx.Response(
                200,
                text='{"content":[{"association_id":"two","rs_id":"rs123","reported_trait":["Trait"]}]}',
            )
        )

    mock_connector(monkeypatch, handler)
    with pytest.raises(IngestionError) as failure:
        ingest_gwas(postgres_engine, mode="variant", identifier="rs123", batch_size=1)
    origin = UUID(failure.value.summary["run_id"])
    replay_id = ingestion._start_run(postgres_engine, {"mode": "replay", "batch_size": 1}, origin)
    fail = False
    assert resume_gwas(postgres_engine, origin)["captured"] == 2
    result = resume_gwas(postgres_engine, replay_id)
    assert result["captured"] == result["processed"] == 1
    with Session(postgres_engine) as session:
        assert count(session, RawObject) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProcessingObservation)
                .where(ProcessingObservation.ingestion_run_id == replay_id)
            )
            == 1
        )
