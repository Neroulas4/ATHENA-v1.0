"""The single source of truth for ATHENA workflows."""
from __future__ import annotations

from .evaluation import build_outcome
from .learning import LessonEngine
from .market_data import MarketDataProvider, configured_market_data_service
from .memory_tools import LearningMemory
from .models import Outcome, SetupState
from .observations import generate_observations
from .orchestrator import Orchestrator
from .store import Store

WORKFLOW_KINDS = frozenset({"DAILY_BRIEF", "POST_MORTEM", "ON_DEMAND"})


class Workflows:
    def __init__(self, orchestrator: Orchestrator, store: Store, provider: MarketDataProvider | None = None) -> None:
        self.orchestrator, self.store = orchestrator, store
        self.provider = provider or configured_market_data_service()

    def dispatch(self, kind: str, question: str | None = None):
        if kind not in WORKFLOW_KINDS:
            raise ValueError(f"Unsupported workflow: {kind}")
        if kind == "DAILY_BRIEF": return self.daily_brief()
        if kind == "POST_MORTEM": return self.post_mortem()
        if not question: raise ValueError("ON_DEMAND requires a question")
        return self.orchestrator.run(question, kind)

    def daily_brief(self):
        previous = self.store.recent_predictions(50)
        lessons = self.store.validated_lessons()
        context = f"Previous hypotheses: {len(previous)}. Validated lessons: {[l.statement for l in lessons]}. Historical performance: {LearningMemory(self.store).query_historical_performance()}."
        result = self.orchestrator.run("Prepare the Daily Brief. Identify material changes since the previous session; compare prior hypotheses; use appropriate specialists; separate facts and inferences; identify ACTIVE vs NOT ACTIVE YET setups; rank justified setups by expected value and quality; preserve uncertainty and relative strength/weakness. " + context, "DAILY_BRIEF")
        comparisons = []
        for prediction in previous:
            technical = next((finding for finding in result.specialist_findings if finding.specialist == "Technical" and finding.subject == (prediction.instrument or prediction.subject)), None)
            if technical is None or technical.data_quality == "UNAVAILABLE":
                state = "current evidence unavailable"
            elif prediction.direction.value == "LONG":
                state = "strengthening" if technical.stance == "BULLISH" else "weakening" if technical.stance == "BEARISH" else "mixed"
            elif prediction.direction.value == "SHORT":
                state = "strengthening" if technical.stance == "BEARISH" else "weakening" if technical.stance == "BULLISH" else "mixed"
            else:
                state = "directional comparison not applicable"
            comparisons.append(f"Prior prediction {prediction.prediction_id}: {state}; original thesis remains unchanged")
        if comparisons:
            result = result.model_copy(update={"inferences": result.inferences + comparisons})
            self.store.finish_run(result.run_id, result)
        return result

    def post_mortem(self):
        if not self.store.acquire_lock("POST_MORTEM"):
            raise ValueError("POST_MORTEM is already running")
        outcomes: list[Outcome] = []
        reviewed: list[Outcome] = []
        try:
            before = {lesson.lesson_id: lesson.status.value for lesson in self.store.list_lessons()}
            for prediction in self.store.eligible_predictions(200):
                outcome, changed = self.evaluate_prediction(prediction.prediction_id)
                reviewed.append(outcome)
                if changed: outcomes.append(outcome)
            observed_lessons = self._create_candidate_lessons(outcomes)
            report = self.orchestrator.run("Run the US Market Post-Mortem using stored outcomes. Separate thesis correctness, timing correctness, trigger activation and execution. Untriggered setups are not trades. Generate only evidence-based candidate lessons.", "POST_MORTEM")
            changes = [{"lesson_id": lesson.lesson_id, "from": before.get(lesson.lesson_id), "to": lesson.status.value} for lesson in self.store.list_lessons() if before.get(lesson.lesson_id) != lesson.status.value]
            self.store.audit("post_mortem_review", {"run_id": report.run_id, "outcome_ids": [item.outcome_id for item in reviewed], "observed_lesson_ids": observed_lessons, "lesson_changes": changes})
            return report
        finally:
            self.store.release_lock("POST_MORTEM")

    def _create_candidate_lessons(self, outcomes: list[Outcome]) -> list[str]:
        engine = LessonEngine(self.store)
        observed: set[str] = set()
        for outcome in outcomes:
            prediction = self.store.get_prediction(outcome.prediction_id)
            if prediction is None: continue
            findings = tuple(self.store.findings_for_run(prediction.created_from_run)) if prediction.created_from_run else ()
            for observation in generate_observations(prediction, outcome, findings, self.provider):
                lesson = engine.observe(observation.statement, observation.category, outcome, observation.supports, context=observation.context, pattern=observation.pattern)
                observed.add(lesson.lesson_id)
        return sorted(observed)

    def evaluate_prediction(self, prediction_id: str) -> tuple[Outcome, bool]:
        prediction = self.store.get_prediction(prediction_id)
        if prediction is None: raise ValueError("Unknown prediction")
        outcome = build_outcome(prediction, self.provider)
        previous = self.store.latest_outcome(prediction_id)
        changed = previous is None or previous.model_dump(exclude={"outcome_id", "evaluated_at"}) != outcome.model_dump(exclude={"outcome_id", "evaluated_at"})
        if changed:
            self.store.save_outcome(outcome)
            self.store.audit("outcome_recorded", {"prediction_id": prediction_id, "outcome_id": outcome.outcome_id, "status": outcome.outcome_status})
        return outcome, changed

    def cancel_prediction(self, prediction_id: str, reason: str) -> Outcome:
        if not reason.strip(): raise ValueError("Cancellation reason is required")
        if self.store.get_prediction(prediction_id) is None: raise ValueError("Unknown prediction")
        previous = self.store.latest_outcome(prediction_id)
        if previous and previous.outcome_status in {"COMPLETED", "EXPIRED", "CANCELLED"}:
            raise ValueError("A final setup cannot be cancelled")
        outcome = Outcome(prediction_id=prediction_id, actual_result="CANCELLED", trigger_activated=previous.trigger_activated if previous else None, execution_occurred=previous.execution_occurred if previous else None, outcome_status="CANCELLED", setup_state=SetupState.CANCELLED, notes=reason)
        self.store.save_outcome(outcome)
        self.store.audit("setup_cancelled", {"prediction_id": prediction_id, "outcome_id": outcome.outcome_id, "reason": reason})
        return outcome
