# ATHENA

ATHENA is an evidence-led market-intelligence assistant. It collects source data, runs isolated deterministic specialists, synthesizes their findings, challenges potential setups, and learns from later market outcomes. It is **not** a live trading bot and never submits orders.

## Data flow

`MarketDataService / source collectors → immutable specialist inputs → independent analysts → synthesis → risk gate → immutable predictions → deterministic outcome evaluation → lessons → next brief`

Technical, Volatility, Market Regime, Macro, Company, News and Sentiment are primary analysts. Each receives only its own immutable DTO. They cannot call providers or one another. The orchestrator waits for all selected outputs before synthesis. Risk sees the proposed setup afterward and can veto it. The optional OpenAI Agents SDK summarizes completed findings only; it cannot create records or change deterministic rules.

All prices and financial metrics use `Decimal`. The collector boundary keeps source URL/reference, availability and historical/current distinctions. Alpaca IEX stock bars are supported with credentials. Finnhub provides company metrics, news and social sentiment; FRED provides macro series. An optional yfinance historical fallback is available. Source failures produce `UNAVAILABLE` findings and pending outcomes. No price, news, execution or historical memory is invented.

## Workflows

- **Daily Brief** scans `ATHENA_SYMBOLS`, runs independent analysts, consults validated lessons and prior predictions, preserves disagreement, and may produce zero setups. LONG and SHORT use the same corroborated trend/catalyst threshold, explicit entry/stop/targets and Risk approval. Ranking factors identify applicable lessons and their quality adjustment.
- **Post-Mortem** loads eligible predictions, obtains historical bars through `MarketDataService`, evaluates triggers, stops, targets and horizon in code, appends only changed outcomes, aggregates lesson observations, and persists a report. An untriggered setup is never a trade. Missing data or unknown free-text conditions remain `PENDING`.
- **ON_DEMAND** selects relevant specialists for the question. It can return `INSUFFICIENT_EVIDENCE` and does not require a setup.

Predictions are immutable snapshots. Outcomes are append-only and pending records can be reevaluated. Setup states are `NOT_ACTIVE_YET`, `ACTIVE`, `INVALIDATED`, `EXPIRED`, `COMPLETED`, `CANCELLED`. Trigger activation, execution evidence, thesis correctness and timing are separate fields. New lessons group by deterministic pattern and structured context; legacy lessons remain readable. Five independent supports with more support than counter-evidence are needed for validation; transitions are audited. Only validated lessons with matching, sufficiently specific scope adjust setup quality. Learning memory exposes read-only breakdowns with denominators and withholds rates below five known outcomes.

## Storage and migration

SQLite is the default (`ATHENA_DB_PATH`, default `data/athena.db`); `DATABASE_URL=postgresql://...` selects PostgreSQL via psycopg. Startup creates missing tables and indexes without replacing existing predictions or outcomes. New installations have foreign keys; existing baseline databases retain their tables and acquire additive evidence, findings, observation and idempotency tables. Back up an existing database before schema upgrades. Runs, evidence, analyst findings, predictions, outcomes, lessons, locks and audit events are stored separately.

## Local commands

Python 3.12+:

```bash
python -m pip install -e ".[test]"
python -m pytest -q
python -m compileall -q src tests
python -m athena.app --help
python -m athena.worker --help
python -m athena.app ask "Assess $AAPL earnings and trend"
python -m athena.app daily
python -m athena.app postmortem
python -m athena.app evaluate PREDICTION_ID
python -m athena.app cancel PREDICTION_ID "Catalyst withdrawn"
python -m athena.app lessons
uvicorn athena.server:app --host 0.0.0.0 --port 8000
```

Run these commands from the repository root after the editable install. The pytest configuration resolves the `src` layout without a manual `PYTHONPATH` setting.

`GET /health` is public. The authenticated `/run` compatibility endpoint accepts only `{"kind":"ON_DEMAND","question":"..."}`. Scheduled Daily Brief and Post-Mortem runs use the worker; API, MCP and CLI invoke the same canonical workflows.

## How George Uses ATHENA

