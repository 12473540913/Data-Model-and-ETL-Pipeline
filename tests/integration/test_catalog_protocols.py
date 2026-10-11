import copy
import json
from datetime import datetime
from pathlib import Path
from types import UnionType
from typing import get_args, get_origin
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy import event, select, text
from sqlalchemy.exc import InternalError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from starlette.websockets import WebSocketDisconnect

from genomic_platform.api.deps import get_session, read_session
from genomic_platform.api.main import app
from genomic_platform.connectors.ensembl import EnsemblConnector
from genomic_platform.domain.models import (
    Association,
    Gene,
    Study,
    Variant,
    VariantGene,
)
from genomic_platform.etl import enrichment
from genomic_platform.etl.enrichment import enrich_variants
from genomic_platform.etl.ingestion import ingest_gwas
from genomic_platform.services import contracts as c
from genomic_platform.soap.server import NAMESPACE, create_soap_app

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
XML_NS = "http://schemas.xmlsoap.org/soap/envelope/"


@pytest.fixture
def api_clients(postgres_engine):
    factory = sessionmaker(postgres_engine)

    def session_dependency():
        with read_session(factory) as session:
            yield session

    app.dependency_overrides[get_session] = session_dependency
    soap_app = create_soap_app(factory)
    try:
        with (
            TestClient(app, raise_server_exceptions=False) as rest,
            httpx.Client(
                transport=httpx.WSGITransport(app=soap_app), base_url="http://testserver/"
            ) as soap,
        ):
            yield rest, soap
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def catalog_data(postgres_engine, monkeypatch):
    summary = ingest_gwas(
        postgres_engine, mode="file", path=ROOT / "case-studies" / "rs4988235-associations.json"
    )
    payload = json.loads((ROOT / "tests" / "fixtures" / "ensembl-rs4988235.json").read_text())

    def factory(**kwargs):
        connector = EnsemblConnector(**kwargs)
        response = copy.deepcopy(payload)
        if kwargs["assembly"] == "GRCh37":
            response["mappings"][0].update(assembly_name="GRCh37", start=136608646, end=136608646)
        connector.http.client = httpx.AsyncClient(
            base_url=connector.base_url,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)),
        )
        return connector

    monkeypatch.setattr(enrichment, "EnsemblConnector", factory)
    enrich_variants(postgres_engine, rsid="rs4988235")
    enrich_variants(postgres_engine, rsid="rs4988235", assembly="GRCh37")
    with Session(postgres_engine) as session, session.begin():
        variant = session.scalar(select(Variant))
        gene = Gene(symbol="LCT", ensembl_gene_id="ENSG00000115850")
        session.add(gene)
        session.flush()
        session.add(
            VariantGene(
                variant_id=variant.id, gene_id=gene.id, relationship_type="persisted_test_evidence"
            )
        )
        row = session.scalar(select(Association).order_by(Association.id))
        study = session.get(Study, row.study_id)
        return {
            "run_id": summary["run_id"],
            "association_id": str(row.id),
            "ontology_id": session.execute(
                text("SELECT ontology_id FROM trait WHERE id=:id"), {"id": row.trait_id}
            ).scalar(),
            "study_id": str(study.id),
        }


def soap_call(client, operation, **arguments):
    root = etree.Element(f"{{{XML_NS}}}Envelope", nsmap={"soap": XML_NS, "tns": NAMESPACE})
    body = etree.SubElement(root, f"{{{XML_NS}}}Body")
    method = etree.SubElement(body, f"{{{NAMESPACE}}}{operation}")
    order = (
        "scope",
        "identifier",
        "rsid",
        "ontology_id",
        "symbol",
        "association_id",
        "raw_id",
        "run_id",
        "trait_name",
        "study_id",
        "limit",
        "offset",
        "assembly",
        "disposition",
    )
    for name in sorted(arguments, key=order.index):
        value = arguments[name]
        if value is not None:
            etree.SubElement(method, f"{{{NAMESPACE}}}{name}").text = str(value)
    response = client.post(
        "/",
        content=etree.tostring(root),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": operation},
    )
    document = etree.fromstring(response.content)
    faults = document.xpath("//*[local-name()='Fault']")
    return response, document, faults


