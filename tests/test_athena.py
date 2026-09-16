from pathlib import Path
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from athena.evaluation import build_outcome, evaluate_trigger
from athena.learning import LessonEngine
from athena.market_data import Bar, FakeMarketDataProvider, UnavailableMarketDataProvider
from athena.memory_tools import LearningMemory
from athena.models import (ActivationState, AnalysisResult, Direction, ExecutionState,
                           Lesson, LessonStatus, Outcome, Prediction, SetupState, SpecialistFinding)
from athena.orchestrator import Orchestrator
from athena.server import app
from athena.store import Store
from athena.workflows import Workflows


@pytest.fixture
def store(tmp_path: Path):
    instance = Store(str(tmp_path / "athena.db"))
    yield instance
    instance.close()


def prediction(**changes):
    base = dict(prediction_id="pred-1", subject="NVDA", direction=Direction.LONG,
                thesis="Demand supports upside", horizon="5d", confidence=.7,
                entry_condition="Close above 100")
    base.update(changes)
    return Prediction(**base)


def test_prediction_validation_and_immutability(store):
    item = prediction()
    store.save_prediction(item)
    assert store.get_prediction(item.prediction_id) == item
    with pytest.raises(Exception): store.save_prediction(item)
    with pytest.raises(Exception): setattr(item, "thesis", "rewritten")


def test_outcome_creation_and_untriggered_is_not_trade(store):
    item = prediction(); store.save_prediction(item)
    outcome = Outcome(prediction_id=item.prediction_id, actual_result="Not triggered", trigger_activated=False, execution_occurred=False, outcome_status="UNTRIGGERED")
    store.save_outcome(outcome)
    assert store.outcomes_for(item.prediction_id)[0].outcome_status == "UNTRIGGERED"
    with pytest.raises(ValueError): Outcome(prediction_id=item.prediction_id, actual_result="bad", trigger_activated=False, execution_occurred=True)


def test_activated_evaluation_keeps_separate_scores(store):
    item = prediction(activation_state=ActivationState.ACTIVE, execution_state=ExecutionState.EXECUTED); store.save_prediction(item)
    outcome = Outcome(prediction_id=item.prediction_id, actual_result="Price rose", trigger_activated=True, execution_occurred=True, thesis_correct=True, timing_correct=False, realized_move=.02, maximum_adverse_excursion=-.01, maximum_favorable_excursion=.03, outcome_status="EVALUATED")
    store.save_outcome(outcome)
    saved = store.outcomes_for(item.prediction_id)[0]
    assert saved.thesis_correct is True and saved.timing_correct is False and saved.execution_occurred is True


def test_lesson_lifecycle_minimum_observations():
    with pytest.raises(ValueError): Lesson(statement="x", status=LessonStatus.VALIDATED, observation_count=4)
    lesson = Lesson(statement="x", status=LessonStatus.VALIDATED, observation_count=5, validation_reason="Five independent supporting outcomes")
    assert lesson.status == LessonStatus.VALIDATED


def test_persistence_round_trip_and_structured_output(store):
    item = prediction(); store.save_prediction(item)
    result = AnalysisResult(run_id="run", bottom_line="Wait", facts=["A"], inferences=["B"], predictions=[])
    store.finish_run(store.start_run("ON_DEMAND", "question"), result)
    assert store.recent_predictions() == [item]
    assert AnalysisResult.model_validate_json(result.model_dump_json()).bottom_line == "Wait"


def test_workflow_dispatch_and_postmortem(store):
    item = prediction(); store.save_prediction(item)
    orchestrator = Orchestrator(store, runner=lambda run_id, question, kind: AnalysisResult(run_id=run_id, bottom_line=kind))
    workflows = Workflows(orchestrator, store)
    assert workflows.dispatch("DAILY_BRIEF").bottom_line == "DAILY_BRIEF"
    assert workflows.dispatch("POST_MORTEM").bottom_line == "POST_MORTEM"
    assert store.outcomes_for(item.prediction_id)[0].outcome_status == "PENDING"
    with pytest.raises(ValueError): workflows.dispatch("UNKNOWN")


def test_api_health_and_invalid_workflow(monkeypatch):
    monkeypatch.setenv("ATHENA_API_TOKEN", "test-token")
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/run", json={"kind": "NOPE"}, headers={"Authorization": "Bearer test-token"}).status_code == 400
    assert client.post("/run", json={"kind": "ON_DEMAND"}, headers={"Authorization": "Bearer test-token"}).status_code == 422


