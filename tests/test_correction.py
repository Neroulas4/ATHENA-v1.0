from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from athena.decision import challenge, synthesize
from athena.learning import LessonEngine, lesson_applies
from athena.market_data import Bar, FakeMarketDataProvider
from athena.memory_tools import LearningMemory
from athena.models import Direction, Lesson, LessonContext, LessonStatus, Outcome, Prediction, SpecialistFinding
from athena.observations import generate_observations
from athena.store import Store
from athena.orchestrator import Orchestrator
from athena.workflows import Workflows

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bars(falling=False):
    return tuple(Bar(T0 + timedelta(days=i), 100 - i if falling else 100 + i, 101 - i if falling else 101 + i, 99 - i if falling else 99 + i, 100 - i if falling else 100 + i) for i in range(20))


def findings(direction=Direction.LONG, opposition=False):
    trend = "BULLISH" if direction == Direction.LONG else "BEARISH"
    news = "POSITIVE" if direction == Direction.LONG else "NEGATIVE"
    values = [SpecialistFinding(specialist="Technical", subject="ABC", stance=trend, data_quality="AVAILABLE", references=["technical:1"]), SpecialistFinding(specialist="News", subject="ABC", stance=news, data_quality="AVAILABLE", references=["news:1"]), SpecialistFinding(specialist="Sentiment", subject="ABC", stance=news, data_quality="AVAILABLE")]
    if opposition: values.append(SpecialistFinding(specialist="Company", subject="ABC", stance="STRENGTH" if direction == Direction.SHORT else "WEAKNESS", data_quality="AVAILABLE", references=["company:1"]))
    return tuple(values)


def prediction(direction=Direction.LONG, **changes):
    values = dict(prediction_id="p", timestamp=T0, subject="ABC", instrument="ABC", direction=direction, thesis="test", horizon="5d", confidence=.6, trigger_operator="ABOVE" if direction == Direction.LONG else "BELOW", entry_reference_level=Decimal(100), stop_level=Decimal(95 if direction == Direction.LONG else 105), tp1=Decimal(110 if direction == Direction.LONG else 90), tp2=Decimal(115 if direction == Direction.LONG else 85), methodology_tags=("BREAKOUT",), supporting_specialists=("Technical", "News"), quality_score=.78)
    values.update(changes)
    return Prediction(**values)


def outcome(pid="p", **changes):
    values = dict(prediction_id=pid, actual_result="EVALUATED", outcome_status="COMPLETED", data_quality="AVAILABLE", trigger_activated=True, thesis_correct=False, timing_correct=False, stop_hit=True, tp1_hit=False)
    values.update(changes)
    return Outcome(**values)


def test_short_synthesis_is_symmetric_and_opposition_blocks():
    short = synthesize("run", "q", "ABC", findings(Direction.SHORT), bars(True), ())
    assert len(short.predictions) == 1
    p = short.predictions[0]
    assert p.direction == Direction.SHORT and p.trigger_operator == "BELOW"
    assert p.tp2 < p.tp1 < p.entry_reference_level < p.stop_level
    assert p.risk_reward == 2 and short.risk_verdict == "APPROVED"
    assert not synthesize("run", "q", "ABC", findings(Direction.SHORT, True), bars(True), ()).predictions
    assert not synthesize("run", "q", "ABC", findings(Direction.SHORT)[:2], bars(True), ()).predictions
    assert synthesize("run", "q", "ABC", findings(Direction.LONG), bars(), ()).predictions[0].direction == Direction.LONG
    assert not challenge(p.model_copy(update={"tp1": p.entry_reference_level - Decimal(1)}), findings(Direction.SHORT)).approved


def test_context_matching_and_legacy_defaults():
    base = synthesize("run", "q", "ABC", findings(), bars(), ())
    matched = Lesson(statement="scope", status=LessonStatus.VALIDATED, observation_count=5, applicability=LessonContext(direction=Direction.LONG, setup_type="BREAKOUT", catalyst_type="NEWS"))
    unrelated = matched.model_copy(update={"lesson_id": "unrelated", "applicability": LessonContext(direction=Direction.SHORT, setup_type="BREAKOUT")})
    legacy = Lesson.model_validate({"statement": "old", "status": "VALIDATED", "observation_count": 5})
    assert lesson_applies(matched, LessonContext(direction=Direction.LONG, setup_type="BREAKOUT", catalyst_type="NEWS"))[0] > 0
    assert lesson_applies(unrelated, LessonContext(direction=Direction.LONG, setup_type="BREAKOUT"))[0] == 0
    assert lesson_applies(legacy, LessonContext(direction=Direction.LONG, setup_type="BREAKOUT"))[0] == 0
    affected = synthesize("run", "q", "ABC", findings(), bars(), (matched, unrelated, legacy))
    assert affected.predictions[0].quality_score < base.predictions[0].quality_score
    same_scope = tuple(matched.model_copy(update={"lesson_id": f"match-{i}"}) for i in range(5))
    assert synthesize("run", "q", "ABC", findings(), bars(), same_scope).predictions[0].quality_score == affected.predictions[0].quality_score
    assert synthesize("run", "q", "ABC", findings(), bars(), (unrelated, legacy, unrelated)).predictions[0].quality_score == base.predictions[0].quality_score