def decode_xml(element, annotation):
    if get_origin(annotation) is UnionType:
        annotation = next(a for a in get_args(annotation) if a is not type(None))
    if element is None or element.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
        return [] if get_origin(annotation) is list else None
    if get_origin(annotation) is list:
        return [decode_xml(child, get_args(annotation)[0]) for child in element]
    if hasattr(annotation, "model_fields"):
        children = {etree.QName(child).localname: child for child in element}
        return annotation(
            **{
                name: decode_xml(children.get(name), field.annotation)
                for name, field in annotation.model_fields.items()
            }
        )
    if annotation is bool:
        return element.text in ("true", "1")
    if annotation is int:
        return int(element.text)
    if annotation is datetime:
        return datetime.fromisoformat(element.text)
    return element.text or ""


def soap_result(client, operation, contract, **kwargs):
    response, document, faults = soap_call(client, operation, **kwargs)
    assert response.status_code == 200, response.text
    assert not faults
    result = document.xpath(f"//*[local-name()='{operation}Result']")[0]
    return decode_xml(result, contract).model_dump(mode="json")


def graphql(client, query, variables=None):
    response = client.post("/graphql", json={"query": query, "variables": variables or {}})
    assert response.status_code == 200, response.text
    return response.json()


def snake_keys(value):
    import re

    if isinstance(value, list):
        return [snake_keys(v) for v in value]
    if isinstance(value, dict):
        return {re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower(): snake_keys(v) for k, v in value.items()}
    return value


VARIANT_FIELDS = "id rsid assembly chromosome position referenceAllele alternateAlleles variantClass mostSevereConsequence projectionMappingId enrichmentStatus"
TRAIT_FIELDS = "id ontologyId name description category"
STUDY_FIELDS = "id accession pubmedId title author publicationDate sampleDescription"
ASSOCIATION_FIELDS = (
    "id variantId traitId studyId variantRsid pValue pValueStatus pValueText effectAllele otherAllele "
    "effectSize effectSizeType effectSizeText effectSizeUnit effectDirection confidenceInterval sampleSize qualityStatus "
    f"trait {{ {TRAIT_FIELDS} }} study {{ {STUDY_FIELDS} }}"
)
PAGE_FIELDS = "limit offset hasMore nextOffset"


def test_variant_and_association_science_identical_across_protocols(api_clients, catalog_data):
    rest, soap = api_clients
    expected = rest.get("/v1/variants/rs4988235").json()
    assert (expected["assembly"], expected["chromosome"], expected["position"]) == (
        "GRCh38",
        "2",
        135851076,
    )
    result = graphql(rest, f'{{variant(rsid:"rs4988235"){{{VARIANT_FIELDS}}}}}')
    assert "errors" not in result, result
    assert snake_keys(result["data"]["variant"]) == expected
    assert soap_result(soap, "GetVariant", c.VariantView, rsid="rs4988235") == expected
    association_id = catalog_data["association_id"]
    association = rest.get(f"/v1/associations/{association_id}").json()
    result = graphql(
        rest, f'{{association(associationId:"{association_id}"){{{ASSOCIATION_FIELDS}}}}}'
    )
    assert "errors" not in result, result
    assert snake_keys(result["data"]["association"]) == association
    assert (
        soap_result(soap, "GetAssociation", c.AssociationView, association_id=association_id)
        == association
    )


