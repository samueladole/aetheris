# CLAUDE.md — Aetheris

Instructions for Claude working in this repository. Read this first; it records
how the project works, how to verify a change, and the traps that have already
cost time. Deeper material: `README.md` (CI-checked facts), `docs/architecture.md`,
`docs/codebase.md`, `docs/roadmap.md`, and `.specify/`.

## What this project is

Aetheris is a Python 3.11+ (CPU-only) pipeline that ingests macro series (FRED),
daily prices (Tiingo), company profiles and news (Finnhub); scores the news for
sentiment; aligns everything as of one point in time with **no lookahead**; and
upserts it into a DuckDB knowledge graph, as a durable DBOS workflow. Everything
must be permissively licensed (constitution Principle I).

Pipeline: `trigger → ingest → sentiment → graph_integration → evaluation`.
The step logic is plain functions in `src/orchestration/steps.py`; the DBOS
workflow (`workflows.py`) and the CLI (`src/cli/main.py`) both call them.

## Commands

Node is **not** on PATH on this machine, and every shell command starts fresh,
so put the Node path on the same command line as anything that needs it:

```bash
PATH="$(mise where node@20)/bin:$PATH" .venv/bin/pytest tests/ -q --timeout 300   # full suite, ~2–4 min
.venv/bin/pytest tests/unit/test_edges.py -q --timeout 120                       # focused
.venv/bin/ruff check src/ tests/ scripts/
.venv/bin/lint-imports
.venv/bin/python scripts/check_readme_sync.py
.venv/bin/aetheris check-licenses --strict
PATH="$(mise where node@20)/bin:$PATH" .venv/bin/aetheris docs build            # all six docs gates
.venv/bin/pytest tests/ --collect-only -q | tail -1                             # test count for the README
```

- Run **one pytest process at a time, in the foreground**. Concurrent runs share
  `site/` and `src/graph/tmp_probe.py` and corrupt each other. If
  `src/graph/tmp_probe.py` exists after an interrupted run, delete it; never
  commit it.
- `pytest -q` prints no summary line here (pyproject already adds `-q`); drop
  the extra `-q` or read the exit code when you need a count.
- If a full run takes far longer than pytest's own reported time, the machine
  slept; it is not a hang.

## Before calling a change done

1. Full suite green, ruff clean, `lint-imports` keeps 5 contracts.
2. `scripts/check_readme_sync.py` passes. It fails CI when the README states a
   stale fact: update the **test count** (`**N tests**`) and the API reference
   page count in the same commit as the change. Any "not yet implemented" claim
   in the README must cite a task that is still open.
3. If the docs site sources changed (docstrings, `docs/`), `aetheris docs build`
   passes.
4. If behaviour changed, the README's Known gaps and `docs/roadmap.md` still
   tell the truth.

## Rules this codebase relies on

- **Spec first (Principle V).** New behaviour gets a spec clarification or FR in
  `.specify/specs/spec.md`, a plan, and tasks before code. Feature work lives in
  `.specify/specs/NNN-name/` (`001-docs-site-cicd`, `002-pipeline-wiring`). Do
  not put plans under `docs/` — that directory is published.
- **Layer boundaries** (`.importlinter`): only `src.orchestration` and `src.cli`
  may combine ingestion, sentiment, and graph. Config is a leaf. Parsing belongs
  in ingestion, entity matching in sentiment, statistics in graph.
- **Point-in-time.** A value influences the graph only after its availability
  time: closes at 16:00 New York (`close_available_at`), macro values at first
  release plus `release_time` or 23:59:59 New York (`release_available_at`).
  Never use observation time as availability.
- **No wall clock in what a step writes.** The trigger fixes `at`; every written
  timestamp comes from it. `tests/integration/test_point_in_time.py` requires
  byte-identical replays.
- **UTC everywhere.** Naive datetimes are errors. `GraphDatabase` pins DuckDB's
  session time zone to UTC; query results come back tz-aware.
