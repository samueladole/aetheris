# Validation Record

Results from running `.specify/specs/001-docs-site-cicd/quickstart.md`. Update this
file whenever a gate's behaviour changes, and keep the numbers honest — a stale
"passing" here is worse than a failing one.

**Last run**: 2026-10-11 (docs gates); the live pipeline run below is dated 2026-10-21

## Quickstart steps

| Step | Check | Result |
|---|---|---|
| §1 | `aetheris docs build` | exit 0 in 8s (SC-004 target <60s) |
| §2 | `aetheris docs serve` + host guard refuses `0.0.0.0` | exit 2 as required |
| §3 | Navigation depth to a deep reference page | ≤ 3 clicks |
| §4 | `tests/contract/test_api_reference.py` | pass |
| §5 | Missing docstring fails the build before generation | pass |
| §6 | Private names excluded from the reference | pass |
| §7a | `html-validate site/**/*.html` | exit 0 |
| §7b | `check_a11y_rendered.js site/` | exit 0 |
| §7c | Permanent fixture fails **both** gates | exit 1 each — fail-closed confirmed |
| §8 | `tests/contract/test_secret_scan.py` | pass |
| §9 | `tests/contract/test_docs_workflow.py` | 12/12 pass |
| §10 | `docs` job present in CI | confirmed |

## Licence gates

| Gate | Scope | Result |
|---|---|---|
| `aetheris check-licenses --strict` | Python distributions | 184 permitted, 6 exempted (A-001), 1 verified by hand (`skops`), 0 unaccepted violations — re-run 2026-10-19 after the gate began failing closed |
| `node scripts/check_licenses.js` | Node packages | 58 scanned, 0 rejected |

## First live pipeline run

**Run**: 2026-10-21, `aetheris --json run --trigger schedule` against the live
FRED, Tiingo, and Finnhub free tiers, with its own data files (not the repo's
default ones). It made about 22 requests: 5 FRED, 8 Tiingo, 8 Finnhub profiles,
and 1 Finnhub news.

| Step | State |
|---|---|
| trigger | SUCCEEDED |
| ingest | SUCCEEDED |
| sentiment | SUCCEEDED |
| graph_integration | SUCCEEDED |
| evaluation | SUCCEEDED |

Workflow status `SUCCESS`. Summary: **21 payloads fetched, 3 news items scored,
55 edges** (28 CORRELATES_WITH, 24 INFLUENCES, 3 TARGETS). The graph held 8
assets and 1,945 macro observations across four series.

**Failure**: one fetch failed and the other 21 continued. FRED series `DGS10`
(10-year Treasury, daily) returned `fred::series/observations unavailable (HTTP
400): request rejected`. It was not retried. Each series is fetched with
initial-release vintages over the full real-time range
(`src/ingestion/fred_client.py`); the 400 may be FRED rejecting that request for
a daily series, but the cause was not confirmed. The workflow still reported
`SUCCESS` because a failed fetch is recorded in the manifest's `failures` rather
than failing the run, and the CLI summary does not list it. No credential
appeared in the output.

**Duration against SC-001** (P95 < 5 minutes): 7.8 s wall-clock (`time -p`
`real 7.77`). This is **one sample, not a P95**; it shows a single scheduled run
finished far inside the target but does not establish the percentile.

## Not verifiable from a checkout

These require a push to `main` and watching the real GitHub Actions run:

- **SC-003** — the live site updates within 5 minutes of a push
- **SC-005** — a forced build failure leaves the previously published site intact

Both are verified *structurally* by `tests/contract/test_docs_workflow.py`; the live
behaviour still needs a human to push and confirm.
