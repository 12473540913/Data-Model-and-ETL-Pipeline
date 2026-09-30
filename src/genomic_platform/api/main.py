from fastapi import FastAPI
from genomic_platform.api.graphql_schema import graphql_router
from genomic_platform.api.rest import router as rest_router
app=FastAPI(title="Genomic Data Integration Platform",version="0.1.0",description="Canonical public-genomics data platform.")
app.include_router(rest_router)
app.include_router(graphql_router,prefix="/graphql")