- **Open the graph with `GraphDatabase(path)`**, never `get_database(path)` in
  new code: the latter is a process-wide singleton that ignores later paths.
- **DBOS step outputs must be JSON-serialisable.** Steps pass payload ids, not
  bodies. A provider failure (`ProviderUnavailable`) is recorded in the
  manifest's `failures`; any other exception is a defect and must propagate.
- **Credentials are never persisted or printed.** Raw payloads store the caller's
  request params, not the adapter's (which carry keys); failure messages are
  redacted.
- **Licences.** The licence gate fails closed. A new non-permissive transitive
  dependency needs an explicit A-001 entry in
  `.specify/memory/constitution-amendments.md` and `KNOWN_VIOLATIONS`; never
  loosen the classifier. On Linux install CPU-only torch (`--torch-backend cpu`).
- **Docstrings on every public name.** The API reference is generated from them.

## Provider facts (verified live)

- FRED: request first releases with `output_type=4` and a **windowed**
  `realtime_start` (default 730 days). The full range since 1776 exceeds FRED's
  vintage-date cap for daily series (DGS10 returned HTTP 400). Rows whose
  `realtime_start` equals the requested start are dropped (release date unknown).
- Tiingo: prices are at `/tiingo/daily/{ticker}/prices` (the bare path is
  metadata). Free tier: 50/hour, 1,000/day.
- FMP `historical-price-full` is a dead legacy endpoint for this account.
- Finnhub `/news?category=general` has empty `related` tags, so articles are
  matched to assets by ticker and company name.
- A live `aetheris run` needs only FRED, Finnhub, and Tiingo keys (`.env`).

## Tests and fixtures

- No test makes a live provider call. Use `tests/integration/fakes.py`
  (`RoutedHttpClient`, shared fixture routes) and `dbos_env` from
  `tests/integration/conftest.py`. In tests that evaluate, `monkeypatch.chdir`
  to `tmp_path` so MLflow does not write `./mlflow.db` into the repo.
- Synthetic fixtures live in `tests/fixtures/providers/` and are committed.
  Recorded cassettes (`tests/fixtures/cassettes/*.yaml`) are **git-ignored on
  purpose**: Finnhub, FMP, and Tiingo forbid redistributing free-tier data and
  the repo is public. Tests must pass whether or not cassettes exist.
- A contract test fails if any other file under `tests/` is git-ignored.
- Live calls (recording cassettes, a live `aetheris run`) spend the owner's
  free-tier quota: ask first, run once, and point `--db`/`--raw` at scratch
  paths so the repo's local data files are untouched.

## Git and GitHub

- Commit messages are conventional (`feat(scope): …`, `fix(…)`, `docs(…)`,
  `test(…)`). **Do not add a `Co-Authored-By: Claude` trailer**, and do not add
  a "Generated with Claude Code" footer to pull requests — the owner removed
  both and wants commits attributed to them alone.
- Work on a branch and open a pull request; ask before pushing or merging.
- Repository ruleset `ruleset-001` applies to **all branches**: no force-pushes,
  no deletions, no bypass. Rewriting a pushed branch needs the owner's go-ahead
  to pause it, then set it straight back to `active`.
- CI (`.github/workflows/ci.yml`) runs the gates above on every push and PR;
  `docs.yml` publishes the site to GitHub Pages from `main` only, after its
  build passes. Keep both green.
- Never commit `.env`, `*.duckdb`, `aetheris-raw.db`, `*.dbos.sqlite`,
  `mlflow.db`, cassettes, or anything under `.superpowers/`.

## Working style the owner has accepted

- Verify claims against the code and git, not against `tasks.md` checkboxes:
  tasks here were once checked off without their deliverables.
- Fix root causes and pin each with a test; say plainly when something is
  unverified (for example, a single live run is one sample, not a P95).
- Larger work has been run as a spec-first plan executed task by task with a
  review after each task and a final whole-branch review.
