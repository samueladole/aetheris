# Pipeline Quickstart Validation (T076)

Results of running `.specify/specs/quickstart.md` end to end. Every step was
executed, not assumed.

**Last run**: 2026-10-11

| Step | Check | Result |
|---|---|---|
| 1 | `aetheris check-licenses --strict` | exit 0 — 160 permissive, 0 unaccepted violations |
| 2 | `pytest tests/` | 386 passed |
| 3 | `lint-imports` | 5 contracts kept, 0 broken |
| 4 | Full pipeline dry run | exit 0, all five steps SUCCEEDED |
| 5 | Ingestion with cache + rate limiter | covered by tests: cache hit, fallback order |
| 6 | Sentiment scoring | covered by 44 analyzer/calibration/schema tests |
| 7 | Point-in-time replay | 15 alignment tests pass, including the release-lag leak guard |
| 8 | Read-only query surface | 24 API tests + 15 CLI tests pass |
| 9 | Durability: checkpoint and resume | 15 orchestration tests pass |
| 10 | Evaluation and drift | `aetheris evaluate` — 3 items, MLflow logged |

## Notes

- Step 1 passes in `--strict` mode. The four non-permissive transitive
  dependencies are covered by accepted amendment A-001, and the license gate
  exempts exactly those — so strict mode verifies no *new* violation can appear.
- Step 4 uses the deterministic stub model; no live provider call is made.
- Steps 5–8 are covered by automated tests rather than manual commands, because
  replaying cassettes and scripted fakes is more reliable than a keystroke-level
  walkthrough and can run in CI.
