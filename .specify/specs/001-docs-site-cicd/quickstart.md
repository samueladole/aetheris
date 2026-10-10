# Quickstart: Validating the Documentation Site

**Purpose**: a runnable validation guide proving the documentation feature works end to
end. Implementation code bodies and workflow internals belong in `tasks.md` — this is a
validation guide.

**Prerequisites**: Python 3.11+, Node.js 20 (for the accessibility gate), no provider
credentials, **no network access required**.

---

## 0. Environment

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[docs]"
```

Optional, for the accessibility gate only:

```bash
npm install --save-dev html-validate
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

## 3. Verify SC-001 — navigation depth

From the landing page, navigate to the API reference for one deep module, e.g.
`graph.repository`.

**Expected**: reachable in 3 clicks or fewer via the sidebar. **Fails if** any link returns
404 or requires a search to find.

## 4. Verify FR-004/FR-005 — automatic generation

```bash
# Add a throwaway public module
cat > src/graph/_tmp_probe.py <<'EOF'
"""A temporary probe module."""
def probe_function(x: int) -> int:
    """Return the input unchanged.

    Args:
        x: A number.

    Returns:
        The same number.
    """
    return x
EOF

aetheris docs build
```

**Expected**: a page for `graph._tmp_probe` appears in the reference with its function
signature and docstring, **without editing any documentation or navigation file**. Then
remove the probe file:

```bash
rm src/graph/_tmp_probe.py
```

## 5. Verify FR-006 — missing docstring fails the build

```bash
cat > src/graph/_tmp_probe.py <<'EOF'
def undocumented(y: int) -> int:
    return y
EOF

aetheris docs build
```

**Expected**: exit `3`, the failure names `src/graph/_tmp_probe.py` and the rule, and the
build stops at gate 1. Clean up:

```bash
rm src/graph/_tmp_probe.py
```

## 6. Verify FR-008 — private names are excluded

Confirm no generated page in `site/reference/` renders a name beginning with `_`.

```bash
grep -rl '_tmp_probe' site/reference/ 2>/dev/null   # expect no output
```

## 7. Verify the accessibility gates

Two gates, one per rule class. Both are hermetic — neither needs network access.

### 7a. Structural gate

```bash
npx html-validate "site/**/*.html" --formatter stylish
```

**Expected**: exit `0` on a clean build. Covers alt text, labels, ARIA misuse patterns,
invalid nesting, duplicate ids/attrs.

### 7b. Rendered gate — contrast and keyboard focus

```bash
python scripts/check_a11y_rendered.py site/
```

**Expected**: exit `0`. This is the bespoke Playwright checker (Apache-2.0) that computes
colour contrast and verifies keyboard focus order and focus visibility on the rendered
pages — the rules static analysis cannot reach.

**Covered rule ids** are listed in the checker itself, so coverage is auditable rather
than assumed. **Read that list before trusting SC-007**: this gate covers fewer rules than
a mature engine would, and that is the accepted trade-off of refusing `axe-core` (MPL-2.0).

### 7c. Prove both gates fail closed (critical)

A checker that silently passes is worse than no checker. Create a deliberately bad
fixture and confirm **both** gates report it:

```bash
mkdir -p /tmp/a11y-fixture
cat > /tmp/a11y-fixture/bad.html <<'EOF'
<html><body>
  <img src="x.png">
  <h1>Title</h1>
  <h4>Skipped heading levels</h4>
  <input type="text" name="unlabelled">
  <div style="color:#aaaaaa;background:#ffffff">Low contrast text</div>
</body></html>
EOF

npx html-validate /tmp/a11y-fixture/bad.html
python scripts/check_a11y_rendered.py /tmp/a11y-fixture/
```

**Expected**: structural gate exits `1` with rules for missing alt text, heading order, and
missing label. Rendered gate exits `1` with a contrast violation.

**If either gate exits `0` on this fixture, that gate is broken and MUST NOT be trusted.**
This is the exact fail-open behaviour observed during research in a permissively-licensed
alternative (`research.md` §C), which is why it was rejected.

## 8. Verify VR-5 — secret scan

```bash
grep -rE '(api[_-]?key|token|secret|password)\s*[:=]\s*["'"'"'][A-Za-z0-9]{16,}' site/ && exit 1 || echo "no secrets found"
```

**Expected**: `no secrets found`. Any match fails the build, because the site is public
(FR-017).

## 9. Verify FR-011/FR-012 — deployment and failure isolation

Push a trivial documentation change to `main` and observe:

```bash
git add docs/ && git commit -m "docs: trivial change" && git push origin main
```

**Expected**:
- The `docs` workflow runs and publishes; the change is visible at the Pages URL within
  5 minutes (SC-003).
- The commit reports a successful deployment check.

Then force a failure to prove isolation (FR-012):

```bash
git revert --no-edit HEAD && git push origin main
```

**Expected**: the failing run publishes nothing, and the previously published site keeps
serving. A failed build must never produce a partial deploy.

## 10. Verify FR-010 — build on every push and PR

Open a pull request with a documentation change.

**Expected**: the CI workflow builds and runs all gates, reporting pass/fail on the PR,
but does **not** publish (branches other than `main` are verify-only).

---

## Success Criteria Cross-Reference

| SC | Verification | Target |
|---|---|---|
| SC-001 | §3 — click depth to a deep module | ≤ 3 clicks |
| SC-002 | §4 — zero missing-docstring errors on a clean build | 100% of public entities |
| SC-003 | §9 — time from push to visible change | < 5 minutes |
| SC-004 | §1 — timed local build | < 60 seconds |
| SC-005 | §9 — forced failure leaves prior site live | 100% of failures |
| SC-006 | §0–§1 — single documented command | No extra setup |
| SC-007 | §7a + §7b — both accessibility gates pass with zero violations | ⚠️ **Verify the covered-rule list first** — see the note below |

## ⚠️ SC-007: verify, don't assume

Spec SC-007 requires zero WCAG 2.1 Level AA violations. The gap between "the gates pass"
and "AA is satisfied" is **rule breadth**: the bespoke Playwright checker covers the rules
it was written for, not all ~90 that a mature engine would check.

Before marking SC-007 as passing, read the covered-rule list in
`scripts/check_a11y_rendered.py` and confirm it addresses the AA success criteria that
matter for a documentation site — contrast (1.4.3), keyboard (2.1.1), focus order (2.4.3),
focus visible (2.4.7), link purpose (2.4.4), and headings/labels (2.4.6). Rules outside
that list are **unverified, not compliant**.

The alternative — `axe-core`, the engine with complete coverage — is MPL-2.0 and
forbidden by Principle I. The decision to accept this breadth gap was made deliberately on
2026-10-10; see `plan.md` Constitution Check and `research.md` §C.

## Deferred items

- Contrast verification requires either a constitution amendment permitting `axe-core`, or
  a palette-based contrast lint against the documented colours.
- CLI reference generation from `aetheris --help` output is deferred (see `data-model.md` §3).