def test_observation_evidence_and_stable_grouping(tmp_path: Path):
    p = prediction()
    o = outcome(maximum_adverse_excursion=Decimal("-.06"))
    historical = [Bar(T0 + timedelta(days=i), 100-i, 101-i, 99-i, 100-i) for i in range(3)]
    spy = [Bar(T0 + timedelta(days=i), 100+i, 101+i, 99+i, 100+i) for i in range(3)]
    original = (SpecialistFinding(specialist="Volatility", subject="ABC", stance="ELEVATED", data_quality="AVAILABLE", metrics={"mean_range_20": Decimal(10)}), SpecialistFinding(specialist="MarketRegime", subject="ABC", stance="RISK_OFF", data_quality="AVAILABLE"), SpecialistFinding(specialist="Company", subject="ABC", stance="WEAKNESS", data_quality="AVAILABLE"), SpecialistFinding(specialist="Technical", subject="ABC", stance="BULLISH", data_quality="AVAILABLE"))
    observed = generate_observations(p, o, original, FakeMarketDataProvider({"ABC": historical, "SPY": spy}))
    patterns = {x.pattern for x in observed}
    assert {"LONG_RELATIVE_UNDERPERFORMANCE", "TIGHT_STOP_HIGH_VOL", "REGIME_CONFLICT_FAILURE", "DISSENT_FAILURE", "TECHNICAL_COMPANY_CONFLICT"} <= patterns
    assert "LONG_RELATIVE_UNDERPERFORMANCE" not in {x.pattern for x in generate_observations(p, o, original, FakeMarketDataProvider({"ABC": historical}))}
    store = Store(str(tmp_path / "test.db"))
    engine = LessonEngine(store)
    item = next(x for x in observed if x.pattern == "TIGHT_STOP_HIGH_VOL")
    for i in range(5):
        current = outcome(pid=f"p{i}")
        engine.observe(item.statement if i == 0 else "Equivalent wording", item.category, current, True, context=item.context, pattern=item.pattern)
    lessons = store.list_lessons()
    assert len(lessons) == 1 and lessons[0].status == LessonStatus.VALIDATED and lessons[0].observation_count == 5
    store.close()


def test_performance_breakdown_uses_latest_outcomes_and_denominators(tmp_path: Path):
    store = Store(str(tmp_path / "test.db"))
    for i in range(7):
        direction = Direction.LONG if i < 6 else Direction.SHORT
        p = prediction(direction, prediction_id=f"p{i}")
        store.save_prediction(p)
        o = outcome(pid=p.prediction_id, trigger_activated=False if i == 0 else True, execution_occurred=False if i == 0 else None, thesis_correct=None if i == 0 else i < 6, timing_correct=None if i == 0 else i < 4)
        store.save_outcome(o)
    summary = LearningMemory(store).performance_breakdown(min_samples=5)
    assert summary["direction"]["LONG"]["sample_size"] == 6
    assert summary["direction"]["LONG"]["thesis_known"] == 5
    assert summary["direction"]["LONG"]["thesis_accuracy"] == 1
    assert summary["direction"]["LONG"]["timing_accuracy"] == .6
    assert summary["direction"]["SHORT"]["sample_size"] == 1
    assert summary["direction"]["SHORT"]["thesis_accuracy"] is None
    assert summary["activation"]["UNTRIGGERED"]["sample_size"] == 1
    assert summary["activation"]["UNTRIGGERED"]["executed"] == 0
    assert summary["direction"]["SHORT"]["thesis_correct"] == 0
    store.close()


def test_postmortem_reevaluates_pending_without_rewriting_prediction(tmp_path: Path):
    store = Store(str(tmp_path / "post.db"))
    p = prediction()
    store.save_prediction(p)
    flow = Workflows(Orchestrator(store), store, FakeMarketDataProvider())
    flow.post_mortem()
    assert store.latest_outcome(p.prediction_id).outcome_status == "PENDING"
    market = [Bar(T0, 100, 101, 99, 100), Bar(T0 + timedelta(days=1), 112, 115, 110, 112), Bar(T0 + timedelta(days=5), 113, 116, 111, 114)]
    flow.provider = FakeMarketDataProvider({"ABC": market})
    flow.post_mortem()
    assert len(store.outcomes_for(p.prediction_id)) == 2
    assert store.latest_outcome(p.prediction_id).outcome_status == "COMPLETED"
    assert store.get_prediction(p.prediction_id) == p
    store.close()
