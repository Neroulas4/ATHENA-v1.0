"""Command-line entry point."""
import argparse
from .bridge import execute
from .store import Store
from .orchestrator import Orchestrator
from .workflows import Workflows

def main() -> None:
    parser = argparse.ArgumentParser(prog="athena", description="ATHENA market intelligence runtime")
    commands = parser.add_subparsers(dest="command")
    ask = commands.add_parser("ask"); ask.add_argument("question")
    commands.add_parser("daily"); commands.add_parser("postmortem"); commands.add_parser("status")
    evaluate = commands.add_parser("evaluate"); evaluate.add_argument("prediction_id")
    cancel = commands.add_parser("cancel"); cancel.add_argument("prediction_id"); cancel.add_argument("reason")
    commands.add_parser("lessons")
    args = parser.parse_args()
    if args.command == "ask": print(execute("ON_DEMAND", args.question).model_dump_json(indent=2))
    elif args.command == "daily": print(execute("DAILY_BRIEF").model_dump_json(indent=2))
    elif args.command == "postmortem": print(execute("POST_MORTEM").model_dump_json(indent=2))
    elif args.command == "status": print(f"Stored predictions: {len(Store().recent_predictions())}")
    elif args.command == "evaluate":
        store = Store()
        outcome, changed = Workflows(Orchestrator(store), store).evaluate_prediction(args.prediction_id)
        print(outcome.model_dump_json(indent=2))
        store.close()
    elif args.command == "cancel":
        store = Store()
        outcome = Workflows(Orchestrator(store), store).cancel_prediction(args.prediction_id, args.reason)
        print(outcome.model_dump_json(indent=2))
        store.close()
    elif args.command == "lessons": print("\n".join(f"{l.status}: {l.statement}" for l in Store().list_lessons()))
    else: parser.print_help()

if __name__ == "__main__": main()
