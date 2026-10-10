# Phase 0 Research: Automated Documentation Site & CI/CD

**Date**: 2026-10-10 | **Spec**: [`./spec.md`](./spec.md) | **Plan**: [`./plan.md`](./plan.md)

Every unknown in Technical Context was resolved by targeted research against the
constitution's binding constraint: **permissive licenses only (MIT / Apache-2.0 / BSD /
ISC), no paid tiers, self-hosted, zero lock-in.** Decisions follow the
*Decision → Rationale → Alternatives considered* format.

Licenses below were verified against each project's own `LICENSE` file and published
package metadata, not inferred from project descriptions.

---

## A. API Reference Generator

### Decision
**`mkdocstrings[python]`** (ISC) with **`mkdocs-gen-files`** + **`mkdocs-literate-nav`** +
**`mkdocs-section-index`** (all MIT), driven by a single `scripts/gen_api.py` that walks
the source tree.

### Rationale
The only mature, actively maintained, MkDocs-native option that parses **Google-style**
docstrings natively and renders signatures, typed parameters, and return types. The
generation script means no page or navigation entry is ever hand-written, which directly
satisfies FR-004 and FR-009. The whole stack is ISC + MIT — no lock-in, no SaaS, optional
extras only.

### Alternatives considered

| Candidate | License (verified) | Why rejected |
|---|---|---|
| **`pdoc`** | **MIT-0** | Excellent Google-style support and full auto-discovery, but it emits **its own HTML site** — it cannot produce MkDocs pages, so it fails the "MkDocs + Material theme" requirement. Best runner-up. |
| **`pdoc3`** | **AGPL-3.0** | **Disqualified** — copyleft |
| **Sphinx + autodoc + napoleon + myst-parser** | BSD-2-Clause / MIT | Powerful, but it is not a MkDocs-Material site; requires manual `automodule` directives, which breaks the no-hand-written-pages requirement |
| **`pydoc-markdown`** | MIT | Unmaintained since 2023; requires a manual module list |
| **`lazydocs`** | MIT (verified — *not* GPL) | Unmaintained since 2021; emits docstrings as prose rather than structured parameters |
| **`mkdocs-autoapi`** | MIT | Low maturity (0.4.1); watch for future |
| **`mkdocs-api-autonav`** | BSD-3-Clause | Viable companion, but redundant when the generation script writes nav directly |
| **`mkdocs-api-autodoc`** | — | **Does not exist on PyPI** (404) |

### Generator configuration

```yaml
plugins:
  - search
  - gen-files:
      scripts: [scripts/gen_api.py]
  - literate-nav
  - section-index
  - mkdocstrings:
      handlers:
        python:
          paths: [src]              # package root is literally `src`
          options:
            docstring_style: google
            show_source: false
            show_signature_annotations: true
            show_if_no_docstring: false   # never render an undocumented entity
            filters: ["!^_"]               # exclude private/protected/special
            members_order: source
            show_submodules: true
```

---

## B. Failing the Build on a Missing Docstring (FR-006)

### Decision
**`ruff`** (MIT) D-rules, run as a separate gate before `mkdocs build --strict`.

```bash
ruff check --select D100,D101,D102,D103,D104,D105,D106,D107 src
mkdocs build --strict
```

### Rationale
Neither `mkdocstrings` nor `griffe` can fail a build on a missing docstring. `griffe check`
compares API *breaking changes*, which is a different thing entirely. `ruff`'s D1xx rules
(`undocumented-public-*`) fail the run with a non-zero exit, are **public-only** — they skip
underscore-prefixed names automatically, matching the generator's `filters: ["!^_"]`
exactly — and `ruff` is already a project dev dependency, so this adds no new tool.

### Alternatives considered

| Candidate | License (verified) | Why rejected |
|---|---|---|
| Generator's `show_if_no_docstring: false` | — | Silently **hides** undocumented entities. That is the opposite of FR-006: the API reference would look complete while growing silently staler |
| `griffe-cli` `griffe check` | ISC | Checks API breaking-change diffs, not docstring presence |
| **`pydocstyle`** | MIT | Correct tool, but unmaintained since 2023. Retained as fallback only |
| **`interrogate --fail-under 100`** | MIT | Presence-only metric with good `--ignore-private` support. Useful as a *coverage score* in reporting, but weaker than ruff's structured D-rules as a gate |

---

## C. Accessibility Gate (FR-019 / FR-020 / SC-007) — **resolved, option D**

### Decision (2026-10-10, owner decision)
**Two permissively-licensed gates**, preserving Principle I unchanged:

