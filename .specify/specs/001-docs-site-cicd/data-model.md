# Data Model: Automated Documentation Site & CI/CD

**Date**: 2026-10-10 | **Spec**: [`./spec.md`](./spec.md) | **Plan**: [`./plan.md`](./plan.md)

A documentation site has a small data model: the entities are documentation artifacts
rather than domain objects. This file defines them, the docstring convention the generator
depends on, and the validation rules that make the spec's requirements testable.

---

## 1. Entities

### 1.1 DocumentationPage

An authored Markdown source file.

| Field | Type | Rule |
|---|---|---|
| `src_path` | relative path | Under `docs/`, extension `.md` |
| `title` | string | First heading; required |
| `nav_position` | integer or implicit | From the navigation tree; never hand-edited for generated pages |
| `authored` | boolean | `true` for hand-written pages; `false` for generated reference pages |
| `public` | boolean | Must be `true` for every page — the site is publicly readable (FR-017) |

**Lifecycle**: `authored → built → published`. Generated pages enter at `built`; they are
never authored and never committed.

**Uniqueness**: `src_path`.

### 1.2 ApiReferencePage

A **generated** page describing one source module.

| Field | Type | Rule |
|---|---|---|
| `module_path` | dotted path | Derived from the file path relative to `src/`, e.g. `config.settings` |
| `output_path` | relative path | `docs/reference/<same relative path>.md`, relative to the build output |
| `generated` | `true` | Constant — this entity is never hand-authored (FR-005) |
| `entities` | list of PublicEntity | Everything the module documents |

**Identity**: `module_path` — one page per module.

**Derivation**: written by `scripts/gen_api.py` during the build. It walks `src/**/*.py`,
skips `__pycache__` and `__init__.py`, and emits one page per module containing a single
`::: <module_path>` directive.

### 1.3 PublicEntity

Any module, class, or function that the generator renders in the reference.

| Field | Type | Rule |
|---|---|---|
| `qualified_name` | dotted path | e.g. `orchestration.workflows.start` |
| `kind` | enum | `module` \| `class` \| `function` \| `method` \| `attribute` |
| `signature` | string | Rendered from the source; must include parameter names and type annotations |
| `docstring` | Google-style string | **Required for all public entities** (VR-1) |
| `is_public` | boolean | `false` if the name begins with `_` |

**Uniqueness**: `qualified_name` within the documented surface.

### 1.4 DocstringConvention

The convention a docstring must satisfy for the generator to extract structure.

| Field | Type | Rule |
|---|---|---|
| `style` | enum | `google` — the convention used throughout the codebase |
| `summary_line` | string | One line, required, first line of the docstring |
| `sections` | enum set | `Args`, `Returns`, `Raises`, `Yields`, `Attributes`, `Examples` |
| `params_documented` | boolean | Every parameter must appear in `Args` (VR-2) |

### 1.5 BuildRun

One execution of the documentation build.

| Field | Type | Rule |
|---|---|---|
| `trigger` | enum | `push` \| `pull_request` |
| `branch` | string | Publication only occurs when `branch == "main"` |
| `gates` | list of GateResult | Docstring gate, build gate, a11y gate |
| `outcome` | enum | `success` \| `failed` |
| `published` | boolean | `true` only when `outcome == success` **and** `branch == "main"` |
| `duration_seconds` | number | Must be < 60 (SC-004) |

**State transitions**:
```
PENDING → GATES_RUNNING → BUILDING → PUBLISHED      (main, all gates pass)
PENDING → GATES_RUNNING → BUILDING → VERIFIED_ONLY  (non-main, all gates pass)
PENDING → GATES_RUNNING → FAILED                    (any gate or build fails)
```
A `FAILED` run on `main` never reaches `PUBLISHED`, and the previously published site
remains serving (FR-012).

### 1.6 GateResult

| Field | Type | Rule |
|---|---|---|
| `name` | enum | `docstrings` \| `build` \| `accessibility` |
| `passed` | boolean | Must be `true` for publication |
| `violations` | list of Violation | Empty when `passed` |
| `actionable` | boolean | Each violation must identify a file, page, or rule (VR-4) |

### 1.7 DeploymentTarget

| Field | Type | Rule |
|---|---|---|
| `url` | URL | Provided by the repository host's static-site publishing, rooted at the project path |
| `visibility` | enum | `public` — confirmed by clarification |
| `current_version` | string | Whatever the last successful `main` build produced |

---

## 2. Validation Rules

| ID | Rule | Enforced by |
|---|---|---|
| VR-1 | Every public module, class, and function has a docstring | `ruff` D100–D107 gate |
| VR-2 | Every parameter of a public callable appears in its docstring `Args` section | Generator rendering + review |
| VR-3 | No generated page is ever hand-edited or committed | `.gitignore` + generator is the sole writer |
| VR-4 | Every gate violation identifies a file, page, or rule id | Gate configuration |
| VR-5 | No credential, key, or non-public value appears in any published page (FR-017) | Secret-pattern scan in CI |
| VR-6 | Publication occurs only on `main` with all gates passing | Workflow trigger + job dependency |
| VR-7 | A failed build leaves the previous published site intact (FR-012) | Deploy is a separate downstream job that requires build success |
| VR-8 | Accessible-output requirements (FR-019) — enforced by two gates: `html-validate` (structural) and a bespoke Playwright checker (computed contrast, keyboard focus) | Both gates in the docs build; see `plan.md` Constitution Check |

---

## 3. The Published Surface

What the site exposes, given clarification #1 (no web service):

| Surface | Form | Source |
|---|---|---|
| Landing page | Authored Markdown | `docs/index.md` |
| Installation & usage | Authored Markdown | `docs/installation.md` |
| CLI reference | Authored Markdown, mirroring `aetheris --help` output | `docs/usage/cli.md` |
| Library API reference | **Generated** from docstrings | `scripts/gen_api.py` → `mkdocstrings` |

The CLI reference is authored rather than generated because the CLI's help text is a
hand-maintained argparse surface today; automating it would couple the docs build to the
CLI's implementation and is explicitly deferred.

---

## 4. Explicitly Out of Scope

- A web service of any kind (clarification #1)
- Publishing `.specify/` design records (clarification #2)
- Versioned / release-pinned documentation snapshots
- Translated or localised content
- Programmatic API search beyond the site's built-in search
