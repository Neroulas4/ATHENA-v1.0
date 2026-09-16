from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from athena.agents.deterministic import TechnicalInput
from athena.market_data import Bar, FakeMarketDataProvider
from athena.models import AnalysisResult, Direction, Prediction
from athena.orchestrator import Orchestrator
from athena.server import app
from athena.store import Store
from athena.workflows import Workflows

T0 = datetime(2026, 1, 5, 16, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    value = Store(str(tmp_path / "db.sqlite"))
    yield value
    value.close()


def test_daily_brief_scans_configured_symbols_and_persists_findings(store, monkeypatch):
    monkeypatch.setenv("ATHENA_SYMBOLS", "ABC,XYZ")
    store.save_prediction(Prediction(prediction_id="prior-abc", subject="ABC", instrument="ABC", direction=Direction.LONG, thesis="Prior bullish thesis", horizon="5d", confidence=.6))
    class Collector:
        errors = []
        def collect(self, symbol, selected, run_id):
            from athena.agents.deterministic import (CompanyInput, MacroInput, NewsInput, RegimeInput,
                                                      SentimentInput, VolatilityInput)
            bars = tuple(Bar(T0 + timedelta(days=i), 100, 101, 99, 100) for i in range(200))
            data = {"Technical": TechnicalInput(symbol, bars), "Volatility": VolatilityInput(symbol, bars), "MarketRegime": RegimeInput(symbol, bars), "Macro": MacroInput(symbol, ()), "Company": CompanyInput(symbol, ()), "News": NewsInput(symbol, ()), "Sentiment": SentimentInput(symbol, ())}
            return data, []
    result = Workflows(Orchestrator(store, collector=Collector()), store).daily_brief()
    assert "ABC" in result.bottom_line and "XYZ" in result.bottom_line
    assert len(store.findings_for_run(result.run_id)) == 14
    assert result.predictions == []
    assert any("prior-abc" in inference and "original thesis remains unchanged" in inference for inference in result.inferences)
    assert store.recent_runs()[0]["kind"] == "DAILY_BRIEF"


def test_postmortem_records_real_outcome_and_avoids_duplicate(store):
    prediction = Prediction(prediction_id="post-1", timestamp=T0, subject="ABC", instrument="ABC", direction=Direction.LONG, thesis="upside", horizon="2d", confidence=.6, trigger_operator="ABOVE", entry_reference_level=100, stop_level=95, tp1=110)
    store.save_prediction(prediction)
    bars = [Bar(T0, 99, 101, 98, 100), Bar(T0 + timedelta(days=1), 101, 111, 100, 110), Bar(T0 + timedelta(days=2), 110, 112, 109, 111)]
    flow = Workflows(Orchestrator(store), store, FakeMarketDataProvider({"ABC": bars}))
    first = flow.post_mortem()
    assert "post-1" in " ".join(first.facts)
    assert len(store.outcomes_for("post-1")) == 1
    flow.post_mortem()
    assert len(store.outcomes_for("post-1")) == 1


def test_api_valid_on_demand_uses_canonical_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("ATHENA_API_TOKEN", "test-token")
    client = TestClient(app)
    response = client.post("/run", json={"kind": "ON_DEMAND", "question": "What is the outlook for $ABC?"}, headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json()["run_id"]
    stored = Store(str(tmp_path / "api.db"))
    assert stored.recent_runs()[0]["kind"] == "ON_DEMAND"
    stored.close()


def test_worker_dispatch(monkeypatch, capsys):
    from athena import worker
    monkeypatch.setattr(worker, "Store", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(worker, "execute", lambda kind: AnalysisResult(run_id="worker-run", bottom_line=kind))
    monkeypatch.setattr(sys, "argv", ["athena-worker", "DAILY_BRIEF"])
    worker.main()
    assert "worker-run" in capsys.readouterr().out


def test_sqlite_migration_preserves_legacy_prediction(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE predictions (prediction_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
    prediction = Prediction(subject="Legacy", thesis="original", horizon="1d", confidence=.5)
    connection.execute("INSERT INTO predictions VALUES (?,?,?)", (prediction.prediction_id, prediction.model_dump_json(), prediction.timestamp.isoformat()))
    connection.commit(); connection.close()
    store = Store(str(path))
    assert store.get_prediction(prediction.prediction_id) == prediction
    assert store._execute("SELECT name FROM sqlite_master WHERE name='analyst_findings'").fetchone()
    store.close()


def test_postgres_schema_path_is_parameterized(monkeypatch):
    statements = []
    class FakeDB:
        def execute(self, query, params=()):
            statements.append((query, params))
            return self
        def commit(self): pass
        def close(self): pass
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda url: FakeDB()))
    store = Store("postgresql://example.invalid/athena")
    store._execute("SELECT ?", ("safe",))
    assert statements[-1] == ("SELECT %s", ("safe",))
    assert any("CREATE TABLE IF NOT EXISTS analyst_findings" in sql for sql, _ in statements)
    store.close()
