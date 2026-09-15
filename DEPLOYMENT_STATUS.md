# ATHENA Autonomous Readiness

## Completed in code
- Multi-agent Orchestrator.
- Macro / News / Company / Market / Sentiment / Risk / Learning specialists.
- Agents-as-Tools manager pattern.
- Web research tool.
- Persistent SQLite memory.
- Immutable prediction/outcome/lesson records.
- Daily Brief and Post-Mortem workflow contracts.
- Deployment-facing HTTP API.
- Health endpoint.
- Render deployment manifest with persistent disk.
- Environment variable contract for `OPENAI_API_KEY`.
- Existing ChatGPT scheduled tasks updated to the ATHENA workflow policy.

## Final external step
A hosted service must be deployed and the scheduled triggers must call its `/run`
endpoint. ChatGPT's scheduled-task system cannot execute an arbitrary local ZIP
inside the conversation.

Render was selected as the straightforward deployment target because its
integration can be managed from ChatGPT after the user connects it.

## Production acceptance checks
1. `/health` returns `status=ok`.
2. `POST /run {"kind":"DAILY_BRIEF"}` creates a completed ATHENA run.
3. `POST /run {"kind":"POST_MORTEM"}` creates a completed run.
4. SQLite database persists between restarts.
5. Failed specialist calls are recorded.
6. Historical predictions remain immutable.
7. Daily brief can retrieve prior state.
8. Post-mortem can evaluate prior predictions.
