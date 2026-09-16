"""Remote MCP adapter. Tool logic lives in AccessService, never in analysts."""
from __future__ import annotations

import logging
import os
from time import monotonic
from typing import Any
from uuid import uuid4

from mcp.server.mcpserver import MCPServer

from .access import AccessService, redact_sensitive
from .store import Store

logger = logging.getLogger(__name__)
mcp = MCPServer("ATHENA", description="Authenticated ATHENA market intelligence and institutional memory")
_AUTH_META = {"securitySchemes": [{"type": "oauth2", "scopes": [os.getenv("ATHENA_OAUTH_SCOPE", "athena:access")]}]}


def _call(tool: str, method: str, *args, **kwargs):
    request_id = str(uuid4())
    start = monotonic()
    store = Store()
    try:
        result = redact_sensitive(getattr(AccessService(store), method)(*args, **kwargs))
        store.audit("mcp_request", {"request_id": request_id, "tool": tool, "run_id": result.get("run_id"), "status": "OK"})
        logger.info("mcp_request", extra={"request_id": request_id, "tool": tool, "run_id": result.get("run_id"), "status": "OK", "duration_ms": round((monotonic()-start)*1000)})
        return result
    except Exception as exc:
        store.audit("mcp_request", {"request_id": request_id, "tool": tool, "status": "ERROR", "error_class": type(exc).__name__})
        logger.warning("mcp_request_failed", extra={"request_id": request_id, "tool": tool, "error_class": type(exc).__name__})
        raise
    finally:
        store.close()


@mcp.tool(description="Run a full isolated-analyst ATHENA analysis of one listed ticker.", meta=_AUTH_META, structured_output=True)
def athena_deep_analysis(symbol: str, question: str | None = None, horizon: str | None = None, depth: str | None = None) -> dict[str, Any]:
    return _call("athena_deep_analysis", "deep_analysis", symbol, question, horizon, depth)


@mcp.tool(description="Compare two to five tickers using independent canonical ATHENA runs.", meta=_AUTH_META, structured_output=True)
def athena_compare(symbols: list[str], question: str | None = None, horizon: str | None = None) -> dict[str, Any]:
    return _call("athena_compare", "compare", symbols, question, horizon)


@mcp.tool(description="Ask an evidence-led market question through ATHENA's ON_DEMAND workflow.", meta=_AUTH_META, structured_output=True)
def athena_market_question(question: str, symbols: list[str] | None = None) -> dict[str, Any]:
    return _call("athena_market_question", "market_question", question, symbols)


@mcp.tool(description="Retrieve the latest stored Daily Brief without rerunning analysis.", meta=_AUTH_META, structured_output=True)
def athena_latest_daily_brief() -> dict[str, Any]:
    return _call("athena_latest_daily_brief", "latest_report", "DAILY_BRIEF")


@mcp.tool(description="Retrieve the latest stored US Market Post-Mortem.", meta=_AUTH_META, structured_output=True)
def athena_latest_post_mortem() -> dict[str, Any]:
    return _call("athena_latest_post_mortem", "latest_report", "POST_MORTEM")


@mcp.tool(description="Retrieve an immutable original prediction, latest outcome, and relevant lessons.", meta=_AUTH_META, structured_output=True)
def athena_prediction(prediction_id: str) -> dict[str, Any]:
    return _call("athena_prediction", "prediction", prediction_id)


@mcp.tool(description="Explain original evidence and current state of a stored prediction.", meta=_AUTH_META, structured_output=True)
def athena_explain_prediction(prediction_id: str, question: str | None = None) -> dict[str, Any]:
    return _call("athena_explain_prediction", "explain_prediction", prediction_id, question)


@mcp.tool(description="List persisted ACTIVE and NOT_ACTIVE_YET setups with as-of timestamps.", meta=_AUTH_META, structured_output=True)
def athena_active_setups() -> dict[str, Any]:
    return _call("athena_active_setups", "active_setups")


@mcp.tool(description="Read candidate/validated lessons and sample-aware historical performance.", meta=_AUTH_META, structured_output=True)
def athena_learning_summary(category: str | None = None, symbol: str | None = None, regime: str | None = None, direction: str | None = None) -> dict[str, Any]:
    return _call("athena_learning_summary", "learning_summary", category, symbol, regime, direction)


mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0")
