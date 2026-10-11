import base64
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from genomic_platform import cli
from genomic_platform.connectors.ensembl import EnsemblConnector, ensembl_retry_wait
from genomic_platform.domain.models import (
    Association,
    AssociationSource,
    EnrichmentObservation,
    IngestionRun,
    QualityIssue,
    RawObject,
    Variant,
    VariantEnrichment,
    VariantMapping,
)
from genomic_platform.etl import enrichment
from genomic_platform.etl.enrichment import EnrichmentError, enrich_variants
from genomic_platform.etl.loading import load_gwas_associations
from genomic_platform.etl.normalize import gwas_record_to_association
from genomic_platform.schema_migrations import migration_config

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ensembl-rs4988235.json"
# Public Ensembl variation response retrieved 2026-10-11:
# https://rest.ensembl.org/variation/homo_sapiens/rs4988235?content-type=application/json


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def variant_id(postgres_engine):
    with Session(postgres_engine) as session, session.begin():
        variant = Variant(rsid="rs4988235", alternate_alleles=[], metadata_json={})
        session.add(variant)
        session.flush()
        return variant.id


def mock_http(monkeypatch, handler):
    connectors = []

    def factory(**kwargs):
        connector = EnsemblConnector(**kwargs)
        connector.http.client = httpx.AsyncClient(
            base_url=connector.base_url,
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout", 30),
        )
        connectors.append(connector)
        return connector

    monkeypatch.setattr(enrichment, "EnsemblConnector", factory)
    return connectors


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def snapshot(session, model):
    return [
        {column.name: getattr(row, column.name) for column in model.__table__.columns}
        for row in session.scalars(
            select(model).order_by(model.__table__.primary_key.columns.values()[0])
        )
    ]