def test_full_association_pagination_and_unreconciled_values(api_clients, catalog_data):
    rest, soap = api_clients
    all_items = []
    for offset in (0, 17, 34, 51):
        expected = rest.get(
            "/v1/variants/rs4988235/associations", params={"limit": 17, "offset": offset}
        ).json()
        assert len(expected["items"]) <= 17
        query = f'{{variantAssociations(rsid:"rs4988235",limit:17,offset:{offset}){{{PAGE_FIELDS} items{{{ASSOCIATION_FIELDS}}}}}}}'
        result = graphql(rest, query)
        assert "errors" not in result, result
        assert snake_keys(result["data"]["variantAssociations"]) == expected
        assert (
            soap_result(
                soap,
                "GetVariantAssociations",
                c.AssociationPage,
                rsid="rs4988235",
                limit=17,
                offset=offset,
            )
            == expected
        )
        all_items.extend(expected["items"])
    assert len(all_items) == len({item["id"] for item in all_items}) == 64
    disputed = [item for item in all_items if item["p_value_status"] == "unreconciled"]
    assert len(disputed) == 9
    assert all(
        item["p_value"] is None and item["quality_status"] == "findings_present"
        for item in disputed
    )
    assert any(item["p_value_text"] == "3e-1451" for item in disputed)
    assert any(item["quality_status"] == "no_findings" for item in all_items)


def test_trait_study_gene_relationships_and_pagination(api_clients, catalog_data):
    rest, soap = api_clients
    ontology_id = catalog_data["ontology_id"]
    trait = rest.get(f"/v1/traits/{ontology_id}").json()
    gql = graphql(rest, f'{{trait(ontologyId:"{ontology_id}"){{{TRAIT_FIELDS}}}}}')
    assert snake_keys(gql["data"]["trait"]) == trait
    assert soap_result(soap, "GetTrait", c.TraitView, ontology_id=ontology_id) == trait
    study_id = catalog_data["study_id"]
    study = rest.get(f"/v1/studies/{study_id}").json()
    gql = graphql(rest, f'{{study(studyId:"{study_id}"){{{STUDY_FIELDS}}}}}')
    assert snake_keys(gql["data"]["study"]) == study
    assert soap_result(soap, "GetStudy", c.StudyView, study_id=study_id) == study
    for offset in (0, 1):
        expected = rest.get(
            f"/v1/traits/{ontology_id}/associations", params={"limit": 1, "offset": offset}
        ).json()
        gql = graphql(
            rest,
            f'{{associationsForTrait(ontologyId:"{ontology_id}",limit:1,offset:{offset}){{{PAGE_FIELDS} items{{{ASSOCIATION_FIELDS}}}}}}}',
        )
        assert snake_keys(gql["data"]["associationsForTrait"]) == expected
        assert (
            soap_result(
                soap,
                "GetTraitAssociations",
                c.AssociationPage,
                ontology_id=ontology_id,
                limit=1,
                offset=offset,
            )
            == expected
        )
    expected = rest.get("/v1/genes/LCT/associations", params={"limit": 2}).json()
    gql = graphql(
        rest,
        f'{{geneAssociations(symbol:"LCT",limit:2){{{PAGE_FIELDS} items{{{ASSOCIATION_FIELDS}}}}}}}',
    )
    assert snake_keys(gql["data"]["geneAssociations"]) == expected
    assert (
        soap_result(soap, "GetGeneAssociations", c.AssociationPage, symbol="LCT", limit=2, offset=0)
        == expected
    )


MAPPING_FIELDS = "id variantId enrichmentId rawObjectId sourceCode requestedAssembly ordinal assembly coordinateSystem chromosome position endPosition strand valid alleleString ancestralAllele sha256"
ENRICHMENT_FIELDS = "id ingestionRunId variantId enrichmentId rawObjectId sourceCode requestedAssembly lookupIdentifier retrievedAt status sha256"
RAW_FIELDS = "id ingestionRunId sourceCode sourceRecordType sourceRecordId createdAt sha256 disposition associationId pValueCandidate pValueText pValueMantissa pValueExponent reportedPValueText"
QUALITY_FIELDS = "id ingestionRunId rawObjectId processingObservationId enrichmentObservationId entityType entityKey ruleCode severity message"


