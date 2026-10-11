import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from genomic_platform.api.deps import build_catalog_service, get_session
from genomic_platform.services import contracts as c

router = APIRouter(
    prefix="/v1",
    responses={
        404: {"model": c.ErrorResponse, "description": "Record not found."},
        422: {"model": c.ErrorResponse, "description": "Invalid query arguments."},
        503: {"model": c.ErrorResponse, "description": "Database unavailable."},
    },
)
SessionDep = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=10000)]


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/variants/{rsid}", response_model=c.VariantView, tags=["Canonical"])
def get_variant(rsid: str, session: SessionDep):
    return build_catalog_service(session).execute("get_variant", rsid=rsid)


@router.get("/traits/{ontology_id}", response_model=c.TraitView, tags=["Canonical"])
def get_trait(ontology_id: str, session: SessionDep):
    return build_catalog_service(session).execute("get_trait", ontology_id=ontology_id)


@router.get("/studies/{study_id}", response_model=c.StudyView, tags=["Canonical"])
def get_study(study_id: str, session: SessionDep):
    return build_catalog_service(session).execute("get_study", study_id=study_id)


@router.get("/genes/{symbol}", response_model=c.GeneView, tags=["Canonical"])
def get_gene(symbol: str, session: SessionDep):
    return build_catalog_service(session).execute("get_gene", symbol=symbol)


@router.get("/associations/{association_id}", response_model=c.AssociationView, tags=["Canonical"])
def get_association(association_id: str, session: SessionDep):
    return build_catalog_service(session).execute("get_association", association_id=association_id)


@router.get("/variants/{rsid}/associations", response_model=c.AssociationPage, tags=["Canonical"])
def variant_associations(rsid: str, session: SessionDep, limit: Limit = 50, offset: Offset = 0):
    return build_catalog_service(session).execute(
        "variant_associations", rsid=rsid, limit=limit, offset=offset
    )


@router.get(
    "/traits/{ontology_id}/associations",
    response_model=c.AssociationPage | list[c.AssociationView],
    tags=["Canonical"],
)
def trait_associations(
    ontology_id: str, session: SessionDep, limit: Limit = 50, offset: Offset = 0
):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*[_:][A-Za-z0-9][A-Za-z0-9_.-]{0,90}", ontology_id):
        return (
            build_catalog_service(session)
            .execute("get_trait_associations", trait_name=ontology_id, limit=limit, offset=offset)
            .items
        )
    return build_catalog_service(session).execute(
        "trait_associations", ontology_id=ontology_id, limit=limit, offset=offset
    )


@router.get("/genes/{symbol}/associations", response_model=c.AssociationPage, tags=["Canonical"])
def gene_associations(symbol: str, session: SessionDep, limit: Limit = 50, offset: Offset = 0):
    return build_catalog_service(session).execute(
        "gene_associations", symbol=symbol, limit=limit, offset=offset
    )


@router.get(
    "/associations/{association_id}/sources", response_model=c.SourcePage, tags=["Evidence"]
)
def association_sources(
    association_id: str, session: SessionDep, limit: Limit = 50, offset: Offset = 0
):
    return build_catalog_service(session).execute(
        "association_sources", association_id=association_id, limit=limit, offset=offset
    )


@router.get("/variants/{rsid}/mappings", response_model=c.MappingPage, tags=["Evidence"])
def variant_mappings(
    rsid: str,
    session: SessionDep,
    limit: Limit = 50,
    offset: Offset = 0,
    assembly: str | None = None,
):
    return build_catalog_service(session).execute(
        "variant_mappings", rsid=rsid, limit=limit, offset=offset, assembly=assembly
    )


@router.get("/variants/{rsid}/enrichments", response_model=c.EnrichmentPage, tags=["Evidence"])
def variant_enrichments(rsid: str, session: SessionDep, limit: Limit = 50, offset: Offset = 0):
    return build_catalog_service(session).execute(
        "variant_enrichments", rsid=rsid, limit=limit, offset=offset
    )


@router.get("/evidence/raw/{raw_id}", response_model=c.RawView, tags=["Evidence"])
def raw_evidence(raw_id: str, session: SessionDep):
    """Safe evidence metadata and scientific representations; never returns file paths or payload bytes."""
    return build_catalog_service(session).execute("get_raw", raw_id=raw_id)


@router.get("/evidence/runs/{run_id}", response_model=c.RunView, tags=["Evidence"])
def get_run(run_id: str, session: SessionDep):
    return build_catalog_service(session).execute("get_run", run_id=run_id)


@router.get("/evidence/runs/{run_id}/raw", response_model=c.RawPage, tags=["Evidence"])
def run_raw(
    run_id: str,
    session: SessionDep,
    limit: Limit = 50,
    offset: Offset = 0,
    disposition: str | None = None,
):
    return build_catalog_service(session).execute(
        "run_raw", run_id=run_id, limit=limit, offset=offset, disposition=disposition
    )


@router.get(
    "/evidence/runs/{run_id}/processing", response_model=c.ProcessingPage, tags=["Evidence"]
)
def run_processing(run_id: str, session: SessionDep, limit: Limit = 50, offset: Offset = 0):
    return build_catalog_service(session).execute(
        "run_processing", run_id=run_id, limit=limit, offset=offset
    )


@router.get(
    "/evidence/{scope}/{identifier}/quality", response_model=c.QualityPage, tags=["Evidence"]
)
def quality(
    scope: str, identifier: str, session: SessionDep, limit: Limit = 50, offset: Offset = 0
):
    return build_catalog_service(session).execute(
        "quality", scope=scope, identifier=identifier, limit=limit, offset=offset
    )