| Layer | Tool | License | Covers |
|---|---|---|---|
| Structural / static | `html-validate` | MIT | alt text, labels, ARIA misuse patterns, invalid nesting, duplicate ids/attrs |
| Rendered / dynamic | bespoke **Playwright** checker | Apache-2.0 | computed colour contrast, keyboard focus order, focus visibility |

### Rationale
`html-validate` is the only verified tool that is simultaneously permissive, **hermetic**
(pure static parsing — no browser, no CDN, no network), fail-closed, and maintained. Its
gap is exactly the rules that require rendered layout — contrast and focus — and Playwright
(Apache-2.0) drives a real browser, making both computable without touching an MPL
dependency.

Both gates fail closed and both are hermetic, so neither can silently pass when broken —
unlike the alternative discussed below, which was observed doing exactly that.

### Why the alternatives were rejected

| Option | License | Outcome |
|---|---|---|
| **A** — use `axe-core` | **MPL-2.0** | Rejected: would require amending Principle I for a **direct** dependency |
| **B** — weaken the spec to the `html-validate` subset | MIT | Rejected: discards the AA claim rather than meeting it |
| **C** — vendor IBM `accessibility-checker`'s engine | Apache-2.0 | Rejected **on reliability, not licensing**: it fetches its rule engine from a public CDN at runtime, and was observed **failing open** — reporting `1 of 1 passed` with **exit code 0** on a page with missing alt text, skipped heading levels, unlabelled inputs, and low contrast. It also silently auto-discovers `.achecker.yml` from the working directory, overriding an explicit `--config`, and sends telemetry unless opted out |
| **D** — hybrid, bespoke Playwright rules | MIT + Apache-2.0 | **Chosen** |

### Alternatives considered (tooling, with verified licenses)

| Candidate | License | AA coverage | Why not chosen |
|---|---|---|---|
| **`axe-core`** + `@axe-core/playwright` | **MPL-2.0** | Complete (~90 rules) | **Disqualified by Principle I** — the only complete AA engine, and it is copyleft |
| **`pa11y` / `pa11y-ci`** | **LGPL-3.0-only** | Good | Not permissive — disqualified by the same principle that excludes GPL |
| **`lighthouse` / `@lhci/cli`** | Apache-2.0 | Good | Permissive itself, but declares a direct dependency on **`axe-core` (MPL-2.0)** and executes it in-page |
| **`accessibility-checker`** (IBM) | Apache-2.0 | WCAG 2.0/2.1/2.2 A+AA | Best permissive coverage, rejected for the CDN dependency and fail-open behaviour above |
| `cypress-axe`, `jest-axe`, `axe-webdriverjs` | MPL-2.0 | — | Every axe wrapper inherits MPL-2.0 |
| AChecker, WAVE, ANDI, Siteimprove | — | — | Web UIs / SaaS; cannot be driven headlessly from CI |
| **`html-validate`** | MIT | Structural subset | Chosen as one half of the pair |
| **Playwright** | Apache-2.0 | Driver only, enables bespoke rules | Chosen as the other half |

### The honest bottom line, and how it is now closed

> No single permissively-licensed tool fully covers WCAG 2.1 AA in headless CI.

That remains true after this decision. What changed is that the gap is now **closed by
composition** rather than by weakening either the constitution or the spec: `html-validate`
handles structure, the bespoke Playwright checker handles the rendered rules. The residual
gap is rule *breadth* — a bespoke checker covers fewer cases than axe's ~90 — which is
mitigated, not eliminated, by:

1. A deliberately-bad fixture page that must fail **every** gate (quickstart §7).
2. An explicit list of covered WCAG rule ids in the checker, so coverage is auditable
   rather than assumed.
3. Recording the decision in `constitution-amendments.md` under **non-amendments**, so a
   future "just use axe-core" proposal has the counter-evidence on record.

---

## D. GitHub Pages Deployment

### Decision
Standard Actions deployment to GitHub Pages, triggered on push to `main` only, with the
build and gates in the same job so a broken build cannot publish.

### Rationale
The repository already has a GitHub remote and a `main` branch, so Pages is available with
no additional infrastructure and no cost — consistent with Principle I. Building and gating
in the same job satisfies FR-012 (a failed build must not publish, and the previous version
must survive).

### Alternatives considered
- Manual deploy — fails FR-011 (must be automatic)
- Third-party static hosts — introduce a SaaS dependency, violating Principle I
- Deploy on every branch — explicitly excluded by the spec's edge case (main only)

---

## E. Decisions carried into `data-model.md` and `contracts/`

- The docstring convention that generated pages depend on, and the validation rules
  (VR-1…VR-4) are in [`data-model.md`](./data-model.md).
- The build command contract and the shape of a generated reference page are in
  [`contracts/`](./contracts/).
