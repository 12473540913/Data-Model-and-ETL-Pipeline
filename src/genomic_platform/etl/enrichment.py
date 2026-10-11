import asyncio
import base64
import binascii
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import Engine, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from genomic_platform.connectors.ensembl import EnsemblConnector
from genomic_platform.domain.models import (
    EnrichmentObservation,
    IngestionRun,
    QualityIssue,
    RawObject,
    SourceSystem,
    Variant,
    VariantEnrichment,
    VariantMapping,
)
from genomic_platform.etl.ensembl_normalization import (
    EnsemblResult,
    forward_snp_alleles,
    normalize_ensembl,
)
from genomic_platform.etl.normalize import normalize_rsid
from genomic_platform.etl.source_json import decode_source_json

logger = logging.getLogger(__name__)


class EnrichmentError(RuntimeError):
    def __init__(self, message: str, summary: dict[str, Any] | None = None):
        super().__init__(message)
        self.summary = summary


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def _verify_raw(raw: RawObject | None, digest: str, byte_digest: str) -> None:
    if raw is None or raw.payload_text is None:
        raise EnrichmentError("Stored Ensembl evidence is missing.")
    encoded = raw.metadata_json.get("response_bytes_base64")
    try:
        original_bytes = (
            base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else None
        )
    except (ValueError, binascii.Error):
        raise EnrichmentError("Stored Ensembl evidence failed checksum verification.") from None
    if (
        original_bytes is None
        or hashlib.sha256(original_bytes).hexdigest() != byte_digest
        or raw.metadata_json.get("response_bytes_sha256") != byte_digest
        or hashlib.sha256(raw.payload_text.encode()).hexdigest() != raw.sha256
        or raw.sha256 != digest
    ):
        raise EnrichmentError("Stored Ensembl evidence failed checksum verification.")


