"""Evidence-led, symmetric setup synthesis and adversarial risk review."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .learning import lesson_applies
from .market_data import Bar
from .models import AnalysisResult, Direction, Lesson, LessonContext, Prediction, SpecialistFinding


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reasons: tuple[str, ...]


_SUPPORT = {
    Direction.LONG: {"BULLISH", "POSITIVE", "STRENGTH", "RISK_ON", "TAILWIND"},
    Direction.SHORT: {"BEARISH", "NEGATIVE", "WEAKNESS", "RISK_OFF", "HEADWIND"},
}


def challenge(prediction: Prediction, findings: tuple[SpecialistFinding, ...]) -> RiskVerdict:
    reasons = []
    if prediction.entry_reference_level is None or prediction.stop_level is None or prediction.tp1 is None:
        reasons.append("Entry, stop and first target must be structured")
    else:
        sign = Decimal(1) if prediction.direction == Direction.LONG else Decimal(-1)
        reward = (prediction.tp1 - prediction.entry_reference_level) * sign
        risk = (prediction.entry_reference_level - prediction.stop_level) * sign
        if risk <= 0 or reward / risk < Decimal("1.5"):
            reasons.append(f"{prediction.direction.value} risk/reward below 1.5")
    if any(f.specialist == "Volatility" and f.stance == "ELEVATED" for f in findings):
        reasons.append("Elevated range regime makes proposed stop less reliable")
    contrary_regime = "RISK_OFF" if prediction.direction == Direction.LONG else "RISK_ON"
    if any(f.specialist == "MarketRegime" and f.stance == contrary_regime for f in findings):
        reasons.append(f"{prediction.direction.value} thesis conflicts with broad {contrary_regime.lower()} regime")
    opposition = _SUPPORT[Direction.SHORT if prediction.direction == Direction.LONG else Direction.LONG]
    dissent = [f.specialist for f in findings if f.specialist != "MarketRegime" and f.stance in opposition and f.data_quality != "UNAVAILABLE"]
    if dissent:
        reasons.append("Dissent: " + ", ".join(dissent))
    return RiskVerdict(not reasons, tuple(reasons))


def synthesize(run_id: str, question: str, subject: str, findings: tuple[SpecialistFinding, ...], bars: tuple[Bar, ...], lessons: tuple[Lesson, ...], workflow_kind: str = "ON_DEMAND") -> AnalysisResult:
    available = [f for f in findings if f.data_quality != "UNAVAILABLE"]
    sides = {direction: [f for f in available if f.stance in stances] for direction, stances in _SUPPORT.items()}
    bullish, bearish = sides[Direction.LONG], sides[Direction.SHORT]
    disagreement = [f"{f.specialist}: {f.stance}" for f in (bearish if bullish else bullish if bearish else ())]
    factors = {"available_specialists": str(len(available)), "supporting_long": ", ".join(f.specialist for f in bullish), "supporting_short": ", ".join(f.specialist for f in bearish), "expected_value": "UNAVAILABLE: no calibrated outcome probability"}
    decision = "INSUFFICIENT_EVIDENCE" if len(available) < 2 else "WATCH" if bullish or bearish else "NO_ACTION"
    risks = [risk for f in findings for risk in f.risks]
    predictions: list[Prediction] = []
    risk_verdict = "NO_SETUP"
    for direction in (Direction.LONG, Direction.SHORT):
        supporting = sides[direction]
        opposing = sides[Direction.SHORT if direction == Direction.LONG else Direction.LONG]
        names = {f.specialist for f in supporting}
        technical = any(f.specialist == "Technical" and f.references and f.stance == ("BULLISH" if direction == Direction.LONG else "BEARISH") for f in supporting)
        catalyst = any(f.specialist in ("Company", "News") and f.references for f in supporting)
        if not (technical and catalyst and len(supporting) >= 3 and not opposing and len(bars) >= 20):
            continue
        entry = bars[-1].high if direction == Direction.LONG else bars[-1].low
        stop = min(b.low for b in bars[-20:]) if direction == Direction.LONG else max(b.high for b in bars[-20:])
        sign = Decimal(1) if direction == Direction.LONG else Decimal(-1)
        risk = (entry - stop) * sign
        if risk <= 0 or entry + sign * 3 * risk <= 0:
            continue
        regime = next((f.stance for f in available if f.specialist == "MarketRegime"), None)
        volatility_finding = next((f for f in available if f.specialist == "Volatility"), None)
        volatility = volatility_finding.stance if volatility_finding else None
        mean_range = volatility_finding.metrics.get("mean_range_20") if volatility_finding else None
        try: mean_range = Decimal(str(mean_range)) if mean_range is not None else None
        except ValueError: mean_range = None
        tight_stop = volatility == "ELEVATED" and (risk < mean_range if mean_range and mean_range > 0 else risk / entry < Decimal("0.03"))
        catalyst_type = "COMPANY" if any(f.specialist == "Company" for f in supporting) else "NEWS"
        context = LessonContext(workflow=workflow_kind, direction=direction, setup_type="BREAKOUT", symbol=subject, market_regime=regime, volatility_regime=volatility, catalyst_type=catalyst_type, analyst_tags=tuple(sorted(names)), horizon_tag="5d", risk_pattern="TIGHT_STOP" if tight_stop else None)
        applied = [(lesson, score, reason) for lesson in lessons if (match := lesson_applies(lesson, context))[0] > 0 for score, reason in [match]]
        penalty = max((min(.08, .03 + .02 * score) for _, score, _ in applied), default=0.0)
        components = {"trend": .25, "catalyst": .20, "regime_alignment": .20 if "MarketRegime" in names else 0, "source_references": .15, "structured_risk": .20, "validated_lesson_penalty": -penalty}
        quality = max(.0, min(1.0, sum(components.values())))
        factors.update({f"quality_{key}": str(value) for key, value in components.items()})
        factors["applied_lessons"] = "; ".join(f"{l.lesson_id} ({score}: {reason})" for l, score, reason in applied)
        prediction = Prediction(subject=subject, instrument=subject, direction=direction, thesis=f"Independent trend and catalyst evidence support {'upside' if direction == Direction.LONG else 'downside'}", horizon="5d", confidence=.65, catalyst=f"Sourced {catalyst_type.lower()} support", evidence_references=[r for f in supporting for r in f.references], supporting_specialists=[f.specialist for f in supporting], entry_condition=f"Price {'above' if direction == Direction.LONG else 'below'} {entry}", trigger_operator="ABOVE" if direction == Direction.LONG else "BELOW", entry_reference_level=entry, stop_level=stop, stop_invalidation=str(stop), tp1=entry + sign * 2 * risk, tp2=entry + sign * 3 * risk, risk_reward=Decimal(2), quality_score=quality, methodology_tags=["trend-catalyst", "BREAKOUT"], created_from_run=run_id)
        verdict = challenge(prediction, findings)
        risk_verdict = "APPROVED" if verdict.approved and quality >= .75 else "; ".join(verdict.reasons) or "Applicable lessons downgrade setup quality below threshold"
        if verdict.approved and quality >= .75:
            predictions.append(prediction)
            decision = "STRONG_OPPORTUNITY"
        else:
            risks.extend(verdict.reasons or ("Applicable lessons downgraded setup quality",))
    return AnalysisResult(run_id=run_id, question=question, bottom_line=f"{decision}: {subject}", facts=[fact for f in findings for fact in f.facts], inferences=[inf for f in findings for inf in f.inferences], specialist_findings=list(findings), disagreements=disagreement, risks=risks, predictions=predictions, learning_context=[l.statement for l in lessons], decision=decision, ranking_factors=factors, risk_verdict=risk_verdict, references=[r for f in findings for r in f.references])
