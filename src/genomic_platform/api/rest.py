from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from genomic_platform.api.deps import build_catalog_service, get_session
router=APIRouter(prefix="/v1")

@router.get("/health")
def health(): return {"status":"ok"}

@router.get("/variants/{rsid}")
def get_variant(rsid: str, session: Session=Depends(get_session)):
    v=build_catalog_service(session).get_variant(rsid)
    if not v: raise HTTPException(404,"Variant not found")
    return {"rsid":v.rsid,"assembly":v.assembly,"chromosome":v.chromosome,"position":v.position,"reference_allele":v.reference_allele,"alternate_alleles":v.alternate_alleles,"variant_class":v.variant_class,"most_severe_consequence":v.most_severe_consequence}

@router.get("/traits/{trait_name}/associations")
def associations_for_trait(trait_name: str, limit: int=Query(100, ge=1, le=1000), session: Session=Depends(get_session)):
    rows=build_catalog_service(session).get_trait_associations(trait_name,limit)
    return [{"id":str(a.id),"variant_id":str(a.variant_id),"trait_id":str(a.trait_id),"study_id":str(a.study_id) if a.study_id else None,"p_value":str(a.p_value) if a.p_value is not None else None,"effect_allele":a.effect_allele,"effect_size":str(a.effect_size) if a.effect_size is not None else None,"effect_size_type":a.effect_size_type} for a in rows]

@router.get("/genes/{symbol}/associations")
def gene_associations(symbol: str, session: Session=Depends(get_session)):
    gene=build_catalog_service(session).get_gene(symbol.upper())
    if not gene: raise HTTPException(404,"Gene not found")
    return {"gene":gene.symbol,"associations":[],"todo":"implement variant_gene join"}
