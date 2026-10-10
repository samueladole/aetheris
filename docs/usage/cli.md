# CLI Reference

Aetheris exposes a single entrypoint. Global options are accepted either before
or after the subcommand.

## Global options

<table>
  <thead>
    <tr><th scope="col">Option</th><th scope="col">Meaning</th></tr>
  </thead>
  <tbody>
    <tr><td><code>--config PATH</code></td><td>Settings file; defaults to <code>$AETHERIS_CONFIG</code></td></tr>
    <tr><td><code>--db PATH</code></td><td>DuckDB graph file (default <code>./aetheris.duckdb</code>)</td></tr>
    <tr><td><code>--raw PATH</code></td><td>SQLite raw payload file (default <code>./aetheris-raw.db</code>)</td></tr>
    <tr><td><code>--json</code></td><td>Emit machine-readable JSON on stdout</td></tr>
    <tr><td><code>--trace</code></td><td>Force OpenTelemetry span export</td></tr>
  </tbody>
</table>

## Exit codes

<table>
  <thead>
    <tr><th scope="col">Code</th><th scope="col">Meaning</th></tr>
  </thead>
  <tbody>
    <tr><td><code>0</code></td><td>Success</td></tr>
    <tr><td><code>1</code></td><td>Runtime failure</td></tr>
    <tr><td><code>2</code></td><td>Usage error</td></tr>
    <tr><td><code>3</code></td><td>Gate failure (docstrings, build, accessibility, or licence)</td></tr>
  </tbody>
</table>

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

Read-only graph queries.

```bash
aetheris query influencers --id equity:NASDAQ:AAPL --top 5 [--json]
aetheris query sentiment   --entity equity:NASDAQ:AAPL --start ISO8601 [--json]
aetheris query edges       --from NODE_ID [--min-confidence FLOAT] [--json]
```

### `aetheris check-licenses`

Constitution Principle I gate. Exits `3` if any *unaccepted* non-permissive
licence is found. `--no-exemptions` ignores the accepted deviations.

### `aetheris status`

Provider health, quota consumption, cache hit ratio, and last workflow state.

### `aetheris version`

Print version and exit.
