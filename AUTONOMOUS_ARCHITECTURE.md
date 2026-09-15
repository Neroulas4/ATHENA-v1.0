# ATHENA Autonomous Architecture — v0.10

## Trigger model

The two existing ChatGPT scheduled commands remain the authoritative triggers:

- 10:00 Europe/Athens — `DAILY_BRIEF`
- 23:00 Europe/Athens — `POST_MORTEM`

They carry ATHENA's operating policy directly in their prompts. This means the
scheduled commands already run with the ATHENA methodology, even before a
separate cloud deployment is connected.

## Runtime model

When hosted, each trigger maps to:

`scheduled trigger -> bridge -> orchestrator -> specialist agents -> synthesis -> persistent store`

The bridge accepts only the two known workflow kinds.

## Safety / epistemic rules

- Immutable historical predictions.
- No rewriting prior calls.
- Trigger/entry activation is separate from directional thesis.
- Thesis correctness is separate from execution quality.
- Lessons progress from observation to validation.
- No forced trade count.
- No fabricated evidence.
- Partial failures are recorded rather than hidden.

## Remaining infrastructure dependency

The code is deployment-ready but a hosted HTTP endpoint and scheduler-to-endpoint
connection are external infrastructure. ChatGPT scheduled tasks cannot execute an
arbitrary ZIP file inside this conversation. Once a hosted endpoint exists, the
bridge contract is already defined.
