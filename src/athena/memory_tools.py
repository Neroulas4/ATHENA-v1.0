"""Read-only institutional-memory interface for the Learning specialist."""
from __future__ import annotations
from .store import Store

class LearningMemory:
    def __init__(self, store: Store) -> None: self._store = store
    def list_recent_predictions(self, limit: int = 50): return self._store.recent_predictions(limit)
    def get_prediction(self, prediction_id: str): return self._store.get_prediction(prediction_id)
    def list_outcomes(self, limit: int = 200): return self._store.list_outcomes(limit)
    def get_outcomes_for_prediction(self, prediction_id: str): return self._store.outcomes_for(prediction_id)
    def list_lessons(self): return self._store.list_lessons()
    def list_validated_lessons(self): return self._store.validated_lessons()
    def list_candidate_lessons(self): return self._store.candidate_lessons()
    def list_recent_runs(self, limit: int = 50): return self._store.recent_runs(limit)
    def query_historical_performance(self) -> dict[str, int]:
        outcomes = [o for p in self._store.recent_predictions(500) if (o := self._store.latest_outcome(p.prediction_id))]
        return {"outcomes": len(outcomes), "triggered": sum(o.trigger_activated is True for o in outcomes), "executed": sum(o.execution_occurred is True for o in outcomes), "thesis_correct": sum(o.thesis_correct is True for o in outcomes)}

    def performance_breakdown(self, min_samples: int = 5) -> dict[str, dict[str, dict[str, int | float | None]]]:
        """Latest persisted outcome per prediction; rates withheld below the denominator floor."""
        if min_samples < 1: raise ValueError("min_samples must be positive")
        grouped: dict[str, dict[str, list]] = {}
        for prediction in self._store.recent_predictions(500):
            outcome = self._store.latest_outcome(prediction.prediction_id)
            if outcome is None: continue
            findings = self._store.findings_for_run(prediction.created_from_run) if prediction.created_from_run else []
            own = {f.specialist: f for f in findings if f.subject == prediction.subject and f.data_quality != "UNAVAILABLE"}
            dimensions: dict[str, list[str]] = {"direction": [prediction.direction.value], "setup_tag": list(prediction.methodology_tags), "activation": ["ACTIVATED" if outcome.trigger_activated is True else "UNTRIGGERED" if outcome.trigger_activated is False else "UNKNOWN"], "execution": ["EXECUTED" if outcome.execution_occurred is True else "NOT_EXECUTED" if outcome.execution_occurred is False else "UNKNOWN"], "disagreement": ["NONE" if not prediction.dissenting_specialists else "MULTIPLE" if len(prediction.dissenting_specialists) > 1 else "ONE"], "analyst_combination": ["+".join(sorted(prediction.supporting_specialists))] if prediction.supporting_specialists else []}
            if prediction.quality_score is not None: dimensions["quality_bucket"] = ["HIGH" if prediction.quality_score >= .8 else "LOW"]
            dimensions["confidence_bucket"] = ["HIGH" if prediction.confidence >= .8 else "MEDIUM" if prediction.confidence >= .5 else "LOW"]
            for name, dimension in (("MarketRegime", "regime"), ("Volatility", "volatility_regime")):
                if name in own: dimensions[dimension] = [own[name].stance]
            catalyst = [name.upper() for name in ("Company", "News") if name in own and own[name].references and name in prediction.supporting_specialists]
            if catalyst: dimensions["catalyst_type"] = catalyst
            for dimension, values in dimensions.items():
                for value in set(values):
                    grouped.setdefault(dimension, {}).setdefault(value, []).append(outcome)
        output: dict[str, dict[str, dict[str, int | float | None]]] = {}
        for dimension, values in grouped.items():
            output[dimension] = {}
            for value, outcomes in values.items():
                thesis_known = [o for o in outcomes if o.thesis_correct is not None]
                timing_known = [o for o in outcomes if o.timing_correct is not None]
                output[dimension][value] = {"sample_size": len(outcomes), "activated": sum(o.trigger_activated is True for o in outcomes), "untriggered": sum(o.trigger_activated is False for o in outcomes), "executed": sum(o.execution_occurred is True for o in outcomes), "thesis_known": len(thesis_known), "thesis_correct": sum(o.thesis_correct is True for o in thesis_known), "thesis_accuracy": sum(o.thesis_correct is True for o in thesis_known) / len(thesis_known) if len(thesis_known) >= min_samples else None, "timing_known": len(timing_known), "timing_correct": sum(o.timing_correct is True for o in timing_known), "timing_accuracy": sum(o.timing_correct is True for o in timing_known) / len(timing_known) if len(timing_known) >= min_samples else None}
        return output
    def setup_performance_by_tag(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for prediction in self._store.recent_predictions(500):
            outcome = self._store.latest_outcome(prediction.prediction_id)
            if outcome is None: continue
            for tag in prediction.methodology_tags:
                stats = result.setdefault(tag, {"evaluated": 0, "triggered": 0, "thesis_correct": 0})
                stats["evaluated"] += 1
                stats["triggered"] += outcome.trigger_activated is True
                stats["thesis_correct"] += outcome.thesis_correct is True
        return result

    def analyst_performance_by_regime(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for prediction in self._store.recent_predictions(500):
            outcome = self._store.latest_outcome(prediction.prediction_id)
            if outcome is None or outcome.thesis_correct is None or not prediction.created_from_run: continue
            findings = self._store.findings_for_run(prediction.created_from_run)
            regime = next((finding.stance for finding in findings if finding.specialist == "MarketRegime" and finding.subject == prediction.subject), "UNKNOWN")
            for finding in findings:
                if finding.subject != prediction.subject or finding.specialist == "MarketRegime": continue
                key = f"{finding.specialist}:{regime}"
                stats = result.setdefault(key, {"evaluated": 0, "thesis_correct": 0})
                stats["evaluated"] += 1
                stats["thesis_correct"] += outcome.thesis_correct is True
        return result
