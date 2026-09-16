from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from athena.agents.deterministic import (ANALYSTS, CompanyInput, MacroInput, NewsInput,
                                          TechnicalInput, technical)
from athena.collectors import CollectorService
from athena.decision import challenge, synthesize
from athena.evaluation import build_outcome, evaluate_trigger, horizon_end
from athena.learning import LessonEngine
from athena.market_data import (AlpacaMarketDataProvider, Bar, FakeMarketDataProvider,
                                MarketDataService, UnavailableMarketDataProvider)
from athena.models import (AnalysisResult, Direction, Lesson, LessonContext, LessonStatus, Outcome,
                           Prediction, SetupState, SpecialistFinding)
from athena.orchestrator import Orchestrator
from athena.schedule import due_key
from athena.store import Store
from athena.workflows import Workflows

T0 = datetime(2026, 1, 5, 16, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path):
    value = Store(str(tmp_path / "athena.db"))
    yield value
    value.close()


def bar(day: int, lo: int, hi: int, close: int) -> Bar:
    return Bar(T0 + timedelta(days=day), close, hi, lo, close)


def setup(**updates) -> Prediction:
    data = dict(prediction_id="p1", timestamp=T0, subject="ABC", instrument="ABC", direction=Direction.LONG, thesis="test", horizon="2d", confidence=.7, entry_condition="Price above 100", trigger_operator="ABOVE", entry_reference_level=Decimal(100), stop_level=Decimal(95), tp1=Decimal(110), tp2=Decimal(120))
    data.update(updates)
    return Prediction(**data)


def test_analyst_input_is_immutable_and_specialized():
    value = TechnicalInput("ABC", tuple(bar(i, 90, 100, 95) for i in range(50)))
    with pytest.raises(Exception): value.bars = ()
    assert technical(value).specialist == "Technical"
    assert "news" not in TechnicalInput.__dataclass_fields__
    assert "bars" not in NewsInput.__dataclass_fields__


def test_market_and_prediction_values_are_decimal_and_timestamps_aware():
    market_bar = bar(0, 90, 100, 95)
    assert isinstance(market_bar.close, Decimal)
    prediction = setup()
    assert isinstance(prediction.entry_reference_level, Decimal)
    with pytest.raises(ValueError):
        Bar(datetime(2026, 1, 5), 99, 101, 98, 100)
    with pytest.raises(ValueError):
        setup(timestamp=datetime(2026, 1, 5))
    with pytest.raises(ValueError):
        Bar(T0, "NaN", 101, 98, 100)


def test_isolation_and_synthesis_runs_after_all_analysts(store, monkeypatch):
    import athena.orchestrator as module
    completed = []
    def make(name):
        def analyze(data):
            assert not hasattr(data, "specialist_findings")
            completed.append(name)
            return SpecialistFinding(specialist=name, subject="ABC", stance="MIXED")
        return analyze
    monkeypatch.setattr(module, "ANALYSTS", {"Technical": make("Technical"), "News": make("News")})
    monkeypatch.setattr(Orchestrator, "select_specialists", staticmethod(lambda q, k="ON_DEMAND": ["Technical", "News"]))
    def synthetic(*args):
        assert completed == ["News", "Technical"]
        return AnalysisResult(run_id=args[0], bottom_line="isolated")
    monkeypatch.setattr(module, "synthesize", synthetic)
    class Collector:
        errors = []
        def collect(self, symbol, selected, run_id):
            return {"Technical": TechnicalInput(symbol, ()), "News": NewsInput(symbol, ())}, []
    result = Orchestrator(store, collector=Collector()).run("$ABC news")
    assert result.bottom_line == "isolated"
    assert len(store.findings_for_run(result.run_id)) == 2


def test_market_service_fallback_and_quality():
    class Broken:
        name = "broken"
        def historical_bars(self, *args): raise httpx.ConnectError("offline")
    service = MarketDataService([Broken(), FakeMarketDataProvider({"ABC": [bar(0, 90, 100, 95)]})])
    assert service.historical_bars("ABC", T0, T0)[0].close == 95
    assert service.last_status["ABC"] == "fake"
    assert MarketDataService([UnavailableMarketDataProvider()]).historical_bars("ABC", T0, T0) is None