def test_historical_mappings_enrichment_and_quality(api_clients, catalog_data):
    rest, soap = api_clients
    for assembly in ("GRCh37", "GRCh38"):
        expected = rest.get("/v1/variants/rs4988235/mappings", params={"assembly": assembly}).json()
        assert len(expected["items"]) == 1 and expected["items"][0]["assembly"] == assembly
        gql = graphql(
            rest,
            f'{{variantMappings(rsid:"rs4988235",assembly:"{assembly}"){{{PAGE_FIELDS} items{{{MAPPING_FIELDS}}}}}}}',
        )
        assert snake_keys(gql["data"]["variantMappings"]) == expected
        assert (
            soap_result(
                soap,
                "GetVariantMappings",
                c.MappingPage,
                rsid="rs4988235",
                assembly=assembly,
                limit=50,
                offset=0,
            )
            == expected
        )
    expected = rest.get("/v1/variants/rs4988235/enrichments").json()
    gql = graphql(
        rest,
        f'{{variantEnrichments(rsid:"rs4988235"){{{PAGE_FIELDS} items{{{ENRICHMENT_FIELDS}}}}}}}',
    )
    assert snake_keys(gql["data"]["variantEnrichments"]) == expected
    assert (
        soap_result(
            soap, "GetVariantEnrichments", c.EnrichmentPage, rsid="rs4988235", limit=50, offset=0
        )
        == expected
    )
    conflict = next(item for item in expected["items"] if item["status"] == "conflict")
    expected = rest.get(f"/v1/evidence/enrichment/{conflict['id']}/quality").json()
    assert any(item["rule_code"] == "ENSEMBL_COORDINATE_CONFLICT" for item in expected["items"])
    gql = graphql(
        rest,
        f'{{quality(scope:"enrichment",identifier:"{conflict["id"]}"){{{PAGE_FIELDS} items{{{QUALITY_FIELDS}}}}}}}',
    )
    assert snake_keys(gql["data"]["quality"]) == expected
    assert (
        soap_result(
            soap,
            "GetQuality",
            c.QualityPage,
            scope="enrichment",
            identifier=conflict["id"],
            limit=50,
            offset=0,
        )
        == expected
    )


def test_quarantine_raw_references_and_quality_not_canonical(api_clients, catalog_data):
    rest, soap = api_clients
    run_id = catalog_data["run_id"]
    expected = rest.get(
        f"/v1/evidence/runs/{run_id}/raw", params={"disposition": "quarantined"}
    ).json()
    assert len(expected["items"]) == 4 and all(
        item["association_id"] is None for item in expected["items"]
    )
    gql = graphql(
        rest,
        f'{{runRaw(runId:"{run_id}",disposition:"quarantined"){{{PAGE_FIELDS} items{{{RAW_FIELDS}}}}}}}',
    )
    assert snake_keys(gql["data"]["runRaw"]) == expected
    assert (
        soap_result(
            soap,
            "GetRunRaw",
            c.RawPage,
            run_id=run_id,
            disposition="quarantined",
            limit=50,
            offset=0,
        )
        == expected
    )
    raw_id = expected["items"][0]["id"]
    expected = rest.get(f"/v1/evidence/raw/{raw_id}").json()
    assert (
        "storage_uri" not in expected
        and "payload_text" not in expected
        and "metadata_json" not in expected
    )
    gql = graphql(rest, f'{{rawEvidence(rawId:"{raw_id}"){{{RAW_FIELDS}}}}}')
    assert snake_keys(gql["data"]["rawEvidence"]) == expected
    assert soap_result(soap, "GetRawEvidence", c.RawView, raw_id=raw_id) == expected
    quality = rest.get(f"/v1/evidence/raw/{raw_id}/quality").json()
    assert any(item["rule_code"] == "ASSOC_MULTIPLE_TRAITS" for item in quality["items"])
    gql = graphql(
        rest,
        f'{{quality(scope:"raw",identifier:"{raw_id}"){{{PAGE_FIELDS} items{{{QUALITY_FIELDS}}}}}}}',
    )
    assert snake_keys(gql["data"]["quality"]) == quality
    assert (
        soap_result(
            soap, "GetQuality", c.QualityPage, scope="raw", identifier=raw_id, limit=50, offset=0
        )
        == quality
    )


