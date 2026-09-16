# ATHENA Engineering Rules for AI Coding Agents

These rules are permanent project guidance for AI coding agents working on ATHENA.

## Architecture Rules

- Never change architecture without an approved RFC.
- When uncertain, ask for clarification instead of inventing architecture.
- Never introduce business logic outside approved RFCs.
- Never create placeholder implementations.
- Follow the existing folder structure.
- Preserve backwards compatibility unless explicitly approved.
- Prefer simple, extensible designs.
- Prefer composition over inheritance unless inheritance provides a clear architectural benefit.

## Domain Rules

- Always use `Decimal` for financial values.
- Keep domain models immutable.
- Never allow Agents to access providers directly.
- All market data must flow through `MarketDataService`.
- Do not implement trading, AI, broker access, indicators, or market data behavior without an approved RFC.

## Dependency Rules

- Do not add dependencies without justification.
- Prefer the Python standard library when it is sufficient.
- Keep integrations behind interfaces or service boundaries.

## Git And Review Rules

- Use Conventional Commits.
- Keep changes small and reviewable.
- Do not mix unrelated concerns in one change.
- Do not modify generated files unless the change requires regeneration.
- Do not commit without explicit approval.