def _project(
    variant: Variant,
    result: EnsemblResult,
    mappings: list[VariantMapping],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    findings = dict(result.findings)
    applied: dict[str, Any] = {}
    if len(result.candidates) > 1:
        return "ambiguous", findings, applied
    if not result.candidates:
        return "partial", findings, applied
    candidate = result.candidates[0]
    # An invalid target chromosome mapping could conceal another placement.
    if any(
        not m.valid
        and (
            m.assembly is None
            or (m.assembly == candidate.assembly and m.coordinate_system == "chromosome")
        )
        for m in result.mappings
    ):
        findings["ENSEMBL_AMBIGUOUS_MAPPING"] = (
            "Incomplete target mappings prevent a safe projection."
        )
        return "ambiguous", findings, applied
    coordinates = {
        "assembly": candidate.assembly,
        "chromosome": candidate.chromosome,
        "position": candidate.position,
    }
    if any(
        getattr(variant, key) is not None and getattr(variant, key) != value
        for key, value in coordinates.items()
    ):
        findings["ENSEMBL_COORDINATE_CONFLICT"] = (
            "Existing assembly-qualified coordinates retained; incoming mapping differs."
        )
        return "conflict", findings, applied
    for key, value in coordinates.items():
        if getattr(variant, key) is None:
            setattr(variant, key, value)
            applied[key] = value
    if variant.projection_mapping_id is None:
        variant.projection_mapping_id = mappings[candidate.ordinal].id
        applied["projection_mapping_id"] = str(variant.projection_mapping_id)
    annotations = {
        "variant_class": result.annotations.get("var_class"),
        "most_severe_consequence": result.annotations.get("most_severe_consequence"),
    }
    alleles = forward_snp_alleles(candidate)
    if alleles is not None:
        if variant.reference_allele not in (None, alleles[0]) or variant.alternate_alleles not in (
            [],
            alleles[1],
        ):
            findings["ENSEMBL_ANNOTATION_CONFLICT"] = (
                "Existing canonical alleles retained; new evidence differs."
            )
        else:
            annotations.update(reference_allele=alleles[0], alternate_alleles=alleles[1])
    else:
        findings["ENSEMBL_UNPROJECTED_ALLELES"] = (
            "Alleles preserved in mapping evidence; no safe forward-strand SNP projection."
        )
    for key, value in annotations.items():
        if value is None:
            continue
        current = getattr(variant, key)
        if current is None or current == []:
            setattr(variant, key, value)
            applied[key] = value
        elif current != value:
            findings["ENSEMBL_ANNOTATION_CONFLICT"] = (
                "Existing canonical alleles or annotations retained; new evidence differs."
            )
    return ("partial" if findings else "enriched"), findings, applied


def _persist_lookup(
    engine: Engine,
    run_id: UUID,
    variant_id: UUID,
    assembly: str,
    rsid: str,
    response: httpx.Response | None,
    retrieved_at: datetime,
    error_code: str | None,
) -> tuple[str, bool, bool]:
    with Session(engine) as session, session.begin():
        variant = session.scalar(select(Variant).where(Variant.id == variant_id).with_for_update())
        if variant is None or variant.rsid != rsid:
            raise EnrichmentError("Canonical variant changed or disappeared during lookup.")
        evidence = None
        reused = changed = False
        findings: dict[str, str] = {}
        applied: dict[str, Any] = {}
        status = "failed"
        result = None
        if response is not None:
            try:
                body = response.content.decode("utf-8")
                valid_encoding = True
            except UnicodeError:
                body = response.text
                valid_encoding = False
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            byte_digest = hashlib.sha256(response.content).hexdigest()
            # Include response status: identical bodies with differing HTTP meaning are distinct evidence.
            evidence_key = hashlib.sha256(
                f"{response.status_code}:{byte_digest}".encode()
            ).hexdigest()
            evidence = session.scalar(
                select(VariantEnrichment).where(
                    VariantEnrichment.variant_id == variant_id,
                    VariantEnrichment.requested_assembly == assembly,
                    VariantEnrichment.sha256 == evidence_key,
                )
            )
            reused = evidence is not None
            try:
                if not valid_encoding:
                    raise ValueError("Ensembl JSON must be UTF-8.")
                payload = decode_source_json(body)
                if (
                    response.status_code == 404
                    or response.status_code == 400
                    and isinstance(payload, dict)
                    and isinstance(payload.get("error"), str)
                    and (
                        "not found" in payload["error"].lower()
                        or "not a valid variation" in payload["error"].lower()
                    )
                ):
                    status = "not_found"
                elif response.is_success:
                    result = normalize_ensembl(payload, rsid, assembly)
                else:
                    error_code = f"HTTP_{response.status_code}"
            except (ValueError, UnicodeError):
                error_code = "INVALID_RESPONSE"
            if result is not None or status == "not_found":
                previous = session.scalar(
                    select(EnrichmentObservation)
                    .where(
                        EnrichmentObservation.variant_id == variant_id,
                        EnrichmentObservation.requested_assembly == assembly,
                        EnrichmentObservation.status != "failed",
                    )
                    .order_by(EnrichmentObservation.retrieved_at.desc())
                    .limit(1)
                )
                changed = previous is not None and (
                    evidence is None or previous.enrichment_id != evidence.id
                )
            normalized = {
                "parser_version": 1,
                "annotations": result.annotations if result else {},
                "findings": result.findings if result else {},
                "http_status": response.status_code,
            }
            if evidence is None:
                raw = RawObject(
                    id=uuid4(),
                    ingestion_run_id=run_id,
                    source_record_type="ensembl_variation",
                    source_record_id=rsid,
                    payload_text=body,
                    sha256=digest,
                    storage_uri="pending",
                    disposition="captured",
                    metadata_json={
                        "source": "ENSEMBL",
                        "lookup_identifier": rsid,
                        "requested_assembly": assembly,
                        "retrieved_at": retrieved_at.isoformat(),
                        "http_status": response.status_code,
                        "response_bytes_sha256": hashlib.sha256(response.content).hexdigest(),
                        "response_bytes_base64": base64.b64encode(response.content).decode("ascii"),
                        "endpoint": response.request.url.host,
                        "path": f"/variation/homo_sapiens/{rsid}",
                    },
                    normalized_json=_json_safe(normalized),
                )
                raw.storage_uri = f"postgresql:raw_object:{raw.id}"
                session.add(raw)
                session.flush()
                evidence = VariantEnrichment(
                    id=uuid4(),
                    variant_id=variant_id,
                    raw_object_id=raw.id,
                    requested_assembly=assembly,
                    sha256=evidence_key,
                    normalized_json=_json_safe(normalized),
                )
                session.add(evidence)
                session.flush()
                mappings = []
                if result is not None:
                    for m in result.mappings:
                        mapping = VariantMapping(
                            id=uuid4(),
                            enrichment_id=evidence.id,
                            variant_id=variant_id,
                            ordinal=m.ordinal,
                            assembly=m.assembly,
                            coordinate_system=m.coordinate_system,
                            chromosome=m.chromosome,
                            position=m.position,
                            end_position=m.end_position,
                            strand=m.strand,
                            valid=m.valid,
                            payload_json=_json_safe(m.payload),
                        )
                        session.add(mapping)
                        mappings.append(mapping)
                session.flush()
            else:
                raw = session.get(RawObject, evidence.raw_object_id)
                _verify_raw(raw, digest, byte_digest)
                mappings = list(
                    session.scalars(
                        select(VariantMapping)
                        .where(VariantMapping.enrichment_id == evidence.id)
                        .order_by(VariantMapping.ordinal)
                    )
                )
                if result is not None and (
                    len(mappings) != len(result.mappings)
                    or any(
                        stored.ordinal != incoming.ordinal
                        or stored.payload_json != _json_safe(incoming.payload)
                        or any(
                            getattr(stored, key) != getattr(incoming, key)
                            for key in (
                                "assembly",
                                "coordinate_system",
                                "chromosome",
                                "position",
                                "end_position",
                                "strand",
                                "valid",
                            )
                        )
                        for stored, incoming in zip(mappings, result.mappings, strict=True)
                    )
                ):
                    raise EnrichmentError("Stored Ensembl mapping does not match its raw evidence.")
            if result is not None:
                status, findings, applied = _project(variant, result, mappings)
        if status == "failed":
            findings["ENSEMBL_LOOKUP_FAILED"] = (
                f"Ensembl lookup failed ({error_code or 'INVALID_RESPONSE'}); canonical variant unchanged."
            )
        elif status == "not_found":
            findings["ENSEMBL_NOT_FOUND"] = "Ensembl has no available result for this rsID."
        if changed:
            findings["ENSEMBL_EVIDENCE_CHANGED"] = (
                "Ensembl response differs from earlier evidence; historical response and projection retained."
            )
            if status == "enriched":
                status = "partial"
        observation = EnrichmentObservation(
            id=uuid4(),
            ingestion_run_id=run_id,
            variant_id=variant_id,
            enrichment_id=evidence.id if evidence else None,
            requested_assembly=assembly,
            lookup_identifier=rsid,
            retrieved_at=retrieved_at,
            status=status,
            metadata_json={
                "error_code": error_code,
                "reused_evidence": reused,
                "changed_evidence": bool(changed),
                "applied_fields": applied,
            },
        )
        session.add(observation)
        session.flush()
        for code, message in findings.items():
            session.add(
                QualityIssue(
                    ingestion_run_id=run_id,
                    enrichment_observation_id=observation.id,
                    entity_type="variant",
                    entity_key=rsid,
                    rule_code=code,
                    severity="error" if status == "failed" else "warning",
                    message=message,
                    details_json={"enrichment_id": str(evidence.id) if evidence else None},
                )
            )
        return status, reused, bool(changed)


async def _execute(
    engine: Engine,
    run_id: UUID,
    selection: list[tuple[UUID, str]],
    assembly: str,
    timeout: float,
    requests_per_second: float,
    summary: dict[str, Any],
) -> None:
    connector = EnsemblConnector(
        assembly=assembly, timeout=timeout, requests_per_second=requests_per_second
    )
    try:
        for variant_id, rsid in selection:
            response = None
            error_code = None
            try:
                response = await connector.lookup_response(rsid)
            except httpx.HTTPStatusError as error:
                response = error.response
                error_code = f"HTTP_{response.status_code}"
            except httpx.TransportError as error:
                error_code = type(error).__name__
            status, reused, changed = _persist_lookup(
                engine, run_id, variant_id, assembly, rsid, response, datetime.now(UTC), error_code
            )
            summary["processed"] += 1
            summary[status] += 1
            summary["reused_evidence"] += int(reused)
            summary["changed_evidence"] += int(changed)
            with Session(engine) as session, session.begin():
                run = session.get(IngestionRun, run_id)
                if run is None:
                    raise EnrichmentError("Enrichment run disappeared.")
                run.metrics_json = dict(summary)
    finally:
        await connector.aclose()


def enrich_variants(
    engine: Engine,
    *,
    rsid: str | None = None,
    limit: int = 100,
    assembly: str = "GRCh38",
    timeout: float = 30,
    requests_per_second: float = 5,
    refresh: bool = False,
) -> dict[str, Any]:
    if (
        assembly not in ("GRCh37", "GRCh38")
        or not 1 <= limit <= 10000
        or not 0 < timeout <= 300
        or not 0 < requests_per_second <= 15
    ):
        raise EnrichmentError("Invalid assembly, selection limit, timeout or request rate.")
    canonical = normalize_rsid(rsid) if rsid is not None else None
    if rsid is not None and (canonical is None or len(canonical) > 50):
        raise EnrichmentError("Enrichment requires a valid rsID.")
    if engine.dialect.name != "postgresql":
        raise EnrichmentError("Enrichment requires PostgreSQL.")
    with engine.connect() as connection:
        if connection.scalar(text("SELECT version_num FROM alembic_version")) != "0004":
            raise EnrichmentError("Apply database migrations with init-db before enrichment.")
    with Session(engine) as session, session.begin():
        statement = (
            select(Variant.id, Variant.rsid)
            .where(Variant.rsid.is_not(None))
            .order_by(Variant.rsid)
            .limit(limit)
        )
        if canonical is not None:
            statement = statement.where(Variant.rsid == canonical)
        elif not refresh:
            latest_status = (
                select(EnrichmentObservation.status)
                .where(
                    EnrichmentObservation.variant_id == Variant.id,
                    EnrichmentObservation.requested_assembly == assembly,
                )
                .order_by(EnrichmentObservation.retrieved_at.desc())
                .limit(1)
                .scalar_subquery()
            )
            statement = statement.where(latest_status.is_(None) | (latest_status == "failed"))
        selection = [(row[0], row[1]) for row in session.execute(statement)]
        if canonical is not None and not selection:
            raise EnrichmentError("Canonical variant is not present; ingest GWAS evidence first.")
        if any(normalize_rsid(value) != value or len(value) > 50 for _, value in selection):
            raise EnrichmentError("Selected canonical variant has an invalid rsID.")
        session.execute(
            insert(SourceSystem)
            .values(
                id=uuid4(),
                code="ENSEMBL",
                name="Ensembl",
                base_url="https://www.ensembl.org/",
                active=True,
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )
        source_id = session.scalar(select(SourceSystem.id).where(SourceSystem.code == "ENSEMBL"))
        if source_id is None:
            raise EnrichmentError("Ensembl source system could not be persisted.")
        run_id = uuid4()
        run = IngestionRun(
            id=run_id,
            source_system_id=source_id,
            pipeline_name="ensembl_enrichment_v1",
            metadata_json={
                "assembly": assembly,
                "selection": [str(v) for v, _ in selection],
                "timeout": timeout,
                "requests_per_second": requests_per_second,
            },
            metrics_json={},
            status="running",
        )
        session.add(run)
    summary: dict[str, Any] = {
        "run_id": str(run_id),
        "assembly": assembly,
        "selected": len(selection),
        "processed": 0,
        "enriched": 0,
        "partial": 0,
        "ambiguous": 0,
        "conflict": 0,
        "not_found": 0,
        "failed": 0,
        "reused_evidence": 0,
        "changed_evidence": 0,
        "status": "running",
    }
    failure = None
    try:
        asyncio.run(
            _execute(engine, run_id, selection, assembly, timeout, requests_per_second, summary)
        )
    except (SQLAlchemyError, EnrichmentError, OSError, ValueError, KeyboardInterrupt) as error:
        failure = type(error).__name__
        logger.error("Ensembl enrichment interrupted (%s).", failure)
    summary["pending"] = len(selection) - summary["processed"]
    summary["execution_failure"] = failure
    summary["status"] = (
        "failed"
        if failure or summary["failed"]
        else "completed_with_issues"
        if any(summary[k] for k in ("partial", "ambiguous", "conflict", "not_found"))
        else "completed"
    )
    with Session(engine) as session, session.begin():
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise EnrichmentError("Enrichment run disappeared.", summary)
        run.status = summary["status"]
        run.finished_at = datetime.now(UTC)
        run.metrics_json = dict(summary)
        if failure:
            run.metadata_json = {**run.metadata_json, "failure_code": failure}
    if summary["status"] == "failed":
        raise EnrichmentError(
            "Enrichment failed; committed variant evidence is retained. Retry failed lookups independently.",
            summary,
        )
    return summary