def test_association_provenance_and_exact_candidate(api_clients, catalog_data):
    rest, soap = api_clients
    associations = rest.get("/v1/variants/rs4988235/associations", params={"limit": 100}).json()[
        "items"
    ]
    tiny = next(item for item in associations if item["p_value_text"] == "3e-1451")
    expected = rest.get(f"/v1/associations/{tiny['id']}/sources").json()
    assert len(expected["items"]) == 1
    fields = "associationId sourceCode sourceRecordId ingestionRunId rawObjectId sha256"
    gql = graphql(
        rest,
        f'{{associationSources(associationId:"{tiny["id"]}"){{{PAGE_FIELDS} items{{{fields}}}}}}}',
    )
    assert snake_keys(gql["data"]["associationSources"]) == expected
    assert (
        soap_result(
            soap,
            "GetAssociationSources",
            c.SourcePage,
            association_id=tiny["id"],
            limit=50,
            offset=0,
        )
        == expected
    )
    raw_id = expected["items"][0]["raw_object_id"]
    raw = rest.get(f"/v1/evidence/raw/{raw_id}").json()
    from decimal import Decimal

    assert Decimal(raw["p_value_candidate"]) == Decimal("3e-1451") > 0
    assert raw["p_value_mantissa"] == "3" and raw["p_value_exponent"] == "-1451"
    assert Decimal(raw["reported_p_value_text"]) == 0


def test_corrupt_raw_evidence_fails_safely_across_protocols(
    api_clients, catalog_data, postgres_engine
):
    rest, soap = api_clients
    raw_id = rest.get(f"/v1/evidence/runs/{catalog_data['run_id']}/raw").json()["items"][0]["id"]
    with Session(postgres_engine) as session, session.begin():
        session.execute(
            text("UPDATE raw_object SET payload_text='private corrupted evidence' WHERE id=:id"),
            {"id": raw_id},
        )
    response = rest.get(f"/v1/evidence/raw/{raw_id}")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    gql = graphql(rest, f'{{rawEvidence(rawId:"{raw_id}"){{id}}}}')
    assert gql["errors"][0]["extensions"]["code"] == "INTERNAL_ERROR"
    response, _document, faults = soap_call(soap, "GetRawEvidence", raw_id=raw_id)
    assert response.status_code == 500 and faults
    assert "INTERNAL_ERROR" in response.text and "private" not in response.text


@pytest.mark.parametrize(
    "rsid, expected",
    [("rs99999999", "NOT_FOUND"), ("bad'id", "INVALID_INPUT"), ("rs0" * 100, "INVALID_INPUT")],
)
def test_missing_invalid_identifiers_across_protocols(api_clients, rsid, expected):
    rest, soap = api_clients
    response = rest.get(f"/v1/variants/{rsid}")
    assert response.status_code == (404 if expected == "NOT_FOUND" else 422)
    assert response.json()["error"]["code"] == expected
    gql = graphql(rest, "query($id:String!){variant(rsid:$id){id}}", {"id": rsid})
    assert gql["errors"][0]["extensions"]["code"] == expected
    response, _document, faults = soap_call(soap, "GetVariant", rsid=rsid)
    assert response.status_code == 500
    assert expected in etree.tostring(faults[0]).decode()


@pytest.mark.parametrize("limit, offset", [(0, 0), (101, 0), (1, -1), (1, 10001)])
def test_pagination_limits_across_protocols(api_clients, limit, offset):
    rest, soap = api_clients
    response = rest.get(
        "/v1/variants/rs4988235/associations", params={"limit": limit, "offset": offset}
    )
    assert response.status_code == 422
    gql = graphql(
        rest, f'{{variantAssociations(rsid:"rs4988235",limit:{limit},offset:{offset}){{limit}}}}'
    )
    assert gql["errors"][0]["extensions"]["code"] == "INVALID_INPUT"
    response, _document, faults = soap_call(
        soap, "GetVariantAssociations", rsid="rs4988235", limit=limit, offset=offset
    )
    assert response.status_code == 500
    assert "INVALID_INPUT" in etree.tostring(faults[0]).decode()