def test_real_structure_cli_repeated_evidence_and_gwas_immutability(
    postgres_engine,
    gwas_saved_records,
    monkeypatch,
    payload,
):
    load_gwas_associations(
        postgres_engine, [gwas_record_to_association(r) for r in gwas_saved_records]
    )
    with Session(postgres_engine) as session:
        before = {m: snapshot(session, m) for m in (Association, AssociationSource, RawObject)}
    clients = mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kwargs: postgres_engine)
    runner = CliRunner()
    for _ in range(2):
        result = runner.invoke(cli.app, ["enrich-variant", "rs4988235"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["enriched"] == 1
    assert all(client.http.client.is_closed for client in clients)
    with Session(postgres_engine) as session:
        for model in (Association, AssociationSource):
            assert snapshot(session, model) == before[model]
        assert [
            row
            for row in snapshot(session, RawObject)
            if row["source_record_type"] == "association"
        ] == before[RawObject]
        variant = session.scalar(select(Variant))
        assert (variant.assembly, variant.chromosome, variant.position) == (
            "GRCh38",
            "2",
            135851076,
        )
        assert (variant.reference_allele, variant.alternate_alleles) == ("G", ["A", "C", "T"])
        assert variant.variant_class == "SNP"
        assert variant.most_severe_consequence == "intron_variant"
        mapping = session.get(VariantMapping, variant.projection_mapping_id)
        assert mapping.payload_json == payload["mappings"][0]
        assert count(session, VariantEnrichment) == count(session, VariantMapping) == 1
        assert count(session, EnrichmentObservation) == 2
        raw = session.scalar(
            select(RawObject).where(RawObject.source_record_type == "ensembl_variation")
        )
        assert raw.sha256 == hashlib.sha256(raw.payload_text.encode()).hexdigest()
        assert json.loads(raw.payload_text) == payload
        assert (
            hashlib.sha256(base64.b64decode(raw.metadata_json["response_bytes_base64"])).hexdigest()
            == raw.metadata_json["response_bytes_sha256"]
        )
        assert raw.metadata_json["requested_assembly"] == "GRCh38"
        assert (
            session.scalar(
                select(func.count())
                .select_from(Association)
                .where(Association.p_value_status == "unreconciled", Association.p_value.is_(None))
            )
            == 9
        )
    summary = enrich_variants(postgres_engine)
    assert summary["selected"] == 0


@pytest.mark.parametrize(
    "case, expected",
    [
        ("multiple", "ambiguous"),
        ("no_assembly", "partial"),
        ("no_mapping", "partial"),
        ("bad_start", "partial"),
        ("wrong_system", "partial"),
        ("unknown_mapping", "ambiguous"),
        ("empty", "partial"),
    ],
)
def test_ambiguous_or_incomplete_preserves_every_mapping(
    postgres_engine,
    variant_id,
    monkeypatch,
    payload,
    case,
    expected,
):
    if case == "multiple":
        payload["mappings"].append({**payload["mappings"][0], "start": 135851077, "end": 135851077})
    elif case == "no_assembly":
        del payload["mappings"][0]["assembly_name"]
    elif case == "no_mapping":
        del payload["mappings"]
    elif case == "bad_start":
        payload["mappings"][0]["start"] = True
    elif case == "wrong_system":
        payload["mappings"][0]["coord_system"] = "contig"
    elif case == "unknown_mapping":
        payload["mappings"].append(None)
    else:
        payload["mappings"] = []
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    summary = enrich_variants(postgres_engine, rsid="rs4988235")
    assert summary[expected] == 1
    with Session(postgres_engine) as session:
        variant = session.get(Variant, variant_id)
        assert (variant.assembly, variant.chromosome, variant.position) == (None, None, None)
        mappings = list(session.scalars(select(VariantMapping).order_by(VariantMapping.ordinal)))
        assert len(mappings) == len(payload.get("mappings", []))
        assert count(session, QualityIssue) > 0


def test_assembly_selection_and_database_qualified_projection(
    postgres_engine, variant_id, monkeypatch, payload
):
    payload["mappings"].append(
        {**payload["mappings"][0], "assembly_name": "GRCh37", "start": 136608646, "end": 136608646}
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    mock_http(monkeypatch, handler)
    result = enrich_variants(postgres_engine, rsid="rs4988235", assembly="GRCh37")
    assert result["enriched"] == 1
    assert requests[0].url.host == "grch37.rest.ensembl.org"
    with Session(postgres_engine) as session:
        variant = session.get(Variant, variant_id)
        assert (variant.assembly, variant.position) == ("GRCh37", 136608646)
        assert count(session, VariantMapping) == 2
    result = enrich_variants(postgres_engine, rsid="rs4988235", assembly="GRCh38")
    assert result["conflict"] == 1
    with Session(postgres_engine) as session:
        assert session.get(Variant, variant_id).assembly == "GRCh37"
        assert "ENSEMBL_COORDINATE_CONFLICT" in set(session.scalars(select(QualityIssue.rule_code)))
    with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
        session.get(Variant, variant_id).assembly = "GRCh38"
        session.flush()
    with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
        session.get(Variant, variant_id).assembly = None
        session.flush()


def test_changed_evidence_retains_history_and_original_projection(
    postgres_engine, variant_id, monkeypatch, payload
):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    enrich_variants(postgres_engine, rsid="rs4988235")
    with Session(postgres_engine) as session:
        before = snapshot(session, RawObject)
        mapping_id = session.get(Variant, variant_id).projection_mapping_id
    payload["mappings"][0].update(start=135851077, end=135851077)
    result = enrich_variants(postgres_engine, rsid="rs4988235")
    assert result["changed_evidence"] == result["conflict"] == 1
    with Session(postgres_engine) as session:
        assert count(session, VariantEnrichment) == count(session, VariantMapping) == 2
        variant = session.get(Variant, variant_id)
        assert variant.position == 135851076 and variant.projection_mapping_id == mapping_id
        old = session.get(RawObject, before[0]["id"])
        assert {
            column.name: getattr(old, column.name) for column in RawObject.__table__.columns
        } == before[0]
        assert "ENSEMBL_EVIDENCE_CHANGED" in set(session.scalars(select(QualityIssue.rule_code)))
    with (
        pytest.raises(DBAPIError, match="discard enrichment"),
        postgres_engine.begin() as connection,
    ):
        config = migration_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "0003")


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (400, '{"error":"rs4988235 not found for homo_sapiens"}', "not_found"),
        (404, '{"error":"not found"}', "not_found"),
        (400, '{"error":"bad configuration"}', "failed"),
        (200, "not json", "failed"),
        (200, '{"name":"rs123","mappings":[]}', "failed"),
        (200, '{"name":"rs4988235","mappings":{}}', "failed"),
        (200, '{"name":"rs4988235","name":"rs4988235"}', "failed"),
        (200, '{"name":"rs4988235","MAF":NaN}', "failed"),
        (403, '{"error":"forbidden"}', "failed"),
    ],
)
def test_unavailable_malformed_and_permanent_http_errors(
    postgres_engine,
    variant_id,
    monkeypatch,
    status,
    body,
    expected,
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=body)

    mock_http(monkeypatch, handler)
    if expected == "failed":
        with pytest.raises(EnrichmentError) as failure:
            enrich_variants(postgres_engine, rsid="rs4988235")
        result = failure.value.summary
        assert result["status"] == "failed"
    else:
        result = enrich_variants(postgres_engine, rsid="rs4988235")
    assert result[expected] == 1
    assert len(calls) == 1
    with Session(postgres_engine) as session:
        assert session.get(Variant, variant_id).position is None
        assert count(session, RawObject) == count(session, VariantEnrichment) == 1
        assert session.scalar(select(EnrichmentObservation.status)) == expected


