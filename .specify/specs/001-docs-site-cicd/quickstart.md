# Quickstart: Validating the Documentation Site

**Purpose**: a runnable validation guide proving the documentation feature works end to
end. Implementation code bodies, workflow internals, and complete test suites belong in
`tasks.md` and the implementation phase — this is a validation guide.

**Prerequisites**: Python 3.11+, Node.js 20, no provider credentials, **no network
access required** (Playwright's Chromium is a one-time tool download).

---

## 0. Environment

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[docs]"

npm install
npx playwright install chromium    # one-time, ~100MB
```

## 1. Build the site locally

```bash
aetheris docs build
```

**Expected**: exit `0`, a `site/` directory containing `index.html`, and no gate failures.
**Timing**: record the elapsed time — SC-004 requires it under 60 seconds.

## 2. Preview

```bash
aetheris docs serve
```

**Expected**: `http://127.0.0.1:8000` renders the landing page with a working navigation
sidebar, and rebuilds on source change.

Confirm the localhost guard refuses a public bind:

```bash
aetheris docs serve --host 0.0.0.0   # expect exit 2
```

## 3. Verify SC-001 — navigation depth

From the landing page, navigate to the API reference for one deep module, e.g.
`graph.repository`.

**Expected**: reachable in 3 clicks or fewer via the sidebar. **Fails if** any link returns
404 or requires a search to find.

## 4. Verify FR-004/FR-005 — automatic generation

Run the automated tests rather than doing this by hand:

```bash
pytest tests/contract/test_api_reference.py -q
```

**Expected**: all pass. They assert that a newly added public module produces a page with
its signature and docstring, that navigation picks it up with no manual edit, and that no
page is orphaned.

## 5. Verify FR-006 — missing docstring fails the build

Covered by the same suite:

```bash
pytest tests/contract/test_api_reference.py::TestMissingDocstringFailsTheBuild -q
```

**Expected**: the build exits non-zero naming the offending path and rule, because the
docstring gate runs **before** generation.

## 6. Verify FR-008 — private names are excluded

```bash
pytest tests/contract/test_api_reference.py::TestPrivateNamesExcluded -q
```

**Expected**: no generated page renders an entity whose name begins with `_`. Exclusion is
by naming convention (`filters: ["!^_"]`), not a per-module allowlist.

## 7. Verify the accessibility gates

Two gates, one per rule class. Both are hermetic — neither needs network access.

### 7a. Structural gate

```bash
npx html-validate "site/**/*.html" --formatter stylish
```

**Expected**: exit `0` on a clean build. Covers alt text, form labels, ARIA misuse
patterns, invalid nesting, duplicate ids/attributes, landmark structure.

### 7b. Rendered gate

```bash
node scripts/check_a11y_rendered.js site/
```

**Expected**: exit `0`. The bespoke Playwright checker (Apache-2.0) computes colour
contrast and heading-level order on rendered pages — the rules static analysis cannot
reach.

**Print the covered rule list before trusting any claim:**

```bash
node scripts/check_a11y_rendered.js site/ --list-rules
```

It prints two lists: what is **covered** and what is deliberately **not verified**. Rules
outside the covered list are *unverified*, not *compliant* — a bespoke checker intentionally
covers fewer rules than a mature engine's ~90, and the decision to accept that rather than
amend the constitution for `axe-core` (MPL-2.0) is recorded in the plan.

### 7c. Prove both gates fail closed (critical)

A checker that silently passes is worse than no checker — that exact failure mode, reporting
"1 of 1 passed" with exit code 0 on a page of violations, is why one permissively-licensed
alternative was rejected during research.

The permanent fixture `tests/fixtures/docs/a11y-bad.html` **must** fail **both** gates:

```bash
npx html-validate tests/fixtures/docs/a11y-bad.html          # expect exit 1
node scripts/check_a11y_rendered.js tests/fixtures/docs       # expect exit 1
```

Then run the automated guard:

```bash
pytest tests/contract/test_a11y_gates.py -q
```

**Expected**: all pass, including the fail-closed tests on missing commands, non-zero
exits, and timeouts.

## 8. Verify VR-5 — secret scan

```bash
pytest tests/contract/test_secret_scan.py -q
```

**Expected**: all pass. They assert real credential shapes are detected, documented
placeholders are ignored, and — critically — that HTML markup cannot mask a credential
(a regression guard for a bug where the allowlist matched whole lines containing `<`).

## 9. Verify FR-011/FR-012 — deployment and failure isolation

```bash
pytest tests/contract/test_docs_workflow.py -q
```

**Expected**: all pass. They parse `.github/workflows/docs.yml` and assert:

- it triggers on push to **main** only, never on pull requests
- the deploy job re-checks `refs/heads/main`, so no trigger route can publish another ref
- `deploy` declares `needs: build`, so a failed build cannot reach the publish step
- every gate runs in the build job before the artifact is uploaded
- the build needs no provider credentials (verified by a step in the workflow itself)

> **Requires your confirmation.** Pushing to `main` and observing the live GitHub Actions
> run and the Pages URL cannot be done from a development checkout. The contract is
> verified structurally; the *live* publish and a forced-failure rehearsal need you to
> push once and check the run.

To rehearse failure isolation after the first successful deploy:

```bash
git revert --no-edit HEAD && git push origin main
```

**Expected**: the failing run publishes nothing, and the previously published site keeps
serving. A failed build must never produce a partial deploy.

## 10. Verify FR-010 — build on every push and PR

CI runs the documentation gates on every push and pull request:

```bash
grep -A2 'name: CI' .github/workflows/ci.yml
```

**Expected**: a `docs` job exists running the docstring gate, site build, secret scan, and
both accessibility gates.

---

## Success Criteria Cross-Reference

| SC | Verification | Target |
|---|---|---|
| SC-001 | §3 — click depth to a deep module | ≤ 3 clicks |
| SC-002 | §4 — zero missing-docstring errors on a clean build | 100% of public entities |
| SC-003 | §9 — time from push to visible change | < 5 minutes *(requires your push)* |
| SC-004 | §1 — timed local build | < 60 seconds |
| SC-005 | §9 — forced failure leaves prior site live | 100% of failures *(requires your push)* |
| SC-006 | §0–§1 — single documented command | No extra setup |
| SC-007 | §7a + §7b — both accessibility gates | **Verify, don't assume** — see below |

## ⚠️ SC-007: verify, don't assume

Spec SC-007 requires zero WCAG 2.1 Level AA violations. The gap between "the gates pass"
and "AA is satisfied" is **rule breadth**: the bespoke rendered checker covers the rules it
was written for, not all ~90 a mature engine would check.

Before marking SC-007 as passing, read `node scripts/check_a11y_rendered.js --list-rules`
and confirm the covered list addresses the AA criteria that matter for a documentation
site — contrast (1.4.3), headings (1.3.1). The focus criteria (2.1.1, 2.4.3, 2.4.7) and
two theme defects (1.3.5, 3.3.2) are **explicitly not verified**. Rules outside the list
are *unverified, not compliant*.

## Deferred items

- **Live deployment** — the workflow is verified structurally; an actual push, the
  Actions run, and the Pages URL require you to push to `main`.
- **Contrast beyond the palette** — a bespoke check against a documented palette, not a
  full colour audit.
- **Versioned documentation** — the site documents the current state of `main`.
