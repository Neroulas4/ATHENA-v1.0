"""Authenticated HTTP and MCP access to canonical ATHENA services."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .access import AccessService, redact_sensitive
from .auth import auth_challenge, resource_metadata, verify_bearer
from .mcp_server import mcp, mcp_app
from .store import Store

logger = logging.getLogger(__name__)
_requests: dict[str, deque[float]] = defaultdict(deque)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="ATHENA Runtime", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    start = monotonic()
    if request.url.path not in {"/health", "/.well-known/oauth-protected-resource"} and not verify_bearer(request.headers.get("authorization")):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401, headers={"X-Request-ID": request_id, "WWW-Authenticate": auth_challenge()})
    if request.method == "POST" and request.url.path in {"/run", "/analysis/deep", "/analysis/compare", "/questions", "/mcp"}:
        token = request.headers.get("authorization", "")
        bucket = hashlib.sha256((token + (request.client.host if request.client else "")).encode()).hexdigest()
        now = monotonic()
        queue = _requests[bucket]
        while queue and queue[0] <= now - 60: queue.popleft()
        if len(queue) >= 20:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429, headers={"X-Request-ID": request_id})
        queue.append(now)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info("api_request", extra={"request_id": request_id, "path": request.url.path, "status": response.status_code, "duration_ms": round((monotonic()-start)*1000)})
    return response


class RunRequest(BaseModel):
    kind: str
    question: str | None = Field(default=None, max_length=1000)


class DeepRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    question: str | None = Field(default=None, max_length=1000)
    horizon: str | None = Field(default=None, max_length=30)
    depth: str | None = None


class CompareRequest(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=5)
    question: str | None = Field(default=None, max_length=1000)
    horizon: str | None = Field(default=None, max_length=30)


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    symbols: list[str] | None = Field(default=None, max_length=5)


def _access(request: Request, body: BaseModel | None, method: str, *args):
    store = Store()
    key = None
    start = monotonic()
    try:
        supplied = request.headers.get("idempotency-key")
        if supplied and request.method == "POST":
            if len(supplied) > 128: raise HTTPException(422, "Idempotency-Key exceeds 128 characters")
            token = request.headers.get("authorization", "")
            key = hashlib.sha256(f"{token}:{request.url.path}:{supplied}".encode()).hexdigest()
            fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json") if body else {}, sort_keys=True).encode()).hexdigest()
            state, cached = store.claim_api_request(key, fingerprint)
            if state == "CONFLICT": raise HTTPException(409, "Idempotency key used with a different request")
            if state == "IN_PROGRESS": raise HTTPException(409, "Request with this key is in progress")
            if state == "COMPLETE": return cached
        result = redact_sensitive(getattr(AccessService(store), method)(*args))
        if key: store.complete_api_request(key, result)
        store.audit("api_access", {"request_id": request.state.request_id, "path": request.url.path, "run_id": result.get("run_id"), "status": "OK", "duration_ms": round((monotonic()-start)*1000)})
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        if key: store.release_api_request(key)
        raise HTTPException(404 if "not found" in str(exc) else 422, str(exc)) from exc
    except Exception:
        if key: store.release_api_request(key)
        logger.exception("api_access_failed", extra={"path": request.url.path})
        raise HTTPException(500, "ATHENA access failed")
    finally:
        store.close()


@app.get("/health")
def health(): return {"status": "ok", "service": "athena-runtime"}


@app.get("/.well-known/oauth-protected-resource")
def oauth_resource():
    metadata = resource_metadata()
    if metadata is None: raise HTTPException(404, "OAuth is not configured")
    return metadata


@app.post("/run")
def run(body: RunRequest, request: Request):
    if body.kind != "ON_DEMAND": raise HTTPException(400, "Only ON_DEMAND is available through the public API")
    if not body.question: raise HTTPException(422, "ON_DEMAND requires question")
    return _access(request, body, "market_question", body.question)


@app.post("/analysis/deep")
def deep(body: DeepRequest, request: Request):
    return _access(request, body, "deep_analysis", body.symbol, body.question, body.horizon, body.depth)


@app.post("/analysis/compare")
def compare(body: CompareRequest, request: Request):
    return _access(request, body, "compare", body.symbols, body.question, body.horizon)


@app.post("/questions")
def question(body: QuestionRequest, request: Request):
    return _access(request, body, "market_question", body.question, body.symbols)


@app.get("/reports/daily")
def daily(request: Request): return _access(request, None, "latest_report", "DAILY_BRIEF")


@app.get("/reports/post-mortem")
def post_mortem(request: Request): return _access(request, None, "latest_report", "POST_MORTEM")


@app.get("/predictions/{prediction_id}")
def prediction(prediction_id: str, request: Request): return _access(request, None, "prediction", prediction_id)


@app.get("/predictions/{prediction_id}/explain")
def explain(prediction_id: str, request: Request, question: str | None = None): return _access(request, None, "explain_prediction", prediction_id, question)


@app.get("/setups/active")
def active(request: Request): return _access(request, None, "active_setups")


@app.get("/learning")
def learning(request: Request, category: str | None = None, symbol: str | None = None, regime: str | None = None, direction: str | None = None):
    return _access(request, None, "learning_summary", category, symbol, regime, direction)


app.mount("/", mcp_app)