@pytest.mark.parametrize("failure_kind", ["timeout", "429", "503"])
def test_exhausted_transient_retries_and_retry_selection(
    postgres_engine, variant_id, monkeypatch, payload, failure_kind
):
    calls = []
    recovered = False
    monkeypatch.setattr("genomic_platform.connectors.ensembl.ensembl_retry_wait", lambda state: 0)

    def handler(request):
        calls.append(request)
        if recovered:
            return httpx.Response(200, json=payload)
        if failure_kind == "timeout":
            raise httpx.ReadTimeout("sensitive transport detail", request=request)
        return httpx.Response(int(failure_kind), json={"error": "unavailable"})

    mock_http(monkeypatch, handler)
    with pytest.raises(EnrichmentError) as failure:
        enrich_variants(postgres_engine, rsid="rs4988235", requests_per_second=15)
    assert failure.value.summary["failed"] == 1
    assert len(calls) == 4
    recovered = True
    result = enrich_variants(postgres_engine, requests_per_second=15)
    assert result["selected"] == result["enriched"] == 1
    assert len(calls) == 5
    with Session(postgres_engine) as session:
        assert count(session, EnrichmentObservation) == 2


@pytest.mark.parametrize("rsid", ["invalid", "rs999999999999"])
def test_invalid_or_missing_canonical_identifier_no_network(
    postgres_engine, variant_id, monkeypatch, rsid
):
    monkeypatch.setattr(
        enrichment, "EnsemblConnector", lambda **kwargs: pytest.fail("unexpected network")
    )
    with pytest.raises(EnrichmentError):
        enrich_variants(postgres_engine, rsid=rsid)
    with Session(postgres_engine) as session:
        assert count(session, IngestionRun) == 0


def test_bounded_selection_and_later_database_failure_preserve_commits(
    postgres_engine, variant_id, monkeypatch, payload
):
    with Session(postgres_engine) as session, session.begin():
        session.add(Variant(rsid="rs999", alternate_alleles=[], metadata_json={}))
    with postgres_engine.begin() as connection:
        connection.execute(
            text("""
          CREATE FUNCTION reject_mapping() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF NEW.payload_json->>'reject' = 'true' THEN RAISE EXCEPTION 'deliberate failure'; END IF;
            RETURN NEW;
          END $$;
        """)
        )
        connection.execute(
            text(
                "CREATE TRIGGER reject_mapping BEFORE INSERT ON variant_mapping FOR EACH ROW EXECUTE FUNCTION reject_mapping()"
            )
        )

    def handler(request):
        body = copy.deepcopy(payload)
        if request.url.path.endswith("rs999"):
            body["name"] = "rs999"
            body["mappings"][0]["reject"] = True
        return httpx.Response(200, json=body)

    mock_http(monkeypatch, handler)
    with pytest.raises(EnrichmentError) as failure:
        enrich_variants(postgres_engine, limit=2)
    assert failure.value.summary["processed"] == 1
    assert failure.value.summary["pending"] == 1
    with Session(postgres_engine) as session:
        assert count(session, VariantEnrichment) == count(session, RawObject) == 1
        assert session.get(Variant, variant_id).position == 135851076
        assert session.scalar(select(Variant).where(Variant.rsid == "rs999")).position is None


