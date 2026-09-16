from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import jwt
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import rsa
from types import SimpleNamespace

from athena.access import AccessService, redact_sensitive
from athena.agents.deterministic import CompanyInput, MacroInput, NewsInput, RegimeInput, SentimentInput, TechnicalInput, VolatilityInput
from athena.delivery import DeliveryService, FakeEmailProvider, ResendEmailProvider
from athena.email_render import render_daily_brief, render_post_mortem
from athena.market_data import Bar, FakeMarketDataProvider
from athena.mcp_server import mcp
from athena.models import AnalysisResult, Direction, Outcome, Prediction
from athena.orchestrator import Orchestrator
from athena.server import app
from athena.store import Store
from athena.worker import run_worker
from athena.workflows import Workflows

T0 = datetime(2026, 1, 5, 8, tzinfo=timezone.utc)


def persisted_report(store: Store, kind="DAILY_BRIEF", prediction=False):
    run_id = store.start_run(kind, "report")
    p = Prediction(prediction_id=f"p-{run_id}", subject="ABC", instrument="ABC", direction=Direction.LONG, thesis="sourced upside", horizon="5d", confidence=.7, entry_reference_level=Decimal(100), stop_level=Decimal(95), tp1=Decimal(110), tp2=Decimal(115), created_from_run=run_id) if prediction else None
    if p: store.save_prediction(p)
    result = AnalysisResult(run_id=run_id, question="report", bottom_line="WATCH: ABC", predictions=[p] if p else [], facts=["Sourced market fact"], risk_verdict="APPROVED" if p else "NO_SETUP")
    store.finish_run(run_id, result)
    return result


def test_email_rendering_and_resend_payload(tmp_path):
    store = Store(str(tmp_path / "email.db"))
    daily = persisted_report(store, prediction=True)
    message = render_daily_brief(daily)
    assert "Daily Market Brief" in message.html and "Best Setups" in message.html
    assert "TP1" in message.text and "Sourced market fact" in message.text
    post = persisted_report(store, "POST_MORTEM", prediction=True)
    p = post.predictions[0]
    o = Outcome(prediction_id=p.prediction_id, actual_result="UNTRIGGERED", trigger_activated=False, execution_occurred=False, outcome_status="EXPIRED", data_quality="AVAILABLE")
    store.save_outcome(o)
    store.audit("post_mortem_review", {"run_id": post.run_id, "outcome_ids": [o.outcome_id], "lesson_changes": []})
    rendered = render_post_mortem(post, store)
    assert "US Market Post-Mortem" in rendered.html and "UNTRIGGERED ≠ FAILED TRADE" in rendered.text
    assert p.prediction_id not in rendered.html or "ABC LONG" in rendered.html
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers["Idempotency-Key"].startswith("athena-email/")
        assert b'"html"' in request.content and b'"text"' in request.content
        return httpx.Response(200, json={"id": "resend-1"})
    provider = ResendEmailProvider("fake-key", httpx.Client(transport=httpx.MockTransport(handler)))
    sent = DeliveryService(store, provider, sender="Athena <brief@example.com>", recipients=("george@example.com",)).deliver(daily, "DAILY_BRIEF")
    assert sent.status == "SENT" and sent.provider_message_id == "resend-1" and len(seen) == 1
    store.close()


def test_delivery_missing_config_retry_idempotence_and_persistence(tmp_path):
    store = Store(str(tmp_path / "delivery.db"))
    missing = persisted_report(store)
    status = DeliveryService(store, sender="", recipients=(), api_key="").deliver(missing, "DAILY_BRIEF")
    assert status.status == "NOT_CONFIGURED" and len(store.deliveries_for_run(missing.run_id)) == 1
    report = persisted_report(store, prediction=True)
    fake = FakeEmailProvider(failures=2)
    service = DeliveryService(store, fake, sender="brief@example.com", recipients=("a@example.com", "b@example.com"))
    attempt = service.deliver(report, "DAILY_BRIEF")
    assert attempt.status == "SENT" and attempt.retry_count == 2
    assert [item.status for item in store.deliveries_for_run(report.run_id)] == ["FAILED", "FAILED", "SENT"]
    service.deliver(report, "DAILY_BRIEF")
    assert len(fake.sent) == 1 and len(store.deliveries_for_run(report.run_id)) == 3
    assert store.get_prediction(report.predictions[0].prediction_id) == report.predictions[0]
    store.close()