**Morning:** the scheduled worker claims the 10:00 Europe/Athens Daily Brief, runs and stores the canonical workflow, then sends an HTML email with a plain-text alternative through Resend. The email shows market context, sourced developments, watched symbols, justified setups, Risk review, and applicable lessons. If mail is not configured, analysis still persists and delivery is audited as `NOT_CONFIGURED`.

**During the day:** use ATHENA's authenticated remote MCP endpoint from a supported ChatGPT custom-app connection, or the direct HTTP API. Ask for deep analysis, a two-to-five-symbol comparison, a market question, the latest briefing, prediction explanation, current stored setup states, or learning history. Deep analysis invokes the existing `ON_DEMAND` workflow with Company, Technical, News, Macro, Sentiment, Volatility and Market Regime evidence where available; Risk remains the final gate. The tool response adds institutional memory after isolated analyst findings are complete. No tool can submit trades or rewrite predictions, outcomes, lessons or provider settings.

**Evening:** the 23:00 Europe/Athens Post-Mortem evaluates eligible predictions, stores outcomes and lessons, then emails the run's evaluated prediction review. `UNTRIGGERED ≠ FAILED TRADE` appears explicitly. Missing benchmark or missed-opportunity evidence is omitted.

## Authenticated API and MCP

The public `/health` endpoint needs no token. All other API and MCP paths fail closed unless a valid Bearer token is supplied. Direct API clients use `ATHENA_API_TOKEN`; `ATHENA_API_TOKEN_PREVIOUS` permits overlapping rotation. The token is compared in constant time and is never logged. For example:

```bash
curl -H "Authorization: Bearer $ATHENA_API_TOKEN" https://YOUR-RAILWAY-DOMAIN/reports/daily
curl -X POST https://YOUR-RAILWAY-DOMAIN/analysis/deep -H "Authorization: Bearer $ATHENA_API_TOKEN" -H "Content-Type: application/json" -H "Idempotency-Key: nvda-morning-1" -d '{"symbol":"NVDA"}'
```

Direct endpoints: `POST /analysis/deep`, `POST /analysis/compare`, `POST /questions`, and legacy `POST /run` (restricted to `ON_DEMAND`); `GET /reports/daily`, `/reports/post-mortem`, `/predictions/{id}`, `/predictions/{id}/explain`, `/setups/active`, and `/learning`. The latest-report routes read stored reports without rerunning analysis. Setup state is the **latest persisted evaluation**, with its timestamp, rather than an invented live quote. POST requests may send `Idempotency-Key` (up to 128 characters); a repeated matching request returns the stored response. A different body with the same key returns 409. Expensive POST routes have a lightweight per-process limit of 20 requests per minute per token/IP. Questions are limited to 1,000 characters; comparisons require two to five distinct symbols.

The remote MCP streamable-HTTP endpoint is `https://YOUR-RAILWAY-DOMAIN/mcp`. It exposes `athena_deep_analysis`, `athena_compare`, `athena_market_question`, `athena_latest_daily_brief`, `athena_latest_post_mortem`, `athena_prediction`, `athena_explain_prediction`, `athena_active_setups`, and `athena_learning_summary`. Local protocol discovery can be checked with an MCP Inspector or a client that sends `initialize` and `tools/list` to `/mcp`. The endpoint carries the same authenticated access boundary as the API.

**ChatGPT connection requires OAuth.** A static custom API key cannot be supplied by the current ChatGPT MCP connector. Configure an OAuth 2.1 identity provider that publishes authorization-server metadata, supports authorization-code + PKCE `S256` and ChatGPT client registration (CIMD or DCR), and issues signed tokens with the `athena:access` scope. Set `ATHENA_PUBLIC_BASE_URL`, `ATHENA_OAUTH_ISSUER`, `ATHENA_OAUTH_AUDIENCE` (exactly `ATHENA_PUBLIC_BASE_URL/mcp`), and `ATHENA_OAUTH_JWKS_URL`. ATHENA publishes `/.well-known/oauth-protected-resource`, checks JWT signature/issuer/audience/expiry/scope against rotating JWKS, and sends an OAuth discovery challenge on 401. Test the provider's full OAuth flow with MCP Inspector before trying ChatGPT. See the [OpenAI connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt) and [authentication requirements](https://developers.openai.com/plugins/build/auth). Developer-mode/custom-app availability depends on the ChatGPT account and workspace; deploying Railway alone does not add ATHENA to ChatGPT. If unavailable, use the direct API.