def test_alpaca_contract_with_mock_http():
    def handler(request):
        assert request.headers["APCA-API-KEY-ID"] == "test"
        assert request.url.params["feed"] == "iex"
        if request.url.path.endswith("/bars"):
            return httpx.Response(200, json={"bars": [{"t": "2026-01-05T16:00:00Z", "o": 99, "h": 101, "l": 98, "c": 100, "v": 5}]})
        return httpx.Response(200, json={"trade": {"p": 100}})
    provider = AlpacaMarketDataProvider("test", "secret", httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.historical_bars("ABC", T0, T0)[0].close == Decimal(100)
    assert provider.current_price("ABC") == Decimal(100)
    assert provider.metadata("ABC")["feed"] == "iex-single-exchange"


def test_finnhub_and_fred_collector_contract(store, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test")
    monkeypatch.setenv("FRED_API_KEY", "test")
    def handler(request):
        if "fred" in request.url.host: return httpx.Response(200, json={"observations": [{"date": "2026-01-01", "value": "4.0"}, {"date": "2025-12-01", "value": "3.0"}]})
        if request.url.path.endswith("metric"): return httpx.Response(200, json={"metric": {"revenueGrowthTTMYoy": 12}})
        if request.url.path.endswith("company-news"): return httpx.Response(200, json=[{"headline": "Raises guidance", "url": "https://example.test/news"}])
        return httpx.Response(200, json={"reddit": [{"positiveMention": 3, "negativeMention": 1}], "twitter": []})
    collector = CollectorService(MarketDataService(), httpx.Client(transport=httpx.MockTransport(handler)))
    inputs, evidence = collector.collect("ABC", {"Macro", "Company", "News", "Sentiment"}, "run")
    assert isinstance(inputs["Macro"], MacroInput)
    assert isinstance(inputs["Company"], CompanyInput)
    assert inputs["News"].headlines == ("Raises guidance",)
    assert inputs["Sentiment"].metrics[0][1] == Decimal("0.5")
    assert evidence and all(e.reference for e in evidence)


@pytest.mark.parametrize("direction,operator,bars,expected", [
    (Direction.LONG, "ABOVE", [bar(0, 98, 101, 100), bar(1, 100, 112, 111)], True),
    (Direction.SHORT, "BELOW", [bar(0, 99, 102, 100), bar(1, 89, 100, 90)], True),
])
def test_long_short_triggers(direction, operator, bars, expected):
    prediction = setup(direction=direction, trigger_operator=operator, stop_level=95 if direction == Direction.LONG else 105, tp1=110 if direction == Direction.LONG else 90, tp2=120 if direction == Direction.LONG else 80)
    outcome = build_outcome(prediction, FakeMarketDataProvider({"ABC": bars}), T0 + timedelta(days=1))
    assert outcome.trigger_activated is expected
    assert outcome.execution_occurred is None
    assert outcome.tp1_hit is True


def test_lifecycle_active_invalidated_completed_and_expired():
    prediction = setup()
    active = build_outcome(prediction, FakeMarketDataProvider({"ABC": [bar(0, 98, 101, 100)]}), T0)
    invalid = build_outcome(prediction, FakeMarketDataProvider({"ABC": [bar(0, 98, 101, 100), bar(1, 94, 103, 95)]}), T0 + timedelta(days=1))
    complete = build_outcome(prediction, FakeMarketDataProvider({"ABC": [bar(0, 98, 101, 100), bar(1, 100, 121, 120)]}), T0 + timedelta(days=1))
    expired = build_outcome(prediction, FakeMarketDataProvider({"ABC": [bar(0, 90, 99, 95), bar(2, 90, 99, 95)]}), T0 + timedelta(days=2))
    assert [active.setup_state, invalid.setup_state, complete.setup_state, expired.setup_state] == [SetupState.ACTIVE, SetupState.INVALIDATED, SetupState.COMPLETED, SetupState.EXPIRED]
    assert expired.execution_occurred is False and expired.thesis_correct is None


def test_unknown_free_text_condition_and_missing_data_are_pending():
    prediction = setup(trigger_operator=None, entry_condition="when the market feels ready")
    assert build_outcome(prediction, FakeMarketDataProvider({"ABC": [bar(0, 98, 101, 100)]})).outcome_status == "PENDING"
    assert build_outcome(setup(), UnavailableMarketDataProvider()).data_quality == "UNAVAILABLE"


def test_same_bar_stop_target_order_stays_pending():
    prediction = setup()
    outcome = build_outcome(prediction, FakeMarketDataProvider({"ABC": [bar(0, 94, 111, 100)]}), T0)
    assert outcome.trigger_activated
    assert outcome.stop_hit and outcome.tp1_hit
    assert outcome.outcome_status == "PENDING"
    assert outcome.timing_correct is None


def test_pending_outcome_re_evaluation_and_immutable_history(store):
    prediction = setup()
    store.save_prediction(prediction)
    flow = Workflows(Orchestrator(store), store, UnavailableMarketDataProvider())
    first, changed = flow.evaluate_prediction(prediction.prediction_id)
    assert changed and first.outcome_status == "PENDING"
    flow.provider = FakeMarketDataProvider({"ABC": [bar(0, 98, 101, 100)]})
    second, changed = flow.evaluate_prediction(prediction.prediction_id)
    assert changed and second.trigger_activated
    assert len(store.outcomes_for(prediction.prediction_id)) == 2
    assert store.get_prediction(prediction.prediction_id) == prediction


def test_cancellation_is_append_only_and_final(store):
    prediction = setup()
    store.save_prediction(prediction)
    flow = Workflows(Orchestrator(store), store)
    cancelled = flow.cancel_prediction(prediction.prediction_id, "Catalyst withdrawn")
    assert cancelled.setup_state == SetupState.CANCELLED
    assert store.get_prediction(prediction.prediction_id) == prediction
    assert prediction not in store.eligible_predictions()
    with pytest.raises(ValueError): flow.cancel_prediction(prediction.prediction_id, "again")


def test_lesson_count_uses_independent_predictions_and_audits(store):
    engine = LessonEngine(store)
    first = Outcome(prediction_id="one", actual_result="x")
    engine.observe("Timing pattern", "timing", first, True)
    assert engine.observe("Timing pattern", "timing", Outcome(prediction_id="one", actual_result="revised"), True).observation_count == 1
    for i in range(2, 6): lesson = engine.observe("Timing pattern", "timing", Outcome(prediction_id=str(i), actual_result="x"), True)
    assert lesson.status == LessonStatus.VALIDATED
    assert store._execute("SELECT COUNT(*) FROM audit_events WHERE event='lesson_transition'").fetchone()[0] >= 2


def test_no_forced_prediction_and_disagreement_preserved():
    findings = (SpecialistFinding(specialist="Technical", stance="BULLISH", data_quality="AVAILABLE"), SpecialistFinding(specialist="Macro", stance="HEADWIND", data_quality="PARTIAL"))
    result = synthesize("run", "q", "ABC", findings, (), ())
    assert result.predictions == []
    assert result.disagreements == ["Macro: HEADWIND"]


def test_risk_veto_and_validated_lesson_downgrade():
    bars = tuple(bar(i, 90, 100 + i, 95 + i // 2) for i in range(20))
    findings = tuple(SpecialistFinding(specialist=name, stance="BULLISH" if name == "Technical" else "POSITIVE", data_quality="AVAILABLE", references=[f"source:{name}"]) for name in ("Technical", "Company", "News"))
    result = synthesize("run", "q", "ABC", findings, bars, ())
    assert result.predictions and result.risk_verdict == "APPROVED"
    lesson = Lesson(statement="Stop placement frequently precedes target attainment", category="risk", status=LessonStatus.VALIDATED, observation_count=5, validation_reason="five")
    legacy = synthesize("run", "q", "ABC", findings, bars, (lesson,))
    assert legacy.predictions and legacy.predictions[0].quality_score == result.predictions[0].quality_score
    matching = lesson.model_copy(update={"applicability": LessonContext(direction=Direction.LONG, setup_type="BREAKOUT", catalyst_type="COMPANY")})
    downgraded = synthesize("run", "q", "ABC", findings, bars, (matching,))
    assert downgraded.predictions[0].quality_score < result.predictions[0].quality_score
    assert matching.lesson_id in downgraded.ranking_factors["applied_lessons"]
    risky = setup(tp1=Decimal(102))
    assert not challenge(risky, findings).approved


def test_schedule_handles_athens_dst_and_invalid_kind():
    assert due_key("DAILY_BRIEF", datetime(2026, 1, 5, 8, tzinfo=timezone.utc)) == "DAILY_BRIEF:2026-01-05"
    assert due_key("DAILY_BRIEF", datetime(2026, 7, 5, 7, tzinfo=timezone.utc)) == "DAILY_BRIEF:2026-07-05"
    assert due_key("POST_MORTEM", datetime(2026, 1, 5, 21, tzinfo=timezone.utc)) == "POST_MORTEM:2026-01-05"
    assert due_key("DAILY_BRIEF", datetime(2026, 1, 5, 9, tzinfo=timezone.utc)) is None
    with pytest.raises(ValueError): due_key("ON_DEMAND")


def test_schedule_claim_deduplicates(store):
    assert store.claim_schedule("DAILY_BRIEF:2026-01-05")
    assert not store.claim_schedule("DAILY_BRIEF:2026-01-05")
    store.complete_schedule("DAILY_BRIEF:2026-01-05", "run-1")
    assert not store.claim_schedule("DAILY_BRIEF:2026-01-05")
