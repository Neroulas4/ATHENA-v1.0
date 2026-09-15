import uuid
from agents import Agent, Runner, RunConfig
from .agents import SPECIALIST_TOOLS
from .models import ResearchResult, Prediction
from .store import Store

INSTRUCTIONS = """
You are ATHENA, the manager of a multi-agent intelligence system.

You have specialist tools. Dynamically choose only the specialists needed for the question.
Typical mapping:
- macro context -> Macro
- breaking/current information -> News
- company-specific fundamentals -> Company
- price/market structure -> Market
- narrative/positioning -> Sentiment
- high-impact conclusions -> Risk
- historical ATHENA predictions/outcomes/methodology -> Learning

You must synthesize rather than blindly average specialists.
Preserve meaningful disagreements.
Separate FACTS from INTERPRETATIONS.
Do not invent evidence.
For predictions, state horizon, confidence, trigger and invalidation.
Always state what would change the view.
For insufficient evidence, lower confidence rather than filling gaps.
"""

class Orchestrator:
    def __init__(self, store: Store):
        self.store = store
        self.agent = Agent(
            name="ATHENA Orchestrator",
            instructions=INSTRUCTIONS,
            tools=SPECIALIST_TOOLS,
        )

    def run(self, question: str, kind="ON_DEMAND") -> ResearchResult:
        run_id = self.store.start_run(kind, question)
        try:
            result = Runner.run_sync(
                self.agent,
                question,
                run_config=RunConfig(workflow_name=f"ATHENA/{kind}", trace_metadata={"athena_run_id": run_id})
            )
            text = result.final_output
            rr = ResearchResult(run_id=run_id, question=question, interpretations=[text])
            self.store.finish_run(run_id, rr)
            self.store.audit("run_completed", {"run_id": run_id, "kind": kind})
            return rr
        except Exception as exc:
            rr = ResearchResult(run_id=run_id, question=question, errors=[str(exc)])
            self.store.finish_run(run_id, rr, "FAILED")
            self.store.audit("run_failed", {"run_id": run_id, "error": str(exc)})
            return rr
