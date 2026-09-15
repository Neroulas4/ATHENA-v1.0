import argparse
from .store import Store
from .orchestrator import Orchestrator
from .workflows import AutonomousWorkflows

def main():
    p = argparse.ArgumentParser(prog="athena")
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("ask"); a.add_argument("question")
    sub.add_parser("daily"); sub.add_parser("postmortem"); sub.add_parser("status")
    args = p.parse_args()

    store = Store()
    o = Orchestrator(store)
    if args.cmd == "ask":
        r = o.run(args.question)
        print(r.interpretations[0] if r.interpretations else r.errors)
    elif args.cmd == "daily":
        print(o.run("Prepare the Daily Market Brief as defined in ATHENA's workflow.", "DAILY_BRIEF").interpretations[0])
    elif args.cmd == "postmortem":
        print(o.run("Run the US-market Post-Mortem as defined in ATHENA's workflow.", "POST_MORTEM").interpretations[0])
    elif args.cmd == "status":
        print(f"Stored predictions: {len(store.recent_predictions())}")
    else:
        p.print_help()

if __name__ == "__main__":
    main()