def test_prediction_lifecycle_states_and_confidence():
    for state in SetupState:
        assert prediction(setup_state=state).setup_state == state
    with pytest.raises(ValueError): prediction(confidence=1.1)


def test_deterministic_long_short_triggers_and_targets():
    start = prediction().timestamp
    long = prediction(instrument="NVDA", entry_reference_level=100, stop_invalidation="95", tp1=105, tp2=110)
    bars = [Bar(start, 99, 101, 98, 100), Bar(start + timedelta(days=1), 100, 111, 96, 108)]
    outcome = build_outcome(long, FakeMarketDataProvider({"NVDA": bars}), start + timedelta(days=2))
    assert outcome.trigger_activated and outcome.tp1_hit and outcome.tp2_hit and outcome.stop_hit is False
    short = prediction(direction=Direction.SHORT, instrument="NVDA", entry_reference_level=100, stop_invalidation="105", tp1=95)
    short_outcome = build_outcome(short, FakeMarketDataProvider({"NVDA": [Bar(start, 101, 102, 99, 99), Bar(start + timedelta(days=1), 99, 101, 94, 95)]}), start + timedelta(days=2))
    assert short_outcome.trigger_activated and short_outcome.tp1_hit


def test_trigger_unavailable_and_untriggered_pending_behaviour():
    item = prediction(instrument="NVDA", entry_reference_level=100)
    assert build_outcome(item, UnavailableMarketDataProvider()).outcome_status == "PENDING"
    bars = [Bar(item.timestamp, 90, 99, 89, 95)]
    assert build_outcome(item, FakeMarketDataProvider({"NVDA": bars})).outcome_status == "PENDING"
    assert build_outcome(item, FakeMarketDataProvider({"NVDA": bars})).actual_result == "UNTRIGGERED"


def test_pending_outcome_can_be_reevaluated(store):
    item = prediction(instrument="NVDA", entry_reference_level=100); store.save_prediction(item)
    pending = build_outcome(item, UnavailableMarketDataProvider()); store.save_outcome(pending)
    assert item in store.eligible_predictions()
    complete = build_outcome(item, FakeMarketDataProvider({"NVDA": [Bar(item.timestamp, 99, 102, 98, 101)]}))
    store.save_outcome(complete)
    assert len(store.outcomes_for(item.prediction_id)) == 2


def test_lesson_grouping_validation_and_counter_evidence(store):
    engine = LessonEngine(store)
    for index in range(5):
        outcome = Outcome(outcome_id=f"o-{index}", prediction_id=f"p-{index}", actual_result="x")
        lesson = engine.observe("Keep trigger discipline", "execution", outcome, True)
    assert lesson.status == LessonStatus.VALIDATED and lesson.observation_count == 5
    for index in range(6): lesson = engine.observe("Keep trigger discipline", "execution", Outcome(outcome_id=f"c-{index}", prediction_id=f"counter-{index}", actual_result="x"), False)
    assert lesson.status == LessonStatus.REJECTED


def test_memory_tools_expose_real_saved_history(store):
    item = prediction(); store.save_prediction(item)
    store.save_outcome(Outcome(prediction_id=item.prediction_id, actual_result="x", trigger_activated=True, execution_occurred=True, thesis_correct=True))
    memory = LearningMemory(store)
    assert memory.get_prediction(item.prediction_id) == item
    assert memory.query_historical_performance()["thesis_correct"] == 1


def test_specialist_selection_and_risk_gate(store):
    assert {"Company", "Technical", "News", "Risk"}.issubset(Orchestrator.select_specialists("company earnings news market"))
    high = prediction(confidence=.9)
    orchestrator = Orchestrator(store, runner=lambda run_id, question, kind: AnalysisResult(run_id=run_id, bottom_line="x", predictions=[high]))
    assert not orchestrator.run("company").predictions
    reviewed_high = prediction(prediction_id="pred-reviewed", confidence=.9)
    reviewed = Orchestrator(store, runner=lambda run_id, question, kind: AnalysisResult(run_id=run_id, bottom_line="x", predictions=[reviewed_high], specialist_findings=[SpecialistFinding(specialist="Risk")]))
    assert reviewed.run("company").predictions


def test_workflow_idempotency_lock(store):
    orchestrator = Orchestrator(store, runner=lambda run_id, question, kind: AnalysisResult(run_id=run_id, bottom_line=kind))
    workflows = Workflows(orchestrator, store)
    assert store.acquire_lock("POST_MORTEM")
    with pytest.raises(ValueError): workflows.post_mortem()
    store.release_lock("POST_MORTEM")