def test_wsdl_xml_validation_and_query_limits(api_clients):
    rest, soap = api_clients
    response = soap.get("/?wsdl")
    assert response.status_code == 200
    wsdl = etree.fromstring(response.content)
    assert len(wsdl.xpath("//*[local-name()='operation' and @name='GetVariant']")) >= 2
    assert wsdl.xpath("//*[local-name()='fault']")
    for body in (
        b"<bad",
        b'<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///C:/private">]><x>&leak;</x>',
    ):
        response = soap.post("/", content=body, headers={"Content-Type": "text/xml"})
        assert "INVALID_INPUT" in response.text and "private" not in response.text
    response = soap.post("/", content=b"x" * 65537, headers={"Content-Type": "text/xml"})
    assert response.status_code == 413
    response = rest.post("/graphql", content=b"x" * 65537)
    assert response.status_code == 413
    aliases = " ".join(f'a{i}:variant(rsid:"rs1"){{id}}' for i in range(21))
    result = graphql(rest, "{" + aliases + "}")
    assert result["errors"]
    result = graphql(rest, "{" + 'variant(rsid:"rs1"){id} ' * 300 + "}")
    assert result["errors"]
    assert rest.delete("/v1/variants/rs1").status_code == 405


def test_database_failure_sanitized_in_all_protocols(api_clients, postgres_engine):
    rest, soap = api_clients

    def fail(connection, cursor, statement, parameters, context, executemany):
        if "SELECT" in statement:
            raise OperationalError("password=secret", {}, Exception("private server detail"))

    event.listen(postgres_engine, "before_cursor_execute", fail)
    try:
        response = rest.get("/v1/variants/rs1")
        assert response.status_code == 503
        assert "secret" not in response.text and "private" not in response.text
        gql = graphql(rest, '{variant(rsid:"rs1"){id}}')
        assert gql["errors"][0]["extensions"]["code"] == "DATABASE_UNAVAILABLE"
        assert "secret" not in json.dumps(gql)
        response, _document, _faults = soap_call(soap, "GetVariant", rsid="rs1")
        assert response.status_code == 500 and "DATABASE_UNAVAILABLE" in response.text
        assert "secret" not in response.text and "private" not in response.text
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail)


def test_read_only_transactions_and_no_fabricated_missing_data(api_clients, postgres_engine):
    rest, soap = api_clients
    with Session(postgres_engine) as session, session.begin():
        session.add(Variant(rsid="rs1", alternate_alleles=[], metadata_json={}))
        session.add(Gene(symbol="EMPTY"))
    expected = rest.get("/v1/variants/rs1").json()
    assert expected["assembly"] is expected["position"] is expected["enrichment_status"] is None
    assert expected["projection_mapping_id"] is None
    assert rest.get("/v1/variants/rs1/mappings").json()["items"] == []
    assert soap_result(soap, "GetVariant", c.VariantView, rsid="rs1") == expected
    assert rest.get("/v1/genes/EMPTY/associations").json()["items"] == []
    with read_session(sessionmaker(postgres_engine)) as session:
        assert session.scalar(text("SHOW transaction_read_only")) == "on"
        with pytest.raises(InternalError, match="read-only"):
            session.execute(
                text("INSERT INTO gene(id,symbol) VALUES (:id,'NO_WRITE')"), {"id": uuid4()}
            )


