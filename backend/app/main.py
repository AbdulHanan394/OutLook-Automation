import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.responses import Response

from app.api import (
    routes_ask,
    routes_assistant,
    routes_auth,
    routes_chats,
    routes_commitments,
    routes_contacts,
    routes_mail,
    routes_mailbox,
    routes_search,
    routes_webhook,
)
from app.db import engine
from app.observability.logging import configure_logging

configure_logging()
logger = logging.getLogger("app.http")
app = FastAPI(title="Email Finder API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-Id"],
)
for router in [routes_auth.router, routes_mailbox.router, routes_search.router, routes_assistant.router, routes_commitments.router, routes_contacts.router, routes_webhook.router, routes_mail.router, routes_ask.router, routes_chats.router]:
    app.include_router(router)

@app.middleware("http")
async def request_id(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    request_id = request.headers.get("X-Request-Id", f"req_{uuid.uuid4().hex}")
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        # Runs through the redacting filter installed above, so a query string
        # can never leak a token into the log.
        logger.info(
            "%s %s %s in %.1fms",
            request.method,
            request.url.path,
            request_id,
            (time.perf_counter() - start) * 1000,
        )
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response

@app.exception_handler(Exception)
async def errors(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, (ValueError,)):
        return JSONResponse(status_code=400, content={"error": {"code": "bad_request", "message": "Invalid request.", "request_id": request.headers.get("X-Request-Id")}})
    raise exc

@app.get("/healthz")
def healthz() -> dict[str, str]:
    with engine.connect() as connection: connection.execute(text("SELECT 1"))
    return {"status": "ok"}

@app.get("/readyz")
def readyz() -> dict[str, str]:
    return healthz()
