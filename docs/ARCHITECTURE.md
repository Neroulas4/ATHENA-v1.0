# ATHENA architecture

The approved merge specification is `ATHENA_CODEX_SCAFFOLD_MERGE_MASTER.md`, supplied with this implementation task. This document describes the implemented boundaries.

```text
API / CLI / scheduled worker
          │
      Workflows
          │
      Orchestrator ─────────── Store / LearningMemory
          │                         ▲
      CollectorService              │
          │                         │
  MarketDataService + source HTTP   │
          │                         │
  immutable specialist DTOs         │
          │                         │
  independent deterministic analysts
          │
  synthesis → Risk challenge → prediction
          │                         │
          └──── outcome / lesson loop┘
```

`MarketDataService` is the only path for prices and bars. Finnhub and FRED collectors provide company, news, sentiment and macro observations. Analysts do not import collectors, providers, one another, or the store. Their DTOs are distinct frozen dataclasses. The orchestrator calls each selected analyst with its own DTO and invokes synthesis only after all selected findings exist. Risk operates after synthesis and may withhold a setup. Optional SDK narrative generation sees the completed structured result only.

Prediction inserts are immutable. Outcome inserts are append-only. Pending evaluations are revisited. Lessons aggregate one observation per prediction and their transitions are audited. SQLite and PostgreSQL share the same logical schema and SQL access layer. New installations have foreign keys; older baseline databases receive additive tables on startup without destructive rebuilds. Production migrations should be applied after backup.

No execution or broker module is part of this phase. Data availability and uncertain OHLC event ordering remain explicit. See [README](../README.md) for provider configuration, workflows, tests and deployment.