def test_existing_partial_coordinates_and_allele_conflicts(
    postgres_engine, variant_id, monkeypatch, payload
):
    with Session(postgres_engine) as session, session.begin():
        variant = session.get(Variant, variant_id)
        variant.assembly = "GRCh38"
        variant.chromosome = "2"
        variant.reference_allele = "T"
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    result = enrich_variants(postgres_engine, rsid="rs4988235")
    assert result["partial"] == 1
    with Session(postgres_engine) as session:
        variant = session.get(Variant, variant_id)
        assert variant.position == 135851076
        assert variant.reference_allele == "T" and variant.alternate_alleles == []
        assert "ENSEMBL_ANNOTATION_CONFLICT" in set(session.scalars(select(QualityIssue.rule_code)))


def test_raw_corruption_fails_reuse_without_projection_changes(
    postgres_engine, variant_id, monkeypatch, payload
):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    enrich_variants(postgres_engine, rsid="rs4988235")
    with Session(postgres_engine) as session, session.begin():
        session.scalar(select(RawObject)).payload_text = "{}"
    with pytest.raises(EnrichmentError):
        enrich_variants(postgres_engine, rsid="rs4988235")
    with Session(postgres_engine) as session:
        assert count(session, VariantEnrichment) == count(session, EnrichmentObservation) == 1
        assert session.get(Variant, variant_id).position == 135851076


def test_identical_missing_responses_preserve_individual_lookup_provenance(
    postgres_engine,
    variant_id,
    monkeypatch,
):
    with Session(postgres_engine) as session, session.begin():
        session.add(Variant(rsid="rs999", alternate_alleles=[], metadata_json={}))
    mock_http(monkeypatch, lambda request: httpx.Response(404, json={"error": "not found"}))
    result = enrich_variants(postgres_engine, limit=2)
    assert result["not_found"] == 2
    with Session(postgres_engine) as session:
        assert count(session, RawObject) == count(session, VariantEnrichment) == 2
        assert set(session.scalars(select(RawObject.source_record_id))) == {"rs4988235", "rs999"}
        assert count(session, EnrichmentObservation) == 2


def test_reference_reversion_detected_without_duplicate_evidence(
    postgres_engine,
    variant_id,
    monkeypatch,
    payload,
):
    original = copy.deepcopy(payload)
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    enrich_variants(postgres_engine, rsid="rs4988235")
    payload["most_severe_consequence"] = "upstream_gene_variant"
    second = enrich_variants(postgres_engine, rsid="rs4988235")
    assert second["changed_evidence"] == 1
    payload.clear()
    payload.update(original)
    reverted = enrich_variants(postgres_engine, rsid="rs4988235")
    assert reverted["reused_evidence"] == reverted["changed_evidence"] == 1
    with Session(postgres_engine) as session:
        assert count(session, VariantEnrichment) == 2
        assert count(session, EnrichmentObservation) == 3
        assert session.get(Variant, variant_id).most_severe_consequence == "intron_variant"


def test_cli_failure_is_nonzero_and_redacted(postgres_engine, variant_id, monkeypatch):
    monkeypatch.setattr("genomic_platform.connectors.ensembl.ensembl_retry_wait", lambda state: 0)

    def handler(request):
        raise httpx.ReadTimeout("password=private-secret", request=request)

    mock_http(monkeypatch, handler)
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kwargs: postgres_engine)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["enrich-variant", "rs4988235"])
    assert result.exit_code != 0
    assert "private-secret" not in result.output
    assert '"status": "failed"' in result.output


