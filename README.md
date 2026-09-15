# ATHENA v0.10 — Hosted Agent Runtime

This release makes the multi-agent architecture concrete:
- Manager-style orchestration using Agents-as-Tools.
- Seven specialist agents with web research.
- Persistent SQLite runs/predictions/outcomes/lessons/audit.
- Separate workflow concepts for Daily Brief and US Post-Mortem.
- OpenAI Agents SDK tracing metadata and workflow names.
- Explicit failure handling.

## Important
This is **hosted-ready architecture**, not a deployed cloud service.
A production scheduler/API and secret management still need to be connected to the runtime.

## Commands
athena ask "Why is the Nasdaq falling today?"
athena ask "Analyze NVDA for the next 1-3 days"
athena daily
athena postmortem
athena status

## Next milestone: v1.0
A dashboard/API that exposes continuity:
"GOOD MORNING, GEORGE — 3 major changes since yesterday..."
with evidence, confidence changes, prediction history, post-mortems and lessons.


v0.10 adds the deployment-facing bridge contract for the two existing scheduled workflows.

## Hosted deployment

This package contains `render.yaml` and a FastAPI runtime. Deploy the service,
set `OPENAI_API_KEY`, verify `/health`, then connect the scheduled triggers to
the `/run` endpoint. The runtime is designed to be hosted; it is not dependent
on a developer laptop.