## Email delivery

Set `RESEND_API_KEY`, `ATHENA_EMAIL_FROM`, and comma-separated `ATHENA_EMAIL_TO` on both scheduled workers. The sender/domain must be verified with Resend. Delivery uses Resend `POST /emails` with HTML and text, a stable provider `Idempotency-Key`, and an append-only local attempt log. The database claim prevents duplicate sends across restarted workers; transient failures retry at most three times with the same provider key. Resend's own idempotency window is 24 hours; an uncertain stale claim outside that window is not automatically resent. Permanent 4xx failures are recorded without endless retries. Email failure never rolls back a stored run, prediction or outcome. See [Resend's send API](https://resend.com/docs/api-reference/emails/send-email) and [idempotency behavior](https://resend.com/docs/dashboard/emails/idempotency-keys).

## Configuration

| Variable | Purpose |
| --- | --- |
| `ATHENA_DB_PATH` | SQLite file when `DATABASE_URL` is absent. |
| `DATABASE_URL` | PostgreSQL connection URL on Railway. |
| `ATHENA_SYMBOLS` | Comma-separated symbols scanned by Daily Brief; defaults to `SPY`. |
| `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` | Alpaca IEX market bars and last trade. |
| `FINNHUB_API_KEY` | Company fundamentals, news and social sentiment. |
| `FRED_API_KEY` | Federal Reserve macro observations. |
| `ATHENA_ENABLE_YFINANCE=1` | Optional unofficial historical fallback; install `[yfinance]` extra. |
| `ATHENA_ENABLE_LLM=1`, `OPENAI_API_KEY` | Optional Agents SDK narrative over completed findings. |
| `RESEND_API_KEY`, `ATHENA_EMAIL_FROM`, `ATHENA_EMAIL_TO` | Scheduled email delivery; recipients may be comma-separated. |
| `ATHENA_API_TOKEN`, `ATHENA_API_TOKEN_PREVIOUS` | Direct API/MCP static Bearer token and optional rotation overlap. |
| `ATHENA_PUBLIC_BASE_URL` | Public HTTPS API origin, without trailing slash, for MCP OAuth metadata. |
| `ATHENA_OAUTH_ISSUER`, `ATHENA_OAUTH_AUDIENCE`, `ATHENA_OAUTH_JWKS_URL` | OAuth issuer, this MCP resource audience, and identity-provider signing keys. |
| `ATHENA_OAUTH_SCOPE` | Required OAuth scope; default `athena:access`. |

## Railway

Create a PostgreSQL service and share its `DATABASE_URL` with the API and both workers. Deploy **one API/MCP service** with `uvicorn athena.server:app --host 0.0.0.0 --port $PORT`; configure health check `/health` and a public HTTPS domain for remote MCP. Set the auth/OAuth, provider and watchlist environment variables on the services that need them. Create **two cron services**, each running every 15 minutes UTC:

```text
python -m athena.worker DAILY_BRIEF --scheduled
python -m athena.worker POST_MORTEM --scheduled
```

Use cron expression `*/15 * * * *` on both. The worker checks `Europe/Athens` wall time and runs only from 10:00–10:14 for Daily Brief and 23:00–23:14 for Post-Mortem. This handles DST through `zoneinfo`; a database idempotency key prevents a second analytical run on the same local date. Email is sent after persistence and has its own durable claim and attempt log. Keep PostgreSQL persistent. A verified Resend sender/domain and reachable OAuth identity provider are external deployment prerequisites for email and ChatGPT respectively. Live broker connectivity is not needed or used.

## Genuine limits

Provider credentials and hosted infrastructure are external. Alpaca's IEX feed covers one exchange, and yfinance is unofficial. The news and macro rules are simple deterministic screens, not comprehensive causal research. OHLC bars cannot establish intrabar stop/target ordering; ambiguous cases remain pending. Execution stays unknown without independent execution evidence. The optional SDK path has no live-credential test in the offline suite.