def test_successful_transient_retry_is_single_observation(
    postgres_engine,
    variant_id,
    monkeypatch,
    payload,
):
    calls = []
    waits = []

    def recording_wait(state):
        waits.append(ensembl_retry_wait(state))
        return 0

    monkeypatch.setattr("genomic_platform.connectors.ensembl.ensembl_retry_wait", recording_wait)

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate limited"})
            if len(calls) == 1
            else httpx.Response(200, json=payload)
        )

    mock_http(monkeypatch, handler)
    result = enrich_variants(postgres_engine, rsid="rs4988235")
    assert result["enriched"] == 1 and len(calls) == 2
    assert waits == [2]
    with Session(postgres_engine) as session:
        assert count(session, EnrichmentObservation) == count(session, RawObject) == 1


def test_concurrent_identical_lookups_are_idempotent(
    postgres_engine, variant_id, monkeypatch, payload
):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(lambda _: enrich_variants(postgres_engine, rsid="rs4988235"), range(2))
        )
    assert all(result["enriched"] == 1 for result in outcomes)
    with Session(postgres_engine) as session:
        assert (
            count(session, VariantEnrichment)
            == count(session, VariantMapping)
            == count(session, RawObject)
            == 1
        )
        assert count(session, EnrichmentObservation) == 2


def test_latest_failed_refresh_is_selected_for_retry(
    postgres_engine, variant_id, monkeypatch, payload
):
    available = True

    def handler(request):
        return (
            httpx.Response(200, json=payload)
            if available
            else httpx.Response(403, json={"error": "unavailable"})
        )

    mock_http(monkeypatch, handler)
    enrich_variants(postgres_engine, rsid="rs4988235")
    available = False
    with pytest.raises(EnrichmentError):
        enrich_variants(postgres_engine, rsid="rs4988235")
    available = True
    result = enrich_variants(postgres_engine, limit=1)
    assert result["selected"] == result["enriched"] == result["reused_evidence"] == 1


def test_bounded_unobserved_selection_advances_across_invocations(
    postgres_engine, variant_id, monkeypatch, payload
):
    with Session(postgres_engine) as session, session.begin():
        session.add(Variant(rsid="rs999", alternate_alleles=[], metadata_json={}))

    def handler(request):
        body = copy.deepcopy(payload)
        body["name"] = request.url.path.split("/")[-1]
        return httpx.Response(200, json=body)

    mock_http(monkeypatch, handler)
    for _ in range(2):
        result = enrich_variants(postgres_engine, limit=1)
        assert result["selected"] == result["enriched"] == 1
    assert enrich_variants(postgres_engine, limit=1)["selected"] == 0


@pytest.mark.parametrize("corruption", ["bytes", "mapping"])
def test_corrupt_archived_bytes_or_mappings_fail_explicitly(
    postgres_engine, variant_id, monkeypatch, payload, corruption
):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    enrich_variants(postgres_engine, rsid="rs4988235")
    with Session(postgres_engine) as session, session.begin():
        if corruption == "bytes":
            raw = session.scalar(select(RawObject))
            raw.metadata_json = {**raw.metadata_json, "response_bytes_base64": "bad"}
        else:
            mapping = session.scalar(select(VariantMapping))
            mapping.payload_json = {"corrupt": True}
    with pytest.raises(EnrichmentError):
        enrich_variants(postgres_engine, rsid="rs4988235")
    with Session(postgres_engine) as session:
        assert count(session, EnrichmentObservation) == 1
        assert session.get(Variant, variant_id).position == 135851076


def test_invalid_utf8_response_is_archived_but_not_projected(
    postgres_engine, variant_id, monkeypatch
):
    mock_http(
        monkeypatch,
        lambda request: httpx.Response(200, content=b'{"name":"rs4988235","value":"\xff"}'),
    )
    with pytest.raises(EnrichmentError):
        enrich_variants(postgres_engine, rsid="rs4988235")
    with Session(postgres_engine) as session:
        assert session.get(Variant, variant_id).position is None
        raw = session.scalar(select(RawObject))
        assert b"\xff" in base64.b64decode(raw.metadata_json["response_bytes_base64"])