def test_delivery_permanent_failure_preserves_report(tmp_path):
    store = Store(str(tmp_path / "failure.db"))
    report = persisted_report(store, prediction=True)
    class Rejected:
        name = "REJECTED"
        def send(self, *args):
            from athena.delivery import DeliveryError
            raise DeliveryError("ResendHTTPError", "Resend HTTP 401", retryable=False)
    result = DeliveryService(store, Rejected(), sender="x@example.com", recipients=("y@example.com",)).deliver(report, "DAILY_BRIEF")
    assert result.status == "FAILED" and not result.retryable
    assert store.get_run_result(report.run_id) and store.get_prediction(report.predictions[0].prediction_id)
    store.close()


class FakeCollector:
    errors = []
    def collect(self, symbol, selected, run_id):
        bars = tuple(Bar(T0 + timedelta(days=i), 100+i, 101+i, 99+i, 100+i) for i in range(200))
        sources = {"Technical": TechnicalInput(symbol, bars, ("fake:bars",), bars), "Volatility": VolatilityInput(symbol, bars, ("fake:bars",)), "MarketRegime": RegimeInput(symbol, bars, ("fake:spy",)), "Macro": MacroInput(symbol, (("policy_rate", (Decimal(4), Decimal(4))),), ("fake:macro",)), "Company": CompanyInput(symbol, (("revenue_growth_yoy", Decimal(15)),), ("fake:company",)), "News": NewsInput(symbol, ("Raises guidance",), ("fake:news",)), "Sentiment": SentimentInput(symbol, (("positive_score", Decimal("0.5")),), ("fake:sentiment",))}
        return {name: sources[name] for name in selected if name in sources}, []


def test_access_service_deep_compare_history_and_learning(tmp_path):
    store = Store(str(tmp_path / "access.db"))
    service = AccessService(store, Workflows(Orchestrator(store, collector=FakeCollector()), store, FakeMarketDataProvider()))
    deep = service.deep_analysis("abc")
    assert deep["run_id"] and len(store.findings_for_run(deep["run_id"])) == 7
    assert deep["setup_status"] in {"NOT_ACTIVE_YET", "NO_SETUP"}
    compared = service.compare(["ABC", "XYZ"])
    assert len(compared["analyses"]) == 2 and compared["summary"].endswith("no winner is forced.")
    assert service.active_setups()["setups"]
    prediction_id = service.active_setups()["setups"][0]["prediction_id"]
    assert service.prediction(prediction_id)["prediction"]["prediction_id"] == prediction_id
    assert service.explain_prediction(prediction_id)["original_evidence"]
    assert "performance" in service.learning_summary()
    assert service.latest_report("DAILY_BRIEF")["available"] is False
    store.close()


def test_api_auth_idempotency_latest_and_mcp_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("ATHENA_API_TOKEN", "api-test-token")
    monkeypatch.setattr("athena.server.AccessService", lambda store: AccessService(store, Workflows(Orchestrator(store, collector=FakeCollector()), store, FakeMarketDataProvider())))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/analysis/deep", json={"symbol": "ABC"}).status_code == 401
        assert client.get("/reports/daily", headers={"Authorization": "Bearer wrong"}).status_code == 401
        headers = {"Authorization": "Bearer api-test-token", "Idempotency-Key": "deep-1"}
        first = client.post("/analysis/deep", json={"symbol": "ABC"}, headers=headers)
        second = client.post("/analysis/deep", json={"symbol": "ABC"}, headers=headers)
        assert first.status_code == second.status_code == 200 and first.json()["run_id"] == second.json()["run_id"]
        assert client.post("/analysis/deep", json={"symbol": "XYZ"}, headers=headers).status_code == 409
        assert client.post("/analysis/compare", json={"symbols": ["ABC"]}, headers={"Authorization": "Bearer api-test-token"}).status_code == 422
        store = Store(str(tmp_path / "api.db"))
        assert len(store.recent_runs()) == 1
        persisted_report(store)
        store.close()
        assert client.get("/reports/daily", headers={"Authorization": "Bearer api-test-token"}).json()["available"]
        rpc_headers = {"Authorization": "Bearer api-test-token", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
        rpc = client.post("/mcp", headers=rpc_headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert rpc.status_code == 200
        names = {tool["name"] for tool in rpc.json()["result"]["tools"]}
        assert {"athena_deep_analysis", "athena_compare", "athena_latest_daily_brief", "athena_prediction", "athena_active_setups", "athena_learning_summary"} <= names
        assert not any("trade" in name or "delete" in name or "validate" in name for name in names)
        assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).status_code == 401


def test_scheduled_runs_deliver_once_each(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "worker.db"))
    fake = FakeEmailProvider()
    delivery = DeliveryService(store, fake, sender="brief@example.com", recipients=("george@example.com",))
    workflow = Workflows(Orchestrator(store, runner=lambda run_id, question, kind: AnalysisResult(run_id=run_id, question=question, bottom_line=f"{kind}: done")), store, FakeMarketDataProvider())
    runner = lambda kind: workflow.dispatch(kind)
    first, status = run_worker("DAILY_BRIEF", scheduled=True, store=store, delivery=delivery, now=T0, runner=runner)
    again, duplicate = run_worker("DAILY_BRIEF", scheduled=True, store=store, delivery=delivery, now=T0, runner=runner)
    assert status == "COMPLETED" and duplicate.startswith("SKIPPED") and first.run_id == again.run_id
    evening = T0 + timedelta(hours=13)
    post, status = run_worker("POST_MORTEM", scheduled=True, store=store, delivery=delivery, now=evening, runner=runner)
    assert status == "COMPLETED" and post.run_id != first.run_id
    assert len(fake.sent) == 2
    assert store.deliveries_for_run(first.run_id)[0].status == "SENT"
    assert store.deliveries_for_run(post.run_id)[0].status == "SENT"
    store.close()