def test_run_processing_and_quality_parity(api_clients, catalog_data):
    rest, soap = api_clients
    run_id = catalog_data["run_id"]
    fields = "id sourceCode pipelineName originatingRunId startedAt finishedAt status"
    expected = rest.get(f"/v1/evidence/runs/{run_id}").json()
    gql = graphql(rest, f'{{ingestionRun(runId:"{run_id}"){{{fields}}}}}')
    assert snake_keys(gql["data"]["ingestionRun"]) == expected
    assert soap_result(soap, "GetIngestionRun", c.RunView, run_id=run_id) == expected
    fields = "id ingestionRunId rawObjectId associationId disposition createdAt"
    expected = rest.get(f"/v1/evidence/runs/{run_id}/processing", params={"limit": 100}).json()
    assert len(expected["items"]) == 68
    assert sum(item["disposition"] == "quarantined" for item in expected["items"]) == 4
    gql = graphql(
        rest, f'{{runProcessing(runId:"{run_id}",limit:100){{{PAGE_FIELDS} items{{{fields}}}}}}}'
    )
    assert snake_keys(gql["data"]["runProcessing"]) == expected
    assert (
        soap_result(soap, "GetRunProcessing", c.ProcessingPage, run_id=run_id, limit=100, offset=0)
        == expected
    )
    expected = rest.get(f"/v1/evidence/run/{run_id}/quality").json()
    assert len(expected["items"]) > 0
    gql = graphql(
        rest,
        f'{{quality(scope:"run",identifier:"{run_id}"){{{PAGE_FIELDS} items{{{QUALITY_FIELDS}}}}}}}',
    )
    assert snake_keys(gql["data"]["quality"]) == expected
    assert (
        soap_result(
            soap, "GetQuality", c.QualityPage, scope="run", identifier=run_id, limit=50, offset=0
        )
        == expected
    )


def test_legacy_name_lookup_and_soap_ids_preserved(api_clients, catalog_data):
    rest, soap = api_clients
    trait = rest.get(f"/v1/traits/{catalog_data['ontology_id']}").json()
    response = rest.get(f"/v1/traits/{trait['name']}/associations")
    assert response.status_code == 200
    expected = response.json()
    assert isinstance(expected, list)
    gql = graphql(
        rest,
        "query($name:String!){traitAssociations(traitName:$name){id}}",
        {"name": trait["name"]},
    )
    assert [item["id"] for item in gql["data"]["traitAssociations"]] == [
        item["id"] for item in expected
    ]
    response, document, faults = soap_call(
        soap, "GetTraitAssociationIds", trait_name=trait["name"], limit=50
    )
    assert response.status_code == 200 and not faults
    assert document.xpath("//*[local-name()='string']/text()") == [item["id"] for item in expected]


def test_graphql_row_budget_depth_and_integer_validation(api_clients, postgres_engine):
    rest, _ = api_clients
    with Session(postgres_engine) as session, session.begin():
        session.add(Variant(rsid="rs1", alternate_alleles=[], metadata_json={}))
    aliases = " ".join(
        f'a{i}:variantAssociations(rsid:"rs1",limit:100){{{PAGE_FIELDS}}}' for i in range(11)
    )
    result = graphql(rest, "{" + aliases + "}")
    assert any(
        error.get("extensions", {}).get("code") == "QUERY_LIMIT" for error in result["errors"]
    )
    result = graphql(rest, '{variantAssociations(rsid:"rs1",limit:1.5){limit}}')
    assert result["errors"]
    result = graphql(rest, '{variantAssociations(rsid:"rs1",offset:9223372036854775808){limit}}')
    assert result["errors"]
    result = graphql(
        rest,
        '{ variant(rsid:"rs1") { a { b { c { d { e { f { g { h { i { id } } } } } } } } } } }',
    )
    assert any("depth" in error["message"].lower() for error in result["errors"])
    result = graphql(
        rest,
        "{ __schema { types { fields { type { fields { type { fields { type { fields { name } } } } } } } } } }",
    )
    assert any(
        "Maximum introspection depth exceeded" in error["message"] for error in result["errors"]
    )
    with pytest.raises(WebSocketDisconnect), rest.websocket_connect("/graphql"):
        pass


