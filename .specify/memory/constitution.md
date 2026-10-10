# Aetheris Constitution

## Core Principles

### I. Strict Open-Source Compliance
- **Zero Commercial Dependencies:** The project must rely strictly on open-source software (OSS) licensed under permissive terms (e.g., MIT, Apache 2.0). 
- **No Paid APIs or Proprietary Tiers:** All data ingestion must utilize official free tiers (FRED, Alpha Vantage, Finnhub, Financial Modeling Prep, Tiingo) or public open datasets. Commercial orchestration or proprietary SaaS observability tools are strictly prohibited.

### II. Decoupled Modular Architecture
- **Isolated Boundaries:** Each system layer—Ingestion, Caching, Sentiment Analysis, Knowledge Graph, and Orchestration—must operate as an independent, loosely coupled module.
- **Reusability & Portability:** Core components and workers must be designed for portability, allowing them to be imported seamlessly into future autonomous agentic workflows or independent projects without circular or heavy framework dependencies.

### III. Resilient Durable Execution (NON-NEGOTIABLE)
- **State Checkpointing & Recovery:** All pipeline workflows must be executed as durable state machines capable of automatic recovery, retries with exponential backoff, and failure compensation from the exact point of failure.
- **Resilient Network Boundaries:** External API interactions must incorporate robust rate limiting, token bucket queues, and immutable local caching to handle quota limits gracefully.

### IV. Comprehensive Observability & Evaluation
- **End-to-End Tracing:** Every workflow execution, ingestion fetch, sentiment model inference, and graph mutation must emit distributed trace spans and performance metrics using open-source telemetry standards.
- **Continuous Evaluation:** Model outputs, confidence scores, and data drift must be logged and evaluated using open-source tracking frameworks.

### V. Specification-Driven Rigor
- **Code Follows Spec:** No code or infrastructure component shall be written without a corresponding specification update or task breakdown mapping directly to project architecture goals.
- **Red-Green-Refactor Lifecycle:** Strict enforcement that specs drive tasks, tests fail before implementation, and changes respect architectural boundaries.

## Additional Constraints & Technology Standards

- **Language & Runtime:** Modular Python workers where ML libraries reside; containerized,
  language-agnostic interfaces between layers so any layer can be replaced independently.
- **Persistence Layer:** Self-hosted open-source engines only — SQLite (checkpoint/metrics),
  Redis (rate-limit counters, cache), NetworkX or Neo4j Community (knowledge graph).
  Zero cloud-lock-in.
- **Dependency Policy:** No commercial, paid-tier, or proprietary SaaS dependency at runtime.
  Model weights must come from open licenses (e.g., Apache 2.0 / MIT) and be version-pinned.
- **Secrets:** API keys are supplied only via environment variables or local secret files that
  are git-ignored; never committed.
- **Rate Limit Safeguards:** Strict adherence to free-tier constraints across all five financial
  providers with forced backoff strategies and per-endpoint TTL budgets.
- **Testing Standards:** Unit tests pure; integration tests hit providers only through VCR-style
  recorded fixtures to avoid live quota consumption.

## Development Workflow & Quality Gates

- **Specification-First Gate:** All features must originate from `.specify/specs/spec.md` updates
  and get parsed via `/plan` and `/tasks` commands before code generation begins.
- **Red-Green-Refactor Lifecycle:** Specs drive tasks; tests must fail before implementation;
  every change maps back to an architectural boundary.
- **Automated Verification:** AI coding agents must validate changes against this constitution and
  run lint, type-check, and the full test suite prior to task closure, and must report which FR IDs
  the change exercises.
- **Review Gate:** No task may be marked complete without evidence that traces and metrics for its
  code path are emitted (constitution §IV).

## Governance

Constitution supersedes all other practices; Amendments require documentation, approval, and migration plans. All contributions and AI agent code generation passes must verify compliance with these rules; Complexity must be avoided in favor of simplicity and YAGNI.

**Version**: 1.0.0 | **Ratified**: 2026-10-10 | **Last Amended**: 2026-10-10