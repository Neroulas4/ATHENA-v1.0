"""Optional SDK narrative synthesis over completed, sourced findings only."""
from __future__ import annotations

import os

from pydantic import BaseModel, Field

from .models import AnalysisResult


class Narrative(BaseModel):
    bottom_line: str
    what_would_change_view: list[str] = Field(default_factory=list)


def enrich(result: AnalysisResult) -> AnalysisResult:
    if os.getenv("ATHENA_ENABLE_LLM") != "1" or not os.getenv("OPENAI_API_KEY"):
        return result
    try:
        from agents import Agent, Runner
        agent = Agent(name="ATHENA synthesis", instructions="Summarize only the supplied completed independent findings and cited evidence. Preserve disagreement and unknowns. Never add prices, citations, predictions, lessons or executed trades. Return a concise bottom line and conditions that would change it.", output_type=Narrative)
        response = Runner.run_sync(agent, result.model_dump_json(exclude={"bottom_line", "what_would_change_view"}))
        narrative = response.final_output
        if not isinstance(narrative, Narrative): narrative = Narrative.model_validate(narrative)
        return result.model_copy(update={"bottom_line": narrative.bottom_line, "what_would_change_view": narrative.what_would_change_view})
    except Exception as exc:
        return result.model_copy(update={"errors": result.errors + [f"Optional LLM synthesis unavailable: {type(exc).__name__}"]})