def test_missing_study_ambiguous_mappings_and_large_position(
    api_clients, postgres_engine, monkeypatch
):
    from genomic_platform.domain.models import Trait

    rest, soap = api_clients
    with Session(postgres_engine) as session, session.begin():
        variant = Variant(
            rsid="rs1",
            assembly="GRCh38",
            chromosome="2",
            position=3_000_000_000,
            alternate_alleles=[],
            metadata_json={},
        )
        trait = Trait(ontology_id="EFO_0000001", name="Only reported evidence")
        session.add_all([variant, trait])
        session.flush()
        session.add(
            Association(
                variant_id=variant.id, trait_id=trait.id, p_value_status="missing", metadata_json={}
            )
        )
        session.add(Variant(rsid="rs2", alternate_alleles=[], metadata_json={}))
    response = rest.get("/v1/variants/rs1")
    gql = graphql(rest, f'{{variant(rsid:"rs1"){{{VARIANT_FIELDS}}}}}')
    assert snake_keys(gql["data"]["variant"]) == response.json()
    assert soap_result(soap, "GetVariant", c.VariantView, rsid="rs1") == response.json()
    associations = rest.get("/v1/variants/rs1/associations").json()["items"]
    assert associations[0]["study"] is associations[0]["p_value"] is None
    assert associations[0]["p_value_status"] == "missing"
    payload = json.loads((ROOT / "tests" / "fixtures" / "ensembl-rs4988235.json").read_text())
    payload["name"] = "rs2"
    payload["mappings"].append(
        {**payload["mappings"][0], "chromosome": "X", "seq_region_name": "X"}
    )

    def factory(**kwargs):
        connector = EnsemblConnector(**kwargs)
        connector.http.client = httpx.AsyncClient(
            base_url=connector.base_url,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        )
        return connector

    monkeypatch.setattr(enrichment, "EnsemblConnector", factory)
    enrich_variants(postgres_engine, rsid="rs2")
    variant = rest.get("/v1/variants/rs2").json()
    assert variant["enrichment_status"] == "ambiguous" and variant["position"] is None
    assert len(rest.get("/v1/variants/rs2/mappings").json()["items"]) == 2
    gql = graphql(rest, f'{{variant(rsid:"rs2"){{{VARIANT_FIELDS}}}}}')
    assert snake_keys(gql["data"]["variant"]) == variant
    assert soap_result(soap, "GetVariant", c.VariantView, rsid="rs2") == variant


@pytest.mark.parametrize(
    "path",
    [
        "/v1/traits/EFO_999999999",
        "/v1/studies/00000000-0000-0000-0000-000000000000",
        "/v1/genes/MISSING",
        "/v1/associations/00000000-0000-0000-0000-000000000000",
        "/v1/evidence/raw/00000000-0000-0000-0000-000000000000",
        "/v1/evidence/runs/00000000-0000-0000-0000-000000000000",
    ],
)
def test_missing_records_return_explicit_not_found(api_clients, path):
    rest, _ = api_clients
    response = rest.get(path)
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


def test_openapi_is_typed_and_read_only(api_clients):
    rest, _ = api_clients
    specification = rest.get("/openapi.json").json()
    from genomic_platform.api.graphql_schema import schema

    assert schema._schema.mutation_type is None
    assert schema._schema.subscription_type is None
    for path, methods in specification["paths"].items():
        if path == "/graphql":
            continue
        assert set(methods) <= {"get"}
        if path != "/v1/health":
            assert methods["get"]["responses"]["200"]["content"]["application/json"]["schema"]
            assert "503" in methods["get"]["responses"]


def test_liveness_and_database_readiness(api_clients):
    rest, _ = api_clients
    assert rest.get("/v1/health").json() == {"status": "ok"}
    response = rest.get("/v1/ready")
    assert response.status_code == 200 and response.json() == {"status": "ready"}


def test_readiness_failure_is_sanitized_and_liveness_independent(api_clients, postgres_engine):
    rest, _ = api_clients

    def fail(connection, cursor, statement, parameters, context, executemany):
        raise OperationalError("private SQL", {}, Exception("private database credential"))

    event.listen(postgres_engine, "before_cursor_execute", fail)
    try:
        response = rest.get("/v1/ready")
        assert response.status_code == 503
        assert response.json() == {
            "error": {"code": "DATABASE_UNAVAILABLE", "message": "Database query unavailable."}
        }
        assert rest.get("/v1/health").status_code == 200
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail)


def test_readiness_rejects_unmigrated_database(api_clients, postgres_engine):
    rest, _ = api_clients
    with Session(postgres_engine) as session, session.begin():
        session.execute(text("ALTER TABLE association RENAME TO unavailable_association"))
    response = rest.get("/v1/ready")
    assert response.status_code == 503
    assert "association" not in response.text
    assert rest.get("/v1/health").status_code == 200
