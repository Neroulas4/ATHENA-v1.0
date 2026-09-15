from .orchestrator import Orchestrator

class AutonomousWorkflows:
    def __init__(self, orchestrator: Orchestrator):
        self.o = orchestrator

    def daily_brief(self):
        return self.o.run(
            """Prepare ATHENA's Daily Market Brief.
Focus on what materially changed since the previous session, the most important macro/news/company/market developments,
which previous hypotheses appear strengthened or weakened, major risks, and what deserves attention today.
Do not force predictions when evidence is weak.""",
            "DAILY_BRIEF"
        )

    def postmortem(self):
        return self.o.run(
            """Run ATHENA's US-market post-mortem.
Review the session, compare relevant prior ATHENA hypotheses with what actually happened,
separate thesis correctness from timing, trigger activation and execution,
identify contradictions and candidate lessons. Do not call something learned unless it is supported by evaluated outcomes.""",
            "POST_MORTEM"
        )
