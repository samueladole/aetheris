# Implementation Plan: Automated Documentation Site & CI/CD

**Branch**: `001-docs-site-cicd` | **Date**: 2026-10-10 | **Spec**: [`./spec.md`](./spec.md)

**Input**: Feature specification from `/specs/001-docs-site-cicd/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command; its definition describes the execution workflow.

## Summary

Build a static documentation site for Aetheris' existing public surface — its command-line
tool and importable Python library, with **no web service added** — using MkDocs with the
Material theme, auto-generate the API reference by parsing source docstrings, and publish
automatically to GitHub Pages on every push to main, gated by fail-closed CI.

The design is deliberately **generation-only**: API reference pages are produced from a
script that walks the source tree, so adding a module requires no manual page or navigation
entry (FR-004, FR-009). Missing docstrings fail the build via a lint gate rather than a
generator option, because the generator cannot express that check.

## Technical Context

**Language/Version**: Python 3.11+ (site generator config and docstring gate); Node.js 20
(a11y gate tool)

**Primary Dependencies**:
- Site: `mkdocs` (BSD-2-Clause), `mkdocs-material` (MIT)
- API reference: `mkdocstrings[python]` (ISC), `mkdocs-gen-files` (MIT),
  `mkdocs-literate-nav` (MIT), `mkdocs-section-index` (MIT)
- Docstring gate: `ruff` (MIT) D-rules — the mechanism that makes FR-006 fail the build
- Accessibility gates: `html-validate` (MIT) for structural rules, plus a bespoke
  **Playwright** checker (Apache-2.0) for computed contrast and keyboard focus
  (see Constitution Check — conflict V1 resolved)
- Tooling: Node.js 20 for both a11y gates; Python only for the site build and docstring gate

**Storage**: Generated static HTML under a build-output directory; source is Markdown plus
docstrings. No database, no runtime service.

**Testing**: `mkdocs build --strict` (fails on warnings); `ruff check --select D1xx`
(missing docstrings); `html-validate` over the built HTML (accessibility); a deliberate
fixture-page test proving the a11y gate fails closed.

**Target Platform**: GitHub Actions `ubuntu-latest` for CI/CD; local preview on macOS/Linux.

**Project Type**: Documentation site plus CI/CD workflows. Not a web service — confirmed
by clarification.

**Performance Goals**: SC-004 documentation build completes in under 60 seconds.

**Scale/Scope**: ~30 public modules across 7 packages. Site is small; no pagination or
search-index tuning anticipated.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Gate | Status |
|---|-----------|------|--------|
| I | Strict Open-Source Compliance | Every dependency MIT/Apache-2.0/BSD/ISC | ✅ PASS |
| II | Decoupled Modular Architecture | Docs feature adds no cross-layer imports; `src/` layer boundaries unchanged | ✅ PASS |
| III | Resilient Durable Execution | Docs build requires no provider credentials or external data source | ✅ PASS |
| IV | Comprehensive Observability & Evaluation | Documentation build failures are reported per-run; docstring coverage is measured | ✅ PASS |
| V | Specification-Driven Rigor | Spec (20 FR, 7 SC) → this plan → `/speckit.tasks` | ✅ PASS |

**Post-Phase-1 re-check: all five principles PASS.** The original Gate I conflict (below)
was resolved by owner decision on 2026-10-10 without weakening Principle I.

### Gate I conflict — RESOLVED (option D)

**V1 — accessibility verification vs. permissive licensing.** Spec FR-019/FR-020/SC-007
require WCAG 2.1 Level AA verified by automated checks that fail the build. The only
mature engine with complete AA coverage is **`axe-core` (MPL-2.0)**, which Principle I
forbids. The permissive alternatives each fell short: `html-validate` (MIT) computes no
contrast and cannot verify keyboard focus; IBM `accessibility-checker` (Apache-2.0) has
strong coverage but fetches its rule engine from a CDN and was observed failing open.

**Resolution — option D, chosen 2026-10-10:** keep Principle I strict and build AA
coership from two permissively-licensed parts:

| Layer | Tool | License | Covers |
|---|---|---|---|
| Structural / static | `html-validate` | MIT | alt text, labels, ARIA misuse patterns, invalid nesting, duplicate ids/attrs |
| Rendered / dynamic | bespoke **Playwright** checker | Apache-2.0 | computed colour contrast, keyboard focus order, focus visibility |

Playwright is Apache-2.0 and drives a real browser, so contrast and focus — precisely the
rules static analysis cannot reach — become computable. Both gates are hermetic (no
network) and fail closed, so a checker that breaks itself cannot silently pass.

**Why this over the alternatives:** it is the only option that preserves Principle I *and*
delivers real AA coverage. Option A (amending Principle I for `axe-core`) would weaken the
constitution for a **direct** dependency, not merely a transitive one. Option B would have
required weakening FR-019. Option C was rejected on reliability grounds, not licensing.

**Accepted trade-off, stated plainly:** bespoke rules cover a smaller set than axe's ~90,
and can miss rules a mature engine would catch. Mitigation is a deliberately-bad fixture
page that must fail every gate (quickstart §7) plus an explicit list of covered rule ids
in the checker, so coverage is auditable rather than assumed.

## Project Structure

### Documentation (this feature)

```text
.specify/specs/001-docs-site-cicd/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── docs-build.md            # The `aetheris docs` command contract
│   └── api-reference-page.md    # What a generated reference page must contain
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
docs/
├── index.md                 # Landing page
├── installation.md          # Install & first run
├── usage/
│   └── cli.md               # CLI reference (FR-001)
├── reference/               # GENERATED — never hand-edited
│   └── SUMMARY.md           # written by scripts/gen_api.py
├── stylesheets/
│   └── extra.css            # palette overrides for contrast lint target
└── overrides/               # Material theme overrides, only if needed

