"""One user-access facade over canonical workflows and read-only memory."""
from __future__ import annotations

import re
import os
from typing import Any

from .memory_tools import LearningMemory
from .models import AnalysisResult, Direction, LessonContext, Prediction
from .orchestrator import Orchestrator
from .store import Store
from .workflows import Workflows

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


def redact_sensitive(value: Any) -> Any:
    """Keep configured credentials out of HTTP/MCP responses, including source errors."""
    secrets = [secret for name in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "FINNHUB_API_KEY", "FRED_API_KEY", "OPENAI_API_KEY", "RESEND_API_KEY", "ATHENA_API_TOKEN", "ATHENA_API_TOKEN_PREVIOUS") if (secret := os.getenv(name, "")) and len(secret) >= 4]
    if isinstance(value, str):
        for secret in secrets: value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict): return {key: redact_sensitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [redact_sensitive(item) for item in value]
    return value


def normalized_symbol(value: str) -> str:
    symbol = value.strip().upper().lstrip("$")
    if not _SYMBOL.fullmatch(symbol): raise ValueError("symbol must be a 1–10 character ticker")
    return symbol


def _finding(result: AnalysisResult, name: str) -> list[dict[str, Any]]:
    return [f.model_dump(mode="json") for f in result.specialist_findings if f.specialist == name]


def structured_analysis(result: AnalysisResult, symbol: str, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    prediction = next((p for p in result.predictions if p.subject == symbol), None)
    unavailable = sorted({f.specialist for f in result.specialist_findings if f.subject == symbol and f.data_quality == "UNAVAILABLE"})
    return {"run_id": result.run_id, "bottom_line": result.bottom_line, "subject": symbol, "timestamp": result.timestamp.isoformat(), "market_regime": _finding(result, "MarketRegime"), "technical": _finding(result, "Technical"), "company": _finding(result, "Company"), "news": _finding(result, "News"), "macro": _finding(result, "Macro"), "sentiment": _finding(result, "Sentiment"), "volatility": _finding(result, "Volatility"), "specialist_agreement": result.agreements, "specialist_disagreement": result.disagreements, "risk_review": {"verdict": result.risk_verdict, "risks": result.risks}, "relevant_history": history or [], "applied_lessons": result.ranking_factors.get("applied_lessons", ""), "confidence": prediction.confidence if prediction else result.confidence, "quality": prediction.quality_score if prediction else None, "setup_status": prediction.setup_state.value if prediction else "NO_SETUP", "setup": prediction.model_dump(mode="json") if prediction else None, "what_would_change_view": result.what_would_change_view, "evidence_references": result.references, "data_quality": "PARTIAL" if unavailable else "AVAILABLE" if result.specialist_findings else "UNAVAILABLE", "unavailable_sections": unavailable, "errors": result.errors}


class AccessService:
    def __init__(self, store: Store, workflows: Workflows | None = None) -> None:
        self.store = store
        self.workflows = workflows or Workflows(Orchestrator(store), store)

    def deep_analysis(self, symbol: str, question: str | None = None, horizon: str | None = None, depth: str | None = None) -> dict[str, Any]:
        symbol = normalized_symbol(symbol)
        if question and len(question) > 1000: raise ValueError("question exceeds 1000 characters")
        if horizon and len(horizon) > 30: raise ValueError("horizon exceeds 30 characters")
        if depth and depth not in {"standard", "deep"}: raise ValueError("depth must be standard or deep")
        prior = [p for p in self.store.recent_predictions(200) if p.subject == symbol][:10]
        prior_brief = self.store.latest_run_result("DAILY_BRIEF")
        prompt = f"Deep analysis of ${symbol}: company earnings, technical price trend, sourced news catalyst, macro, sentiment, volatility, market regime, and Risk."
        if question: prompt += " User question: " + question
        if horizon: prompt += " Requested horizon context: " + horizon
        result = self.workflows.dispatch("ON_DEMAND", prompt)
        history = [{"prediction_id": p.prediction_id, "timestamp": p.timestamp.isoformat(), "direction": p.direction.value, "thesis": p.thesis, "latest_outcome": (o.model_dump(mode="json") if (o := self.store.latest_outcome(p.prediction_id)) else None)} for p in prior]
        output = structured_analysis(result, symbol, history)
        if prior_brief:
            output["prior_daily_brief"] = {"run_id": prior_brief.run_id, "timestamp": prior_brief.timestamp.isoformat(), "bottom_line": prior_brief.bottom_line}
        return output

    def compare(self, symbols: list[str], question: str | None = None, horizon: str | None = None) -> dict[str, Any]:
        values = [normalized_symbol(s) for s in symbols]
        if not 2 <= len(values) <= 5 or len(set(values)) != len(values): raise ValueError("comparison requires 2–5 distinct symbols")
        analyses = [self.deep_analysis(symbol, question, horizon) for symbol in values]
        criteria = {}
        for item in analyses:
            criteria[item["subject"]] = {"company": [f["stance"] for f in item["company"]], "technical": [f["stance"] for f in item["technical"]], "news": [f["stance"] for f in item["news"]], "relative_strength": [f["metrics"].get("relative_strength_20") for f in item["technical"]], "volatility": [f["stance"] for f in item["volatility"]], "regime": [f["stance"] for f in item["market_regime"]], "sentiment": [f["stance"] for f in item["sentiment"]], "setup_quality": item["quality"], "unavailable_sections": item["unavailable_sections"]}
        return {"summary": "Independent ATHENA analyses compared using the same criteria; no winner is forced.", "symbols": values, "criteria": criteria, "analyses": analyses}

    def market_question(self, question: str, symbols: list[str] | None = None) -> dict[str, Any]:
        if not question.strip() or len(question) > 1000: raise ValueError("question must contain 1–1000 characters")
        if symbols:
            values = [normalized_symbol(s) for s in symbols]
            if len(values) > 5: raise ValueError("at most five symbols")
            if len(values) > 1: return self.compare(values, question)
            question = f"${values[0]} {question}"
        prior_brief = self.store.latest_run_result("DAILY_BRIEF")
        result = self.workflows.dispatch("ON_DEMAND", question)
        subject = result.predictions[0].subject if result.predictions else next((f.subject for f in result.specialist_findings if f.subject), "UNKNOWN")
        output = structured_analysis(result, subject)
        if prior_brief:
            previous = {(f.subject, f.specialist): f.stance for f in prior_brief.specialist_findings}
            changes = [f"{f.subject} {f.specialist}: {previous[(f.subject, f.specialist)]} → {f.stance}" for f in result.specialist_findings if (f.subject, f.specialist) in previous and previous[(f.subject, f.specialist)] != f.stance]
            output["daily_brief_comparison"] = {"prior_run_id": prior_brief.run_id, "prior_bottom_line": prior_brief.bottom_line, "changed_stances": changes, "comparable_findings": sum((f.subject, f.specialist) in previous for f in result.specialist_findings)}
        return output

    def latest_report(self, kind: str) -> dict[str, Any]:
        if kind not in {"DAILY_BRIEF", "POST_MORTEM"}: raise ValueError("unsupported report kind")
        result = self.store.latest_run_result(kind)
        return {"available": result is not None, "kind": kind, "report": result.model_dump(mode="json") if result else None}

    def prediction(self, prediction_id: str) -> dict[str, Any]:
        if len(prediction_id) > 100: raise ValueError("prediction_id exceeds 100 characters")
        p = self.store.get_prediction(prediction_id)
        if p is None: raise ValueError("prediction not found")
        latest = self.store.latest_outcome(prediction_id)
        relevant = [l for l in self.store.list_lessons() if (l.applicability.direction in (None, p.direction)) and (l.applicability.symbol in (None, p.subject))]
        return {"prediction": p.model_dump(mode="json"), "latest_outcome": latest.model_dump(mode="json") if latest else None, "relevant_lessons": [l.model_dump(mode="json") for l in relevant[:20]]}

    def explain_prediction(self, prediction_id: str, question: str | None = None) -> dict[str, Any]:
        if question and len(question) > 1000: raise ValueError("question exceeds 1000 characters")
        record = self.prediction(prediction_id)
        p = self.store.get_prediction(prediction_id)
        original = self.store.get_run_result(p.created_from_run) if p and p.created_from_run else None
        findings = [f.model_dump(mode="json") for f in self.store.findings_for_run(p.created_from_run) if f.subject == p.subject] if p and p.created_from_run else []
        old_stances = {f["specialist"]: f["stance"] for f in findings}
        current_stances: dict[str, str] = {}
        for run in self.store.recent_runs(50):
            if run["run_id"] == p.created_from_run or run["started_at"] <= p.timestamp.isoformat(): continue
            for finding in self.store.findings_for_run(run["run_id"]):
                if finding.subject == p.subject: current_stances.setdefault(finding.specialist, finding.stance)
        changes = [f"{name}: {old} → {current_stances[name]}" for name, old in old_stances.items() if name in current_stances and current_stances[name] != old]
        return {**record, "summary": f"Original {p.direction.value} thesis: {p.thesis}", "original_evidence": findings, "analyst_agreement": original.agreements if original else [], "analyst_disagreement": original.disagreements if original else [], "risk_review": original.risk_verdict if original else "UNAVAILABLE", "what_changed": {"latest_outcome": record["latest_outcome"], "later_analyst_stance_changes": changes}, "question": question}

    def active_setups(self) -> dict[str, Any]:
        items = []
        for p in self.store.recent_predictions(200):
            latest = self.store.latest_outcome(p.prediction_id)
            state = latest.setup_state.value if latest else p.setup_state.value
            if state in {"ACTIVE", "NOT_ACTIVE_YET"}:
                items.append({"prediction_id": p.prediction_id, "subject": p.subject, "direction": p.direction.value, "state": state, "prediction_timestamp": p.timestamp.isoformat(), "outcome_evaluated_at": latest.evaluated_at.isoformat() if latest else None, "entry_condition": p.entry_condition, "quality": p.quality_score})
        return {"summary": f"{len(items)} persisted open setups; state reflects latest stored evaluation", "setups": items}

    def learning_summary(self, category: str | None = None, symbol: str | None = None, regime: str | None = None, direction: str | None = None) -> dict[str, Any]:
        if symbol: symbol = normalized_symbol(symbol)
        if direction and direction not in {"LONG", "SHORT"}: raise ValueError("direction must be LONG or SHORT")
        lessons = [l for l in self.store.list_lessons() if (not category or l.category == category) and (not symbol or l.applicability.symbol in (None, symbol)) and (not regime or l.applicability.market_regime == regime) and (not direction or l.applicability.direction in (None, Direction(direction)))]
        return {"summary": f"{len(lessons)} stored lessons match the filters", "lessons": [l.model_dump(mode="json") for l in lessons], "performance": LearningMemory(self.store).performance_breakdown()}
