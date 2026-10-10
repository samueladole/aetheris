# CLI Reference

Aetheris exposes a single entrypoint with global options accepted either before
or after the subcommand.

## Global options

| Option | Meaning |
|---|---|
| `--config PATH` | Settings file; defaults to `$AETHERIS_CONFIG` |
| `--db PATH` | DuckDB graph file (default `./aetheris.duckdb`) |
| `--raw PATH` | SQLite raw payload file (default `./aetheris-raw.db`) |
| `--json` | Emit machine-readable JSON on stdout |
| `--trace` | Force OpenTelemetry span export |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Runtime failure |
| `2` | Usage error |
| `3` | Gate failure (docstrings, build, accessibility, or license) |

## Commands

### `aetheris run`

Execute the full pipeline durably.

```bash
aetheris run --trigger schedule|event [--dry-run] [--resume WORKFLOW_ID]
```

- `--dry-run` validates configuration and quotas without calling any provider.
- `--resume WORKFLOW_ID` resumes from the first failed step.

### `aetheris ingest`

Run a single ingestion adapter through the registry.

```bash
aetheris ingest <provider> <endpoint> [--param KEY=VALUE] [--data-type TYPE] [--force] [--ttl SECONDS]
```

Providers: `fred`, `alpha_vantage`, `finnhub`, `fmp`, `tiingo`.

### `aetheris docs`

Build, preview, or open the documentation site.

```bash
aetheris docs build      # run all gates and emit site/
aetheris docs serve      # preview at http://127.0.0.1:8000
aetheris docs open       # print the preview URL
```

`serve` binds to localhost only and is refused for any other host.

### `aetheris query`

Read-only graph queries (see the API reference for the underlying Python API).

```bash
aetheris query influencers --id equity:NASDAQ:AAPL --top 5 [--json]
aetheris query sentiment   --entity equity:NASDAQ:AAPL --start ISO8601 [--json]
aetheris query edges       --from NODE_ID [--min-confidence FLOAT] [--json]
```

### `aetheris check-licenses`

Constitution Principle I gate. Exits `3` if any *unaccepted* non-permissive
license is found; `--no-exemptions` ignores the accepted deviations.

### `aetheris status`

Provider health, quota consumption, cache hit ratio, and last workflow state.

### `aetheris version`

Print version and exit.
