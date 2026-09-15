"""Bridge contract between ChatGPT scheduled workflows and ATHENA.

The scheduled prompts are the external triggers. A deployment can expose
POST /athena/run with kind=DAILY_BRIEF or POST /athena/run with
kind=POST_MORTEM and invoke these workflows.
"""
from .store import Store
from .orchestrator import Orchestrator

ALLOWED = {"DAILY_BRIEF", "POST_MORTEM"}

def execute(kind: str):
    if kind not in ALLOWED:
        raise ValueError(f"Unsupported workflow: {kind}")
    o = Orchestrator(Store())
    if kind == "DAILY_BRIEF":
        question = (
            "Execute ATHENA Daily Brief: compare with prior ATHENA state, "
            "research material changes, evaluate active hypotheses, rank "
            "high-quality setups by expected value, and preserve uncertainty."
        )
    else:
        question = (
            "Execute ATHENA US Market Post-Mortem: evaluate every prior "
            "hypothesis against actual outcomes, separate thesis/timing/"
            "trigger/execution, identify missed opportunities and propose "
            "evidence-based lessons without rewriting history."
        )
    return o.run(question, kind)
