"""FastAPI 应用入口。

    make run     # http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import routes_assessments, routes_credibility, routes_health, routes_options, routes_validation
from app.config import get_settings
from app.llm.prompts import get_active_rubric
from app.storage.assessment_history import fail_interrupted_assessments

settings = get_settings()
logging.basicConfig(level=settings.log_level)

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Configuration errors must fail at startup rather than silently scoring
    # with a stale or incomplete rubric.
    get_active_rubric()
    from app.storage.db import init_db

    init_db()
    fail_interrupted_assessments()
    routes_credibility.fail_interrupted_credibility_tasks()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Paper Credibility Check API",
    version="1.0.0",
    description=(
        "Backend for Unified Field Contract v0.4. "
        "Interactive docs are at /docs; the OpenAPI schema at /openapi.json is the frontend contract."
    ),
)

# MVP 阶段前端本地开发需要跨域；上线前收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Every API error uses the same {code, message} envelope as the routes' own errors."""
    errors = jsonable_encoder(exc.errors())
    first = errors[0] if errors else {}
    field = ".".join(str(part) for part in first.get("loc", ())[1:]) or "request"
    return JSONResponse(status_code=422, content={"detail": {
        "code": "INVALID_REQUEST",
        "message": f"The request is invalid: {field}: {first.get('msg', 'invalid value')}.",
        "debug_id": None,
        "errors": errors,
    }})


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict):
        return await http_exception_handler(request, exc)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail), "debug_id": None}},
        headers=getattr(exc, "headers", None),
    )


app.include_router(routes_health.router)
app.include_router(routes_options.router)
app.include_router(routes_assessments.router)
app.include_router(routes_validation.router)
app.include_router(routes_credibility.router)
