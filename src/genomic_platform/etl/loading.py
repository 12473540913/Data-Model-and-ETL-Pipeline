import hashlib
import logging
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from genomic_platform.domain.models import (
    Association,
    AssociationSource,
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
from genomic_platform.domain.schemas import AssociationDTO, AssociationVariantDTO
from genomic_platform.etl.normalize import gwas_record_to_association, normalize_rsid
from genomic_platform.etl.raw_store import encode_raw_json
from genomic_platform.quality.rules import QualityFinding, validate_association

logger = logging.getLogger(__name__)
_IDENTITY_NAMESPACE = UUID("54832da9-438a-556b-a7d5-f471fd45a43a")
_PARTIAL_ERRORS = {"ASSOC_CONFLICTING_PVALUE", "ASSOC_NUMERIC_RANGE"}


@dataclass(frozen=True)
class LoadResult:
    run_id: UUID
    metrics: dict[str, int]


def _identity(kind: str, *parts: str) -> UUID:
    return uuid5(_IDENTITY_NAMESPACE, encode_raw_json([kind, *parts]))


def numeric_supported(value: Decimal | None) -> bool:
    """PostgreSQL unconstrained NUMERIC: 131072 integer / 16383 fractional digits."""
    if value is None:
        return True
    if not value.is_finite():
        return False
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        return False
    return value.adjusted() < 131072 and max(0, -exponent) <= 16383


def _source(session: Session) -> UUID:
    statement = (
        insert(SourceSystem)
        .values(
            id=_identity("source", "GWAS_CATALOG"),
            code="GWAS_CATALOG",
            name="NHGRI-EBI GWAS Catalog",
            base_url="https://www.ebi.ac.uk/gwas/",
            active=True,
        )
        .on_conflict_do_nothing(index_elements=["code"])
        .returning(SourceSystem.id)
    )
    source_id = session.scalar(statement)
    if source_id is None:
        source_id = session.scalar(
            select(SourceSystem.id).where(SourceSystem.code == "GWAS_CATALOG")
        )
    if source_id is None:
        raise RuntimeError("GWAS source could not be persisted.")
    return source_id


def _upsert_id(
    session: Session,
    model: Any,
    values: dict[str, Any],
    key: str,
    fill_missing: tuple[str, ...] = (),
) -> UUID:
    statement = insert(model).values(**values)
    if fill_missing:
        statement = statement.on_conflict_do_update(
            index_elements=[key],
            set_={
                name: func.coalesce(getattr(model, name), getattr(statement.excluded, name))
                for name in fill_missing
            },
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=[key])
    statement = statement.returning(model.id)
    entity_id = session.scalar(statement)
    if entity_id is None:
        entity_id = session.scalar(select(model.id).where(getattr(model, key) == values[key]))
    if entity_id is None:
        raise RuntimeError(f"{model.__tablename__} could not be persisted.")
    return entity_id


def _findings(dto: AssociationDTO) -> list[QualityFinding]:
    findings = validate_association(dto)
    if dto != gwas_record_to_association(dto.raw):
        findings.append(
            QualityFinding(
                "ASSOC_DTO_EVIDENCE_MISMATCH",
                "error",
                "DTO differs from normalization of its raw evidence, including quality findings.",
            )
        )
    variants = dto.variants or (
        [AssociationVariantDTO(rsid=dto.variant_rsid, effect_allele=dto.effect_allele)]
        if dto.variant_rsid
        else []
    )
    traits = dto.traits or ([dto.trait] if dto.trait is not None else [])
    if any(variant.rsid and normalize_rsid(variant.rsid) != variant.rsid for variant in variants):
        findings.append(
            QualityFinding("ASSOC_INVALID_RSID", "error", "Variant rsID is not normalized.")
        )
    for flag in ("multi_snp_haplotype", "snp_interaction"):
        value = dto.raw.get(flag)
        if value is not None and not isinstance(value, bool):
            findings.append(
                QualityFinding(
                    "ASSOC_INVALID_COMPOUND_FLAG",
                    "error",
                    f"{flag} must be a boolean when provided.",
                )
            )
        elif value is True:
            findings.append(
                QualityFinding(
                    "ASSOC_UNSUPPORTED_COMPOUND",
                    "error",
                    "Haplotype/interaction requires explicit compound modeling.",
                )
            )
    if len(variants) != 1 or len(traits) != 1:
        findings.append(
            QualityFinding(
                "ASSOC_UNSUPPORTED_CARDINALITY",
                "error",
                "Canonical loading requires exactly one variant and one trait; no combinations inferred.",
            )
        )
    if (
        dto.variants
        and dto.variant_rsid is not None
        and (len(dto.variants) != 1 or dto.variant_rsid != dto.variants[0].rsid)
    ) or (
        dto.traits
        and dto.trait is not None
        and (len(dto.traits) != 1 or dto.trait != dto.traits[0])
    ):
        findings.append(
            QualityFinding(
                "ASSOC_DTO_INCONSISTENCY", "error", "Singular fields conflict with collections."
            )
        )
    if (
        variants
        and dto.effect_allele is not None
        and (len(variants) != 1 or dto.effect_allele != variants[0].effect_allele)
    ):
        findings.append(
            QualityFinding(
                "ASSOC_DTO_INCONSISTENCY", "error", "Effect allele conflicts with variant mapping."
            )
        )
    for field, value, limit in [
        ("source_record_id", dto.source_record_id, 300),
        *[("variant_rsid", variant.rsid, 50) for variant in variants],
        *[("trait.name", trait.name, 300) for trait in traits],
        *[("trait.ontology_id", trait.ontology_id, 100) for trait in traits],
        *[("reported_trait", name, 300) for name in dto.reported_traits],
        ("study.accession", dto.study.accession if dto.study else None, 80),
        ("study.pubmed_id", dto.study.pubmed_id if dto.study else None, 30),
        ("study.author", dto.study.author if dto.study else None, 300),
        ("effect_size_type", dto.effect_size_type, 80),
        ("confidence_interval", dto.confidence_interval, 200),
    ]:
        if value is not None and len(value) > limit:
            findings.append(
                QualityFinding(
                    "ASSOC_FIELD_TOO_LONG", "error", f"{field} exceeds {limit} characters."
                )
            )
    for field, value in [("p_value", dto.p_value), ("effect_size", dto.effect_size)]:
        if not numeric_supported(value):
            findings.append(
                QualityFinding(
                    "ASSOC_NUMERIC_RANGE",
                    "error",
                    f"{field} exceeds PostgreSQL NUMERIC limits; only its text representation is retained.",
                )
            )
    return findings


def _persist_findings(session: Session, raw: RawObject, findings: list[QualityFinding]) -> None:
    grouped: dict[str, list[QualityFinding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.code].append(finding)
    for code, group in grouped.items():
        messages = list(dict.fromkeys(f.message for f in group))
        session.add(
            QualityIssue(
                id=uuid4(),
                ingestion_run_id=raw.ingestion_run_id,
                raw_object_id=raw.id,
                entity_type="association",
                entity_key=raw.source_record_id,
                rule_code=code,
                severity="error" if any(f.severity == "error" for f in group) else "warning",
                message="; ".join(messages),
                details_json={"messages": messages},
            )
        )


def _entity_findings(session: Session, dto: AssociationDTO) -> list[QualityFinding]:
    findings = []
    if dto.study and dto.study.accession:
        study = session.scalar(select(Study).where(Study.accession == dto.study.accession))
        if study is not None:
            for field in ("pubmed_id", "title", "author", "sample_description"):
                existing, incoming = getattr(study, field), getattr(dto.study, field)
                if existing is not None and incoming is not None and existing != incoming:
                    findings.append(
                        QualityFinding(
                            "STUDY_METADATA_CONFLICT",
                            "warning",
                            f"Study {dto.study.accession} has conflicting {field}; existing value retained, incoming evidence preserved in raw.",
                        )
                    )
    traits = dto.traits or ([dto.trait] if dto.trait is not None else [])
    for trait in traits:
        if trait.ontology_id:
            existing = session.scalar(select(Trait).where(Trait.ontology_id == trait.ontology_id))
            if existing is not None and trait.name and existing.name != trait.name:
                findings.append(
                    QualityFinding(
                        "TRAIT_LABEL_CONFLICT",
                        "warning",
                        f"Ontology {trait.ontology_id} has conflicting source labels; ontology identity retained, all source labels preserved in raw.",
                    )
                )
    return findings


def _canonical(session: Session, dto: AssociationDTO, source_id: UUID) -> UUID:
    variants = dto.variants or [
        AssociationVariantDTO(rsid=dto.variant_rsid, effect_allele=dto.effect_allele)
    ]
    traits = dto.traits or ([dto.trait] if dto.trait is not None else [])
    variant, trait = variants[0], traits[0]
    if not variant.rsid or not trait.name or not dto.source_record_id:
        raise ValueError("Canonical association lacks validated identity fields.")
    variant_id = _upsert_id(
        session,
        Variant,
        {
            "id": _identity("variant", variant.rsid),
            "rsid": variant.rsid,
            "assembly": None,
            "alternate_alleles": [],
            "metadata_json": {},
        },
        "rsid",
    )
    if trait.ontology_id:
        trait_id = _upsert_id(
            session,
            Trait,
            {
                "id": _identity("trait", trait.ontology_id),
                "ontology_id": trait.ontology_id,
                "name": trait.name,
            },
            "ontology_id",
        )
    else:
        trait_id = _upsert_id(
            session,
            Trait,
            {
                "id": _identity("source_trait", "GWAS_CATALOG", trait.name),
                "name": trait.name,
                "ontology_id": None,
            },
            "id",
        )
    for name in dto.reported_traits or ([trait.source_name] if trait.source_name else []):
        session.execute(
            insert(TraitSynonym)
            .values(
                id=_identity("trait_synonym", str(trait_id), str(source_id), name),
                trait_id=trait_id,
                source_system_id=source_id,
                synonym=name,
            )
            .on_conflict_do_nothing(index_elements=["trait_id", "source_system_id", "synonym"])
        )
    study_id = None
    if dto.study and dto.study.accession:
        study_id = _upsert_id(
            session,
            Study,
            {
                "id": _identity("study", "GWAS_CATALOG", dto.study.accession),
                **dto.study.model_dump(exclude={"metadata"}),
                "metadata_json": dto.study.metadata,
            },
            "accession",
            fill_missing=("pubmed_id", "title", "author", "sample_description"),
        )
    identity_key = hashlib.sha256(
        encode_raw_json(["GWAS_CATALOG", dto.source_record_id]).encode("utf-8")
    ).hexdigest()
    conflict = any(
        f.code in {"ASSOC_CONFLICTING_PVALUE", "ASSOC_FLOAT_PVALUE"}
        for f in dto.normalization_findings
    )
    pvalue = dto.p_value if not conflict and numeric_supported(dto.p_value) else None
    status = (
        "unreconciled"
        if conflict or not numeric_supported(dto.p_value)
        else ("reported" if pvalue is not None else "missing")
    )
    return _upsert_id(
        session,
        Association,
        {
            "id": _identity("gwas_observation", dto.source_record_id),
            "identity_key": identity_key,
            "variant_id": variant_id,
            "trait_id": trait_id,
            "study_id": study_id,
            "p_value": pvalue,
            "p_value_text": dto.p_value_text,
            "p_value_status": status,
            "effect_allele": variant.effect_allele,
            "other_allele": dto.other_allele,
            "effect_size": dto.effect_size if numeric_supported(dto.effect_size) else None,
            "effect_size_type": dto.effect_size_type,
            "confidence_interval": dto.confidence_interval,
            "metadata_json": {
                "reported_traits": dto.reported_traits,
                "effect_size_text": dto.effect_size_text,
                "effect_size_unit": dto.effect_size_unit,
                "effect_direction": dto.effect_direction,
                "effect_representation": "source_magnitude"
                if dto.effect_direction
                else "source_numeric",
                "identity_policy": "source_observation_v1",
            },
        },
        "identity_key",
    )


def _load_run(
    session: Session, run: IngestionRun, associations: Iterable[AssociationDTO]
) -> dict[str, int]:
    # Serialize same-source writers so a changed source record can never overwrite evidence.
    session.execute(text("SELECT pg_advisory_xact_lock(173528, 1)"))
    metrics: Counter[str] = Counter(seen=0, loaded=0, partial=0, quarantined=0, duplicates=0)
    seen_payloads: dict[str, AssociationDTO] = {}
    for dto in associations:
        metrics["seen"] += 1
        payload = encode_raw_json(dto.raw)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if digest in seen_payloads:
            if seen_payloads[digest] != dto:
                raise ValueError("Duplicate payload has inconsistent normalized DTOs.")
            metrics["duplicates"] += 1
            continue
        seen_payloads[digest] = dto
        raw_id = uuid4()
        raw = RawObject(
            id=raw_id,
            ingestion_run_id=run.id,
            source_record_type="association",
            source_record_id=dto.source_record_id,
            sha256=digest,
            storage_uri=f"postgresql:raw_object:{raw_id}",
            payload_text=payload,
            normalized_json=dto.model_dump(mode="json", exclude={"raw"}),
            metadata_json={"normalization_version": "gwas_v1", "source_code": "GWAS_CATALOG"},
            disposition="captured",
            p_value_candidate=dto.p_value if numeric_supported(dto.p_value) else None,
            p_value_text=dto.p_value_text,
            p_value_mantissa=str(dto.raw.get("pvalue_mantissa", dto.raw.get("pValueMantissa")))
            if (dto.raw.get("pvalue_mantissa", dto.raw.get("pValueMantissa")) is not None)
            else None,
            p_value_exponent=str(dto.raw.get("pvalue_exponent", dto.raw.get("pValueExponent")))
            if (dto.raw.get("pvalue_exponent", dto.raw.get("pValueExponent")) is not None)
            else None,
        )
        # Overlong source IDs are preserved in payload/normalized_json, not truncated.
        if raw.source_record_id is not None and len(raw.source_record_id) > 300:
            raw.source_record_id = None
        session.add(raw)
        session.flush()
        findings = _findings(dto) + _entity_findings(session, dto)
        existing = (
            session.scalar(
                select(AssociationSource).where(
                    AssociationSource.source_system_id == run.source_system_id,
                    AssociationSource.source_record_id == dto.source_record_id,
                )
            )
            if dto.source_record_id
            else None
        )
        if existing is not None:
            origin = session.get(RawObject, existing.raw_object_id)
            if origin is None or origin.sha256 != digest:
                findings.append(
                    QualityFinding(
                        "ASSOC_SOURCE_RECORD_CHANGED",
                        "error",
                        "Source ID already has different evidence; existing association left unchanged.",
                    )
                )
        blocking = [f for f in findings if f.severity == "error" and f.code not in _PARTIAL_ERRORS]
        if blocking:
            raw.disposition = "quarantined"
            metrics["quarantined"] += 1
        else:
            association_id = (
                existing.association_id
                if existing is not None
                else _canonical(session, dto, run.source_system_id)
            )
            raw.association_id = association_id
            partial = any(f.severity == "error" or f.code == "ASSOC_FLOAT_PVALUE" for f in findings)
            raw.disposition = "partial" if partial else "loaded"
            metrics["partial" if partial else "loaded"] += 1
            session.flush()
            if existing is None:
                session.add(
                    AssociationSource(
                        association_id=association_id,
                        source_system_id=run.source_system_id,
                        source_record_id=dto.source_record_id,
                        ingestion_run_id=run.id,
                        raw_object_id=raw.id,
                        source_payload={"payload_text": payload, "sha256": digest},
                    )
                )
        _persist_findings(session, raw, findings)
        session.flush()
    return dict(metrics)


def load_gwas_associations(
    engine: Engine, associations: Iterable[AssociationDTO], *, release_key: str | None = None
) -> LoadResult:
    """Own a transaction for a dataset; durable run audit survives loading failure."""
    if engine.dialect.name != "postgresql":
        raise ValueError("GWAS persistence requires PostgreSQL.")
    if release_key is not None and (not release_key.strip() or len(release_key) > 200):
        raise ValueError("release_key must contain 1-200 characters.")
    run_id = uuid4()
    with Session(engine) as session, session.begin():
        source_id = _source(session)
        release_id = None
        if release_key is not None:
            release_id = session.scalar(
                insert(DatasetRelease)
                .values(
                    id=_identity("release", str(source_id), release_key),
                    source_system_id=source_id,
                    release_key=release_key,
                    metadata_json={},
                )
                .on_conflict_do_nothing(index_elements=["source_system_id", "release_key"])
                .returning(DatasetRelease.id)
            )
            if release_id is None:
                release_id = session.scalar(
                    select(DatasetRelease.id).where(
                        DatasetRelease.source_system_id == source_id,
                        DatasetRelease.release_key == release_key,
                    )
                )
        session.add(
            IngestionRun(
                id=run_id,
                source_system_id=source_id,
                dataset_release_id=release_id,
                pipeline_name="gwas_canonical_load_v1",
                started_at=datetime.now(UTC),
                status="running",
                metrics_json={},
            )
        )
    try:
        with Session(engine) as session, session.begin():
            run = session.get(IngestionRun, run_id)
            if run is None:
                raise RuntimeError("Ingestion run disappeared before loading.")
            metrics = _load_run(session, run, associations)
            issue_count = session.scalar(
                select(QualityIssue.id).where(QualityIssue.ingestion_run_id == run_id).limit(1)
            )
            run.status = "completed_with_issues" if issue_count else "completed"
            run.finished_at = datetime.now(UTC)
            run.metrics_json = metrics
    except Exception as error:
        logger.exception("GWAS ingestion %s failed; dataset transaction rolled back.", run_id)
        with Session(engine) as session, session.begin():
            run = session.get(IngestionRun, run_id)
            if run is None:
                raise RuntimeError("Failed ingestion run cannot be recorded.") from error
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.metrics_json = {"error_type": type(error).__name__}
            session.add(
                QualityIssue(
                    ingestion_run_id=run_id,
                    entity_type="ingestion_run",
                    entity_key=str(run_id),
                    rule_code="INGESTION_FAILED",
                    severity="error",
                    message=f"Dataset transaction rolled back: {type(error).__name__}. See application logs.",
                    details_json={},
                )
            )
        raise
    logger.info("GWAS ingestion %s completed: %s", run_id, metrics)
    return LoadResult(run_id=run_id, metrics=metrics)
