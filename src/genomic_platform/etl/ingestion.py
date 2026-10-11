import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import Engine, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from genomic_platform.connectors.gwas_catalog import GWASCatalogConnector
from genomic_platform.domain.models import (
    IngestionBatch,
    IngestionRun,
    ProcessingObservation,
    QualityIssue,
    RawObject,
)
from genomic_platform.etl.loading import (
    _persist_findings,
    _source,
    numeric_supported,
    process_association,
)
from genomic_platform.etl.normalize import clean_text, gwas_record_to_association
from genomic_platform.etl.raw_store import encode_raw_json
from genomic_platform.etl.source_json import (
    SourcePayloadError,
    decode_source_json,
    reject_constant,
    unique_object,
)
from genomic_platform.quality.rules import QualityFinding

logger = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    def __init__(self, message: str, summary: dict[str, Any] | None = None):
        super().__init__(message)
        self.summary = summary


def validate_database(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise IngestionError("Ingestion requires PostgreSQL.")
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if version != "0004":
            raise IngestionError("Apply database migrations with init-db before ingestion.")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_file_records(path: Path) -> Iterator[dict[str, Any]]:
    """Stream a JSON array with exact numeric tokens; memory bounded by one record."""
    decoder = json.JSONDecoder(
        parse_float=Decimal, parse_constant=reject_constant, object_pairs_hook=unique_object
    )
    with path.open(encoding="utf-8-sig") as stream:
        buffer = ""
        eof = False

        def more() -> bool:
            nonlocal buffer, eof
            chunk = stream.read(65536)
            eof = not chunk
            buffer += chunk
            return not eof

        def strip() -> None:
            nonlocal buffer
            buffer = buffer.lstrip()
            while not buffer and not eof:
                more()
                buffer = buffer.lstrip()

        strip()
        if not buffer.startswith("["):
            raise SourcePayloadError(
                "Saved GWAS input must be a JSON array of association objects."
            )
        buffer = buffer[1:]
        strip()
        if buffer.startswith("]"):
            buffer = buffer[1:]
        else:
            while True:
                strip()
                while True:
                    try:
                        record, end = decoder.raw_decode(buffer)
                        break
                    except json.JSONDecodeError:
                        if not more():
                            raise SourcePayloadError(
                                "Saved GWAS input contains malformed or incomplete JSON."
                            ) from None
                if not isinstance(record, dict):
                    raise SourcePayloadError("Every saved association must be a JSON object.")
                buffer = buffer[end:]
                yield record
                strip()
                if buffer.startswith("]"):
                    buffer = buffer[1:]
                    break
                if not buffer.startswith(","):
                    raise SourcePayloadError(
                        "Expected a comma or closing array in saved GWAS JSON."
                    )
                buffer = buffer[1:]
        strip()
        if buffer:
            raise SourcePayloadError("Unexpected trailing content in saved GWAS JSON.")


def _selection(run: IngestionRun):
    if run.originating_run_id is None:
        return RawObject.ingestion_run_id == run.id
    cutoff = datetime.fromisoformat(run.metadata_json["selection_cutoff"])
    return (
        (RawObject.ingestion_run_id == run.originating_run_id) & (RawObject.created_at <= cutoff)
    ) | RawObject.id.in_(
        select(ProcessingObservation.raw_object_id).where(
            ProcessingObservation.ingestion_run_id == run.originating_run_id,
            ProcessingObservation.created_at <= cutoff,
        )
    )


def _metrics(session: Session, run: IngestionRun) -> dict[str, int]:
    counts = Counter(
        dict(
            session.execute(
                select(ProcessingObservation.disposition, func.count())
                .where(ProcessingObservation.ingestion_run_id == run.id)
                .group_by(ProcessingObservation.disposition)
            ).all()
        )
    )
    total = session.scalar(select(func.count()).select_from(RawObject).where(_selection(run))) or 0
    processed = sum(counts.values())
    return {
        "captured": total,
        "processed": processed,
        "loaded": counts["loaded"],
        "partial": counts["partial"],
        "quarantined": counts["quarantined"],
        "pending": total - processed,
        "failed": run.metrics_json.get("failed", 0),
        "failed_batches": session.scalar(
            select(func.count())
            .select_from(IngestionBatch)
            .where(IngestionBatch.ingestion_run_id == run.id, IngestionBatch.status == "failed")
        )
        or 0,
    }


def run_summary(engine: Engine, run_id: UUID) -> dict[str, Any]:
    with Session(engine) as session:
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise IngestionError("Ingestion run was not found.")
        return {
            "run_id": str(run.id),
            "originating_run_id": str(run.originating_run_id) if run.originating_run_id else None,
            "status": run.status,
            **_metrics(session, run),
            "capture_complete": run.metadata_json.get("capture_complete", False),
            "attempt": run.metadata_json.get("attempt", 1),
        }


def _batch(
    session: Session, run: IngestionRun, phase: str, status: str, metrics: dict, checkpoint: dict
) -> None:
    sequence = (
        session.scalar(
            select(func.max(IngestionBatch.sequence)).where(
                IngestionBatch.ingestion_run_id == run.id
            )
        )
        or 0
    ) + 1
    session.add(
        IngestionBatch(
            ingestion_run_id=run.id,
            sequence=sequence,
            phase=phase,
            status=status,
            metrics_json=metrics,
            checkpoint_json=checkpoint,
        )
    )


@contextmanager
def _run_lock(engine: Engine, run_id: UUID):
    key = int.from_bytes(hashlib.sha256(run_id.bytes).digest()[:8], "big", signed=True)
    with engine.connect() as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        connection.commit()
        if not acquired:
            raise IngestionError("This ingestion run is already being processed.")
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            connection.commit()


def _start_run(engine: Engine, metadata: dict, origin: UUID | None = None) -> UUID:
    run_id = uuid4()
    with Session(engine) as session, session.begin():
        source_id = _source(session)
        if origin is not None:
            original = session.get(IngestionRun, origin)
            if original is None or original.source_system_id != source_id:
                raise IngestionError("Originating GWAS ingestion run was not found.")
            if original.status == "running":
                raise IngestionError(
                    "Cannot replay a running source run; resume it first if interrupted."
                )
        session.add(
            IngestionRun(
                id=run_id,
                source_system_id=source_id,
                originating_run_id=origin,
                pipeline_name="gwas_replay_v1" if origin else "gwas_bounded_ingest_v1",
                metadata_json={
                    **metadata,
                    "capture_complete": origin is not None,
                    "attempt": 1,
                    **({"selection_cutoff": datetime.now(UTC).isoformat()} if origin else {}),
                },
                metrics_json={},
                status="running",
            )
        )
    return run_id


def _capture_batch(engine: Engine, run_id: UUID, buffer: list[tuple[dict, dict]]) -> None:
    with Session(engine) as session, session.begin():
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise IngestionError("Ingestion run disappeared.")
        inserted = 0
        for record, metadata in buffer:
            payload = encode_raw_json(record)
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            raw_id = uuid4()
            identifier = next(
                (
                    record[k]
                    for k in ("association_id", "associationId", "id")
                    if record.get(k) is not None and record[k] != ""
                ),
                None,
            )
            source_id = clean_text(identifier) if isinstance(identifier, (str, int)) else None
            source_id = source_id if source_id is None or len(source_id) <= 300 else None
            statement = (
                insert(RawObject)
                .values(
                    id=raw_id,
                    ingestion_run_id=run_id,
                    source_record_type="association",
                    source_record_id=source_id,
                    sha256=digest,
                    payload_text=payload,
                    storage_uri=f"postgresql:raw_object:{raw_id}",
                    metadata_json=metadata,
                    normalized_json={},
                    disposition="captured",
                )
                .on_conflict_do_nothing(constraint="uq_raw_object_run_payload")
                .returning(RawObject.id)
            )
            inserted += session.scalar(statement) is not None
        checkpoint = buffer[-1][1] if buffer else {}
        _batch(
            session,
            run,
            "capture",
            "completed",
            {"received": len(buffer), "inserted": inserted},
            checkpoint,
        )
        run.metadata_json = {**run.metadata_json, "source_checkpoint": checkpoint}
        run.metrics_json = _metrics(session, run)


async def _capture_live(engine: Engine, run_id: UUID, metadata: dict, batch_size: int) -> None:
    connector = GWASCatalogConnector(timeout=metadata["timeout"])
    records = (
        connector.iter_associations_for_trait(
            metadata["identifier"],
            show_child_traits=metadata["show_child_traits"],
            page_size=metadata["page_size"],
        )
        if metadata["mode"] == "trait"
        else connector.iter_associations_for_variant(
            metadata["identifier"], page_size=metadata["page_size"]
        )
    )
    buffer = []
    try:
        try:
            async for record in records:
                buffer.append(
                    (
                        record,
                        {
                            "acquisition": "live",
                            "query": {
                                "mode": metadata["mode"],
                                "identifier": metadata["identifier"],
                            },
                            "request": dict(connector.request_metadata),
                            "acquired_at": datetime.now(UTC).isoformat(),
                        },
                    )
                )
                if len(buffer) >= batch_size:
                    _capture_batch(engine, run_id, buffer)
                    buffer = []
        except (SourcePayloadError, json.JSONDecodeError, httpx.HTTPError):
            if buffer:
                _capture_batch(engine, run_id, buffer)
            with Session(engine) as session, session.begin():
                run = session.get(IngestionRun, run_id)
                run.metadata_json = {
                    **run.metadata_json,
                    "last_request": dict(connector.request_metadata),
                }
            raise
        if buffer:
            _capture_batch(engine, run_id, buffer)
        with Session(engine) as session, session.begin():
            run = session.get(IngestionRun, run_id)
            run.metadata_json = {
                **run.metadata_json,
                "source_checkpoint": {"request": dict(connector.request_metadata)},
            }
    finally:
        await connector.aclose()


def _capture_file(engine: Engine, run_id: UUID, metadata: dict, batch_size: int) -> None:
    path = Path(metadata["path"])
    if _file_digest(path) != metadata["file_sha256"]:
        raise IngestionError(
            "Saved input file changed since this run started; start a new ingestion."
        )
    buffer = []
    try:
        for record in iter_file_records(path):
            buffer.append(
                (
                    record,
                    {
                        "acquisition": "file",
                        "file_sha256": metadata["file_sha256"],
                        "filename": path.name,
                        "acquired_at": datetime.now(UTC).isoformat(),
                    },
                )
            )
            if len(buffer) >= batch_size:
                _capture_batch(engine, run_id, buffer)
                buffer = []
    except (SourcePayloadError, UnicodeError, OSError):
        if buffer:
            _capture_batch(engine, run_id, buffer)
        raise
    if buffer:
        _capture_batch(engine, run_id, buffer)
    if _file_digest(path) != metadata["file_sha256"]:
        raise IngestionError("Saved input changed while it was being read; run is incomplete.")


def _process_batch(engine: Engine, run_id: UUID, batch_size: int) -> int:
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(173528, 1)"))
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise IngestionError("Ingestion run disappeared.")
        raws = list(
            session.scalars(
                select(RawObject)
                .where(
                    _selection(run),
                    ~exists(
                        select(ProcessingObservation.id).where(
                            ProcessingObservation.ingestion_run_id == run_id,
                            ProcessingObservation.raw_object_id == RawObject.id,
                        )
                    ),
                )
                .order_by(RawObject.id)
                .limit(batch_size)
            )
        )
        if not raws:
            return 0
        for raw in raws:
            if (
                raw.payload_text is None
                or hashlib.sha256(raw.payload_text.encode("utf-8")).hexdigest() != raw.sha256
            ):
                raise IngestionError("Raw payload checksum verification failed.")
            record = decode_source_json(raw.payload_text)
            if not isinstance(record, dict):
                raise SourcePayloadError("Stored raw association is not an object.")
            dto = gwas_record_to_association(record)
            policy_findings = []
            if (
                raw.source_record_id
                and (
                    session.scalar(
                        select(func.count())
                        .select_from(RawObject)
                        .where(
                            _selection(run),
                            RawObject.source_record_id == raw.source_record_id,
                        )
                    )
                    or 0
                )
                > 1
            ):
                policy_findings.append(
                    QualityFinding(
                        "ASSOC_SOURCE_RECORD_CHANGED",
                        "error",
                        "Multiple distinct payloads for the same source ID were captured; no version chosen.",
                    )
                )
            if run.originating_run_id is not None and session.scalar(
                select(ProcessingObservation.id)
                .where(
                    ProcessingObservation.ingestion_run_id == run.originating_run_id,
                    ProcessingObservation.raw_object_id == raw.id,
                    ProcessingObservation.disposition == "quarantined",
                )
                .limit(1)
            ):
                policy_findings.append(
                    QualityFinding(
                        "ASSOC_HISTORICAL_QUARANTINE",
                        "error",
                        "Originating processing observation was quarantined.",
                    )
                )
            association_id, disposition, findings = process_association(
                session,
                run,
                raw,
                dto,
                preserve_raw=run.originating_run_id is not None,
                policy_findings=policy_findings,
            )
            if run.originating_run_id is None:
                raw.disposition = disposition
                raw.normalized_json = dto.model_dump(mode="json", exclude={"raw"})
                raw.p_value_candidate = dto.p_value if numeric_supported(dto.p_value) else None
                raw.p_value_text = dto.p_value_text
                for field in ("pvalue_mantissa", "pvalue_exponent"):
                    alias = "pValueMantissa" if field == "pvalue_mantissa" else "pValueExponent"
                    value = record.get(field, record.get(alias))
                    setattr(
                        raw,
                        "p_value_mantissa" if field == "pvalue_mantissa" else "p_value_exponent",
                        str(value) if value is not None else None,
                    )
            processing = ProcessingObservation(
                ingestion_run_id=run_id,
                raw_object_id=raw.id,
                association_id=association_id,
                disposition=disposition,
                normalized_json=dto.model_dump(mode="json", exclude={"raw"}),
            )
            session.add(processing)
            session.flush()
            _persist_findings(session, raw, findings, processing_id=processing.id, run_id=run_id)
        session.flush()
        _batch(
            session,
            run,
            "processing",
            "completed",
            {"processed": len(raws)},
            {"raw_ids": [str(raw.id) for raw in raws]},
        )
        run.metrics_json = {**_metrics(session, run), "failed": 0}
        return len(raws)


def _fail_run(
    engine: Engine, run_id: UUID, phase: str, error: BaseException, batch_size: int
) -> dict:
    with Session(engine) as session, session.begin():
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise IngestionError("Failed ingestion run cannot be recorded.")
        metrics = _metrics(session, run)
        failed = min(batch_size, metrics["pending"]) if phase == "processing" else 0
        _batch(
            session,
            run,
            phase,
            "failed",
            {"failed": failed, "error_type": type(error).__name__},
            {"source_checkpoint": run.metadata_json.get("source_checkpoint", {})},
        )
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        run.metrics_json = {**metrics, "failed": failed}
        session.add(
            QualityIssue(
                ingestion_run_id=run.id,
                entity_type="ingestion_run",
                entity_key=str(run.id),
                rule_code="INGESTION_FAILED",
                severity="error",
                message=f"Run failed during {phase}: {type(error).__name__}. Committed batches retained.",
                details_json={"phase": phase},
            )
        )
    return run_summary(engine, run_id)


def _execute(engine: Engine, run_id: UUID, *, resume: bool = False) -> dict[str, Any]:
    with _run_lock(engine, run_id):
        with Session(engine) as session, session.begin():
            run = session.get(IngestionRun, run_id)
            if run is None or run.pipeline_name not in {"gwas_bounded_ingest_v1", "gwas_replay_v1"}:
                raise IngestionError("Run is not a resumable GWAS CLI ingestion.")
            if run.status in {"completed", "completed_with_issues"}:
                return run_summary(engine, run_id)
            metadata = dict(run.metadata_json)
            if resume:
                metadata["attempt"] = metadata.get("attempt", 1) + 1
                run.metadata_json = metadata
                run.status = "running"
                run.finished_at = None
        phase = "capture"
        try:
            if not metadata["capture_complete"]:
                if metadata["mode"] == "file":
                    _capture_file(engine, run_id, metadata, metadata["batch_size"])
                else:
                    asyncio.run(_capture_live(engine, run_id, metadata, metadata["batch_size"]))
                with Session(engine) as session, session.begin():
                    run = session.get(IngestionRun, run_id)
                    run.metadata_json = {**run.metadata_json, "capture_complete": True}
            phase = "processing"
            while _process_batch(engine, run_id, metadata["batch_size"]):
                pass
            with Session(engine) as session, session.begin():
                run = session.get(IngestionRun, run_id)
                run.metrics_json = {**_metrics(session, run), "failed": 0}
                has_issues = session.scalar(
                    select(QualityIssue.id).where(QualityIssue.ingestion_run_id == run_id).limit(1)
                )
                run.status = "completed_with_issues" if has_issues else "completed"
                run.finished_at = datetime.now(UTC)
            return run_summary(engine, run_id)
        except (
            SQLAlchemyError,
            httpx.HTTPError,
            ValueError,
            OSError,
            UnicodeError,
            RuntimeError,
            KeyboardInterrupt,
        ) as error:
            logger.error("GWAS run %s failed during %s (%s).", run_id, phase, type(error).__name__)
            try:
                summary = _fail_run(engine, run_id, phase, error, metadata["batch_size"])
            except (SQLAlchemyError, IngestionError) as audit_error:
                logger.error(
                    "GWAS run %s failed audit persistence (%s).", run_id, type(audit_error).__name__
                )
                raise IngestionError(
                    f"Run {run_id} failed; audit update unavailable. Resume after restoring PostgreSQL."
                ) from None
            reason = (
                str(error)
                if isinstance(error, (IngestionError, SourcePayloadError))
                else type(error).__name__
            )
            raise IngestionError(
                f"Run failed during {phase}: {reason} Committed batches retained.", summary
            ) from None


def ingest_gwas(
    engine: Engine,
    *,
    mode: str,
    identifier: str | None = None,
    path: Path | None = None,
    batch_size: int = 500,
    page_size: int = 100,
    timeout: float = 30,
    show_child_traits: bool = True,
) -> dict[str, Any]:
    if not 1 <= batch_size <= 10000 or not 1 <= page_size <= 1000 or not 0 < timeout <= 300:
        raise IngestionError(
            "Batch size must be 1-10000, page size 1-1000, and timeout 0-300 seconds."
        )
    metadata: dict[str, Any] = {"mode": mode, "batch_size": batch_size}
    if mode == "file":
        if path is None:
            raise IngestionError("Saved GWAS ingestion requires a JSON file.")
        metadata.update(path=str(path.resolve()), file_sha256=_file_digest(path))
    elif mode in {"trait", "variant"}:
        pattern = r"EFO_[0-9]+" if mode == "trait" else r"rs[0-9]+"
        if identifier is None or re.fullmatch(pattern, identifier) is None:
            raise IngestionError(
                "Expected EFO_<digits>." if mode == "trait" else "Expected rs<digits>."
            )
        metadata.update(
            identifier=identifier,
            page_size=page_size,
            timeout=timeout,
            show_child_traits=show_child_traits,
        )
    else:
        raise IngestionError("Unknown GWAS ingestion mode.")
    validate_database(engine)
    return _execute(engine, _start_run(engine, metadata))


def replay_gwas(engine: Engine, origin: UUID, *, batch_size: int = 500) -> dict[str, Any]:
    if not 1 <= batch_size <= 10000:
        raise IngestionError("Batch size must be 1-10000.")
    validate_database(engine)
    with _run_lock(engine, origin):
        return _execute(
            engine, _start_run(engine, {"mode": "replay", "batch_size": batch_size}, origin)
        )


def resume_gwas(engine: Engine, run_id: UUID) -> dict[str, Any]:
    validate_database(engine)
    with Session(engine) as session:
        run = session.get(IngestionRun, run_id)
        origin = run.originating_run_id if run else None
    with _run_lock(engine, origin) if origin is not None else nullcontext():
        return _execute(engine, run_id, resume=True)
