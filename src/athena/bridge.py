"""Thin integration bridge; all workflow logic remains in workflows.py."""
from .orchestrator import Orchestrator
from .store import Store
from .workflows import Workflows


def execute(kind: str, question: str | None = None):
    store = Store()
    try:
        return Workflows(Orchestrator(store), store).dispatch(kind, question)
    finally:
        store.close()
