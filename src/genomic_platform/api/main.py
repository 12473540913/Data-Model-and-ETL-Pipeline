import logging

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from genomic_platform.api.graphql_schema import graphql_router
from genomic_platform.api.limits import RequestLimits
from genomic_platform.api.rest import router as rest_router
from genomic_platform.services.catalog import QueryError
from genomic_platform.soap.server import wsgi_application

logger = logging.getLogger(__name__)
STATUS_CODES = {
    "NOT_FOUND": 404,
    "INVALID_INPUT": 422,
    "QUERY_LIMIT": 422,
    "DATABASE_UNAVAILABLE": 503,
    "INTERNAL_ERROR": 500,
}
app = FastAPI(
    title="Genomic Data Integration Platform",
    version="0.2.0",
    description="Read-only canonical data and bounded scientific evidence queries.",
)


@app.exception_handler(QueryError)
async def query_error(request: Request, error: QueryError):
    return JSONResponse(
        status_code=STATUS_CODES[error.code],
        content={"error": {"code": error.code, "message": str(error)}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "INVALID_INPUT", "message": "Invalid request arguments."}},
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, error: SQLAlchemyError):
    logger.error("API database unavailable (%s).", type(error).__name__)
    return JSONResponse(
        status_code=503,
        content={
            "error": {"code": "DATABASE_UNAVAILABLE", "message": "Database query unavailable."}
        },
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": "HTTP_ERROR", "message": "Request unavailable."}},
    )


app.include_router(rest_router)
app.include_router(graphql_router, prefix="/graphql")
app.mount("/soap", WSGIMiddleware(wsgi_application))
app.add_middleware(RequestLimits)
