# Validation Record

Results from running `.specify/specs/001-docs-site-cicd/quickstart.md`. Update this
file whenever a gate's behaviour changes, and keep the numbers honest — a stale
"passing" here is worse than a failing one.

**Last run**: 2026-10-11

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

## Not verifiable from a checkout

These require a push to `main` and watching the real GitHub Actions run:

- **SC-003** — the live site updates within 5 minutes of a push
- **SC-005** — a forced build failure leaves the previously published site intact

Both are verified *structurally* by `tests/contract/test_docs_workflow.py`; the live
behaviour still needs a human to push and confirm.
