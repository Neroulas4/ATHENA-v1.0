"""Process entry point for a Railway cron/worker invocation."""
from __future__ import annotations

import argparse
import logging

from .bridge import execute
from .delivery import DeliveryService
from .schedule import due_key
from .store import Store
from .workflows import WORKFLOW_KINDS

logger = logging.getLogger(__name__)


def run_worker(kind: str, *, scheduled: bool = False, store: Store | None = None, delivery: DeliveryService | None = None, now=None, runner=None):
    key = due_key(kind, now) if scheduled else None
    if scheduled and key is None:
        return None, "SKIPPED: outside Athens schedule window"
    own_store = store is None
    store = store or Store()
    try:
        if key and not store.claim_schedule(key):
            run_id = store.scheduled_run_id(key)
            previous = store.get_run_result(run_id) if run_id else None
            if previous:
                (delivery or DeliveryService(store)).deliver(previous, kind)
            return previous, "SKIPPED: scheduled workflow already claimed"
        try:
            result = (runner or execute)(kind)
            if result.bottom_line.startswith("Analysis failed"):
                raise RuntimeError("Scheduled workflow failed")
            if key: store.complete_schedule(key, result.run_id)
        except Exception:
            if key: store.release_schedule(key)
            raise
        if key:
            try: (delivery or DeliveryService(store)).deliver(result, kind)
            except Exception:
                logger.exception("delivery_integration_failed", extra={"run_id": result.run_id, "kind": kind})
        logger.info("workflow_complete", extra={"run_id": result.run_id, "kind": kind, "errors": len(result.errors)})
        return result, "COMPLETED"
    finally:
        if own_store: store.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="athena-worker")
    parser.add_argument("kind", choices=sorted(WORKFLOW_KINDS - {"ON_DEMAND"}))
    parser.add_argument("--scheduled", action="store_true", help="Run only in the Athens wall-clock window, once per local date")
    args = parser.parse_args()
    result, status = run_worker(args.kind, scheduled=args.scheduled)
    print(result.model_dump_json() if status == "COMPLETED" and result else status)


if __name__ == "__main__":
    main()
