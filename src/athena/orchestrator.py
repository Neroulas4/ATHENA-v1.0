"""Single coordination point: collect first, analyze independently, synthesize last."""
from __future__ import annotations

import os
import re
from typing import Callable

from .agents.deterministic import ANALYSTS, TechnicalInput
from .collectors import CollectorService
from .decision import synthesize
from .llm import enrich
from .market_data import configured_market_data_service
from .models import AnalysisResult, now_utc
from .store import Store


class Orchestrator:
    def __init__(self, store: Store, runner: Callable[[str, str, str], AnalysisResult] | None = None, collector: CollectorService | None = None) -> None:
        self.store = store
        self.runner = runner
        self.collector = collector or CollectorService(configured_market_data_service())

    def run(self, question: str, kind: str = "ON_DEMAND") -> AnalysisResult:
        run_id = self.store.start_run(kind, question)
        try:
            result = self.runner(run_id, question, kind) if self.runner else self._run_pipeline(run_id, question, kind)
            result = self._apply_risk_gate(result, question)
            saved_predictions = []
            for prediction in result.predictions:
                if prediction.created_from_run != run_id:
                    prediction = prediction.model_copy(update={"created_from_run": run_id})
                self.store.save_prediction(prediction)
                saved_predictions.append(prediction)
            if saved_predictions:
                result = result.model_copy(update={"predictions": saved_predictions})
            self.store.finish_run(run_id, result)
            self.store.audit("run_completed", {"run_id": run_id, "kind": kind, "prediction_ids": [p.prediction_id for p in result.predictions]})
            return result
        except Exception as exc:
            result = AnalysisResult(run_id=run_id, question=question, bottom_line="Analysis failed before a supported conclusion.", errors=[f"{type(exc).__name__}: {exc}"])
            self.store.finish_run(run_id, result, "FAILED")
            self.store.audit("run_failed", {"run_id": run_id, "kind": kind, "error": type(exc).__name__})
            return result

    def _run_pipeline(self, run_id: str, question: str, kind: str) -> AnalysisResult:
        if kind == "POST_MORTEM":
            outcomes = self.store.list_outcomes(200)
            return AnalysisResult(run_id=run_id, question=question, bottom_line=f"Post-mortem evaluated {len(outcomes)} stored outcome records", facts=[f"{o.prediction_id}: {o.outcome_status}, trigger={o.trigger_activated}, execution={o.execution_occurred}, thesis={o.thesis_correct}, timing={o.timing_correct}" for o in outcomes], learning_context=[l.statement for l in self.store.validated_lessons()], decision="NO_ACTION", risk_verdict="NOT_APPLICABLE")
        selected = set(self.select_specialists(question, kind)) & set(ANALYSTS)
        symbols = [part.strip().upper() for part in os.getenv("ATHENA_SYMBOLS", "SPY").split(",") if part.strip()] if kind == "DAILY_BRIEF" else [self._symbol(question)]
        self.collector.errors.clear()
        results = []
        for symbol in dict.fromkeys(symbols):
            inputs, evidence = self.collector.collect(symbol, selected, run_id)
            for item in evidence: self.store.save_evidence(item)
            # No output is provided to another primary analyst. Synthesis starts only
            # after every selected analyst has returned its finding.
            findings = tuple(ANALYSTS[name](inputs[name]) for name in sorted(selected))
            for finding in findings: self.store.save_finding(run_id, finding)
            technical_input = inputs.get("Technical")
            bars = technical_input.bars if isinstance(technical_input, TechnicalInput) else ()
            results.append(synthesize(run_id, question, symbol, findings, bars, tuple(self.store.validated_lessons()), kind))
        if not results:
            return AnalysisResult(run_id=run_id, question=question, bottom_line="No configured symbols", errors=["ATHENA_SYMBOLS is empty"])
        if len(results) == 1:
            result = results[0]
        else:
            result = AnalysisResult(run_id=run_id, question=question, bottom_line="; ".join(r.bottom_line for r in results), facts=[x for r in results for x in r.facts], inferences=[x for r in results for x in r.inferences], specialist_findings=[x for r in results for x in r.specialist_findings], disagreements=[x for r in results for x in r.disagreements], risks=[x for r in results for x in r.risks], predictions=sorted([x for r in results for x in r.predictions], key=lambda p: (p.quality_score or 0, p.expected_value or 0), reverse=True), references=[x for r in results for x in r.references], learning_context=[x for r in results for x in r.learning_context], decision="RANKED_OPPORTUNITIES" if any(r.predictions for r in results) else "NO_ACTION", risk_verdict="REVIEWED" if any(r.risk_verdict != "NO_SETUP" for r in results) else "NO_SETUP", ranking_factors={r.bottom_line: str(r.ranking_factors) for r in results})
        if self.collector.errors:
            result = result.model_copy(update={"errors": result.errors + list(self.collector.errors)})
        return enrich(result)

    @staticmethod
    def _symbol(question: str) -> str:
        match = re.search(r"\$([A-Z]{1,5})\b", question)
        if match: return match.group(1)
        configured = os.getenv("ATHENA_SYMBOLS", "SPY").split(",")
        return configured[0].strip().upper() or "SPY"

    @staticmethod
    def select_specialists(question: str, kind: str = "ON_DEMAND") -> list[str]:
        if kind in ("DAILY_BRIEF", "POST_MORTEM"):
            return sorted(ANALYSTS)
        text = question.lower()
        selected: set[str] = {"Risk"}
        if any(word in text for word in ("rate", "inflation", "gdp", "macro", "central bank", "fx")): selected.add("Macro")
        if any(word in text for word in ("earnings", "company", "revenue", "valuation", "guidance")): selected.add("Company")
        if any(word in text for word in ("price", "market", "trend", "momentum", "sector", "nasdaq", "stock", "$")): selected.update(("Technical", "Volatility", "MarketRegime"))
        if any(word in text for word in ("news", "today", "catalyst", "shock")): selected.add("News")
        if any(word in text for word in ("sentiment", "positioning", "narrative", "crowded")): selected.add("Sentiment")
        if any(word in text for word in ("lesson", "histor", "outcome", "post-mortem", "prediction")): selected.add("Learning")
        return sorted(selected)

    @staticmethod
    def _apply_risk_gate(result: AnalysisResult, question: str) -> AnalysisResult:
        high_impact = any(p.confidence >= .8 or (p.quality_score or 0) >= .8 for p in result.predictions)
        has_risk = result.risk_verdict == "APPROVED" or any(f.specialist.lower() == "risk" for f in result.specialist_findings)
        if high_impact and not has_risk:
            return result.model_copy(update={"predictions": [], "actionable_setups": [], "risks": result.risks + ["High-impact setup withheld: no Risk review"], "errors": result.errors + ["Risk gate withheld high-impact predictions"]})
        return result