def test_mcp_tool_schemas_load():
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert len(names) == 9 and "athena_explain_prediction" in names
    assert all(tool.meta and tool.meta.get("securitySchemes") for tool in tools)


def test_mcp_tools_use_access_service_and_persisted_history(tmp_path, monkeypatch):
    import athena.mcp_server as module
    monkeypatch.setenv("ATHENA_DB_PATH", str(tmp_path / "mcp.db"))
    monkeypatch.setattr(module, "AccessService", lambda store: AccessService(store, Workflows(Orchestrator(store, collector=FakeCollector()), store, FakeMarketDataProvider())))
    store = Store(str(tmp_path / "mcp.db"))
    brief = persisted_report(store, prediction=True)
    store.close()
    deep = asyncio.run(mcp.call_tool("athena_deep_analysis", {"symbol": "ABC"}))
    assert deep.structured_content["run_id"]
    compared = asyncio.run(mcp.call_tool("athena_compare", {"symbols": ["ABC", "XYZ"]}))
    assert len(compared.structured_content["analyses"]) == 2
    latest = asyncio.run(mcp.call_tool("athena_latest_daily_brief", {}))
    assert latest.structured_content["report"]["run_id"] == brief.run_id
    lookup = asyncio.run(mcp.call_tool("athena_prediction", {"prediction_id": brief.predictions[0].prediction_id}))
    assert lookup.structured_content["prediction"]["prediction_id"] == brief.predictions[0].prediction_id
    active = asyncio.run(mcp.call_tool("athena_active_setups", {}))
    assert active.structured_content["setups"]
    learning = asyncio.run(mcp.call_tool("athena_learning_summary", {}))
    assert "performance" in learning.structured_content


def test_oauth_resource_metadata_and_signed_token(tmp_path, monkeypatch):
    import athena.auth as auth
    monkeypatch.setenv("ATHENA_DB_PATH", str(tmp_path / "oauth.db"))
    monkeypatch.delenv("ATHENA_API_TOKEN", raising=False)
    monkeypatch.setenv("ATHENA_PUBLIC_BASE_URL", "https://athena.example.com")
    monkeypatch.setenv("ATHENA_OAUTH_ISSUER", "https://identity.example.com")
    monkeypatch.setenv("ATHENA_OAUTH_AUDIENCE", "https://athena.example.com/mcp")
    monkeypatch.setenv("ATHENA_OAUTH_JWKS_URL", "https://identity.example.com/jwks")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth, "_jwks_client", lambda url: SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())))
    token = jwt.encode({"iss": "https://identity.example.com", "aud": "https://athena.example.com/mcp", "exp": 2000000000, "scope": "athena:access"}, key, algorithm="RS256")
    bad = jwt.encode({"iss": "https://identity.example.com", "aud": "wrong", "exp": 2000000000, "scope": "athena:access"}, key, algorithm="RS256")
    client = TestClient(app)
    metadata = client.get("/.well-known/oauth-protected-resource")
    assert metadata.status_code == 200 and metadata.json()["authorization_servers"] == ["https://identity.example.com"]
    assert client.get("/setups/active", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    denied = client.get("/setups/active", headers={"Authorization": f"Bearer {bad}"})
    assert denied.status_code == 401 and "resource_metadata" in denied.headers["WWW-Authenticate"]


def test_provider_secrets_are_redacted_from_access_payload(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "private-source-key")
    assert redact_sensitive({"errors": ["request to private-source-key failed"]}) == {"errors": ["request to [REDACTED] failed"]}
