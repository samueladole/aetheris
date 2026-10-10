# Contract: Ingestion Adapter Protocol (Principle II)

**Module**: `src/ingestion/base.py`
**Purpose**: define the boundary that keeps Ingestion independently importable and replaceable —
constitution Principle II. Any adapter that satisfies this protocol can be swapped in without
touching orchestration, sentiment, or the graph layer.

---

## Protocol

```python
class ProviderAdapter(Protocol):
    provider: str                 # key in the §3.2.1 registry
    endpoints: tuple[str, ...]    # declared endpoints, e.g. ("quote", "/news")

    def can_handle(self, endpoint: str) -> bool: ...

    def fetch(self, endpoint: str, params: Mapping[str, str]) -> RawPayload:
        """Return an immutable raw payload. MUST NOT parse, score, or store derived values."""
```

## Invariants

| # | Invariant | Enforced by |
|---|---|---|
| A1 | Adapters return **raw immutable payloads only** — no domain objects, no scoring, no graph writes | Protocol signature + unit test per adapter |
| A2 | Adapters never call another adapter — provider fallback is the orchestrator's job (`registry.py`) | Import-linter contract |
| A3 | Adapters contain **no** retry/backoff logic of their own; that is the orchestration layer's policy (FR-003) | Import-linter contract (`ingestion` may not import `orchestration`) |
| A4 | Adapters declare their quota usage through the registry, so the shared rate limiter can reason about all providers uniformly (FR-020) | `registry.py` test |
| A5 | Adapters are importable without importing `orchestration`, `sentiment`, `graph`, or `observability` | Import-linter contract (Principle II) |
| A6 | Every adapter ships a `vcrpy` cassette for its integration test; **live calls are never made in CI** | Constitution "Testing Standards" |
| A7 | An adapter's response to 429/5xx is a typed `ProviderUnavailable` exception, not a swallowed error | Unit test |

## Shared Cross-Cutting Services (injected, not imported by adapters)

- **Rate limiter** (`ratelimit.py`) — token bucket, thread-safe, fed from registry config. In-process
  for v1: §3.2 requires sharing "across worker threads", which in-process state satisfies. See
  `research.md`/plan V2 for why Redis is absent.
- **TTL cache** (`cache.py`) — SQLite-backed, per-endpoint TTL from the registry. Cache is checked
  before any provider call; `--force` bypasses it.

## Registry Contract

`registry.py` is the single source of truth for provider selection. Its required behavior:

1. Load the §3.2.1 table (provider → module → endpoints → free-tier limit → fallback order) as **data**,
   not code — quotas must be tunable without a code change.
2. On `ProviderUnavailable` (429 or 5xx), try the next non-exhausted provider for the same data type,
   in fallback order.
3. Record every fallback and exhaustion as an OTel span event.
4. Expose current quota consumption to `aetheris status`.

## Prohibited Dependencies (the decoupling rules, mechanically)

```
ingestion  -> orchestration   ❌ forbidden (A3)
ingestion  -> sentiment       ❌ forbidden
ingestion  -> graph           ❌ forbidden
sentiment  -> graph           ❌ forbidden (the analyzer emits the payload contract; the
                                repository consumes it — boundary is the JSON schema)
graph      -> ingestion       ❌ forbidden
orchestration -> {ingestion, sentiment, graph}  ✅ allowed (it drives them)
cli        -> anything        ✅ allowed
*          -> observability    ✅ allowed, but tracer must be injected, not imported at module
                                scope, so layers stay importable in isolation
```

These rules are enforced in CI by `importlinter` contracts; a violation fails the build, which is how
Principle II stays true over time rather than only at Milestone 1.
