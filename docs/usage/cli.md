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
- `--resume WORKFLOW_ID` resumes from the first failed step. Steps that already
  succeeded return their checkpointed results, so a resume never re-fetches.
- A live run needs `FRED_API_KEY`, `FINNHUB_API_KEY`, and `TIINGO_API_KEY`; the
  Alpha Vantage and FMP keys are optional.
- A fetch that fails does not fail the run: the output lists it under
  `failures` (`summary.failures` with `--json`).

### `aetheris ingest`

Run a single ingestion adapter through the registry.

```bash
aetheris ingest <provider> <endpoint> [--param KEY=VALUE] [--data-type TYPE] [--force] [--ttl SECONDS]
```

Providers: `fred`, `alpha_vantage`, `finnhub`, `fmp`, `tiingo`.

The provider must declare the endpoint, but it does not pin who serves it: the
registry tries every provider that declares the endpoint, in fallback order,
and the output names the one that answered. FRED requests default to first
releases from the last 730 days; pass `--param realtime_start=YYYY-MM-DD` to
change the window.

### `aetheris docs`

Build, preview, or open the documentation site.

```bash
aetheris docs build      # run all gates and emit site/
aetheris docs serve      # preview at http://127.0.0.1:8000
aetheris docs open       # print the preview URL
```

`serve` binds to localhost only and is refused for any other host.

### `aetheris score`

Score news text with a sentiment model and print the payloads.

```bash
aetheris score [--entity ENTITY_ID] [--since ISO8601] [--model MODEL] [--temperature T]
aetheris score --text "Company beats expectations" [--text "..."]
```

- Without `--text` it scores the news already stored by `aetheris ingest` or
  `aetheris run`, matched to the watchlist, and prints one payload per article
  per matched asset. Nothing is written.
- `--model` takes an id from the licence-checked manifest; the default is the
  deterministic stub scorer.
- `--entity` keeps one asset's payloads; `--since` keeps text published from
  that time.

### `aetheris graph`

```bash
aetheris graph upsert [--since ISO8601] [--until ISO8601] [--rebuild-edges]
aetheris graph query entity|sentiment|edges|influencers|assets [options]
```

`graph upsert` scores the stored news, aligns everything as of now, and writes
nodes, observations, sentiment metrics, and derived edges into the graph. It
prints the counts written, and exits `2` when the raw store is empty.
`--since/--until` limit the sentiment metrics written; `--rebuild-edges` is
accepted, but edges are recomputed on every upsert anyway.

`graph query` runs read-only queries:

```bash
aetheris graph query entity      --id equity:NASDAQ:AAPL
aetheris graph query sentiment   --entity equity:NASDAQ:AAPL --start ISO8601 [--end ISO8601]
aetheris graph query edges       --from NODE_ID [--rel REL_TYPE] [--min-confidence FLOAT]
aetheris graph query influencers --id equity:NASDAQ:AAPL [--top N] [--min-confidence FLOAT]
aetheris graph query assets      [--class equity|forex|crypto|index] [--top N]
```

All datetimes must carry a time zone and come back in UTC. The CLI contract
names this command `aetheris query`; the implementation nests it under `graph`.

### `aetheris evaluate`

Evaluate the graph's sentiment scores from the last 24 hours against the
previous 7 days, and log the result to MLflow when it is available.

```bash
aetheris evaluate [--entity ENTITY_ID] [--min-confidence FLOAT] [--drift-threshold FLOAT] [--json]
```

The same filters apply to the current scores and to the drift reference. An
empty graph prints `evaluated 0 item(s)` and exits `0`.

### `aetheris check-licenses`

Constitution Principle I gate. Exits `3` if any *unaccepted* non-permissive
licence is found. `--no-exemptions` ignores the accepted deviations.

### `aetheris status`

Provider health, quota consumption, cache hit ratio, and last workflow state.

### `aetheris version`

Print version and exit.