scripts/
└── gen_api.py               # Walks src/, emits reference/*.md (FR-004, FR-009)

mkdocs.yml                   # Site + plugin configuration
.github/workflows/
├── ci.yml                   # extended: docs build + gates (FR-010)
└── docs.yml                 # build + deploy to Pages on main (FR-011)
```

**Structure Decision**: Single documentation site at the repository root. The API reference
is **generated into the build output, not committed** — `reference/` is a build artifact,
which is what keeps it from drifting (FR-005). Only `scripts/gen_api.py` is the authored
source of truth for what gets documented.

Layer boundaries are untouched: the generator walks the filesystem and never imports
`src/` modules, so Principle II is preserved by construction rather than by linting.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

**No constitutional violations remain.** The original Gate I conflict (V1) was resolved by
owner decision on 2026-10-10 without amending Principle I — see the Constitution Check
section for the resolution and its accepted trade-off.

The entries below record complexity that exists for non-constitutional reasons, so a future
reviewer can see why a simpler approach was not taken.

| Complexity | Why needed | Simpler alternative rejected because |
|---|---|---|
| Two accessibility gates instead of one | No single permissively-licensed tool covers both static and rendered WCAG rules | `axe-core` covers everything in one tool but is MPL-2.0; `html-validate` alone misses contrast and keyboard focus |
| Bespoke Playwright rule set rather than a mature engine | Principle I forbids the only complete engine (MPL-2.0) | Bespoke rules cover fewer cases than axe's ~90 — mitigated by a deliberately-bad fixture that must fail every gate, and an explicit covered-rule list so coverage is auditable |
| Docstring gate uses `ruff` D-rules rather than the generator | Neither mkdocstrings nor griffe can fail a build on a missing docstring; griffe's `check` compares API breaking changes, not docstring coverage | `show_if_no_docstring: false` would silently *hide* undocumented entities — the opposite of FR-006 |
| `mkdocs-gen-files` pinned to an older minor | 0.6.x depends on `properdocs` (BSD-2-Clause, still permissive) and pins `mkdocs<=1.6.1` | Pinning `0.5.0` avoids the extra dependency and the mkdocs pin, at the cost of an older release |
