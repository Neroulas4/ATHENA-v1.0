"""Readable HTML and text views of persisted ATHENA reports."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape

from .models import AnalysisResult, Outcome
from .store import Store


@dataclass(frozen=True)
class EmailMessage:
    subject: str
    html: str
    text: str


def _value(value: object) -> str:
    return "UNKNOWN" if value is None else str(value)


def _render(title: str, subtitle: str, sections: list[tuple[str, list[str]]]) -> EmailMessage:
    text_lines = [title, subtitle, ""]
    html_sections = []
    for heading, lines in sections:
        text_lines.extend([heading, *(f"• {line}" for line in lines or ["No supported evidence available."]), ""])
        html_sections.append(f"<section><h2>{escape(heading)}</h2><ul>" + "".join(f"<li>{escape(line)}</li>" for line in lines or ["No supported evidence available."]) + "</ul></section>")
    html = ("<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
            "<body style='margin:0;background:#f5f7fa;color:#172337;font-family:Arial,sans-serif'>"
            "<main style='max-width:680px;margin:auto;padding:24px'><header style='background:#14233c;color:white;padding:24px;border-radius:8px'>"
            f"<h1 style='margin:0;font-size:24px'>{escape(title)}</h1><p style='margin-bottom:0'>{escape(subtitle)}</p></header>"
            "<div style='background:white;padding:22px;margin-top:14px;border-radius:8px;line-height:1.5'>"
            + "".join(html_sections) + "</div></main></body></html>")
    return EmailMessage(title, html, "\n".join(text_lines))


def render_daily_brief(result: AnalysisResult) -> EmailMessage:
    findings = result.specialist_findings
    def context(name: str) -> list[str]:
        return [f"{f.subject}: {f.stance}; {', '.join(f.facts[:2])}" for f in findings if f.specialist == name and f.data_quality != "UNAVAILABLE"]
    strengths = {f.subject: f.metrics.get("relative_strength_20") for f in findings if f.specialist == "Technical"}
    opportunities = [f"{p.subject} {p.direction.value} {p.setup_state.value}; quality {_value(p.quality_score)}, confidence {p.confidence}; {p.thesis}; relative strength {_value(strengths.get(p.subject))}; catalyst {_value(p.catalyst)}; risk {_value(result.risks[0] if result.risks else None)}" for p in result.predictions]
    watched = sorted({f.subject for f in findings if f.subject} - {p.subject for p in result.predictions})
    opportunities.extend(f"{symbol}: WATCH / NO SETUP; evidence has not passed the setup and Risk gates" for symbol in watched)
    setups = [f"{p.subject} {p.direction.value}: {p.entry_condition}; stop {_value(p.stop_level)}; invalidation {_value(p.stop_invalidation)}; TP1 {_value(p.tp1)}; TP2 {_value(p.tp2)}; R:R {_value(p.risk_reward)}; quality {_value(p.quality_score)}; Risk {result.risk_verdict}" for p in result.predictions]
    applied = result.ranking_factors.get("applied_lessons", "")
    sections = [
        ("Bottom Line", [result.bottom_line]),
        ("Market Regime, Volatility and Macro", context("MarketRegime") + context("Volatility") + context("Macro")),
        ("Top Developments", [fact for f in findings if f.specialist in {"News", "Company"} for fact in f.facts][:8] or result.facts[:8]),
        ("Watchlist / Ranked Opportunities", opportunities or ["No justified setup; continue watching available evidence."]),
        ("Best Setups", setups or ["No setup passed the evidence and Risk gates."]),
        ("What Changed Since Prior Brief", [line for line in result.inferences if "Prior prediction" in line]),
        ("ATHENA Learning Context", [applied] if applied else []),
        ("What Would Change the View", result.what_would_change_view or result.risks[:5]),
    ]
    return _render("ATHENA — Daily Market Brief", f"{result.timestamp.isoformat()} · Run {result.run_id}", sections)


def render_post_mortem(result: AnalysisResult, store: Store) -> EmailMessage:
    review = store.post_mortem_review(result.run_id) or {}
    evaluated = [item for outcome_id in review.get("outcome_ids", ()) if (item := store.get_outcome(outcome_id))]
    reviews = []
    right, wrong = [], []
    for item in evaluated:
        prediction = store.get_prediction(item.prediction_id)
        if prediction is None: continue
        line = f"{prediction.subject} {prediction.direction.value}: trigger {_value(item.trigger_activated)}; execution {_value(item.execution_occurred)}; thesis {_value(item.thesis_correct)}; timing {_value(item.timing_correct)}; stop {_value(item.stop_hit)}; TP1 {_value(item.tp1_hit)}; TP2 {_value(item.tp2_hit)}; MAE {_value(item.maximum_adverse_excursion)}; MFE {_value(item.maximum_favorable_excursion)}; state {item.setup_state.value}; {item.notes}"
        reviews.append(line)
        if item.thesis_correct is True: right.append(f"{prediction.subject}: directional thesis supported by evaluated bars")
        if item.thesis_correct is False: wrong.append(f"{prediction.subject}: directional thesis did not hold")
        if item.timing_correct is True: right.append(f"{prediction.subject}: target timing preceded stop")
        if item.timing_correct is False: wrong.append(f"{prediction.subject}: target timing failed")
    lessons = store.list_lessons()
    observed = [l for l in lessons if l.lesson_id in review.get("observed_lesson_ids", ())]
    sections = [
        ("Session Summary", [result.bottom_line, "UNTRIGGERED ≠ FAILED TRADE"]),
        ("Prediction Review", reviews[:30]),
        ("What ATHENA Got Right", right[:10]),
        ("What ATHENA Got Wrong", wrong[:10]),
        ("New Learning Observations", [f"{l.category}: {l.statement} ({l.observation_count} supporting predictions)" for l in observed[:10]]),
        ("Lesson States", [f"{change['from'] or 'NEW'} → {change['to']}: {change['lesson_id']}" for change in review.get("lesson_changes", ())] or [f"{l.status.value}: {l.statement}" for l in lessons[:10]]),
        ("Implications for Next Brief", [l.statement for l in lessons if l.status.value == "VALIDATED"][:5]),
    ]
    missed = [item for item in result.inferences if "missed opportunity" in item.lower() and result.references]
    if missed: sections.insert(4, ("Missed / Better Relative Opportunities", missed[:5]))
    return _render("ATHENA — US Market Post-Mortem", f"{result.timestamp.isoformat()} · Run {result.run_id}", sections)
