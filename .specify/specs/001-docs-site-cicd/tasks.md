# Tasks: Automated Documentation Site & CI/CD

**Input**: Design documents from `/specs/001-docs-site-cicd/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included. The feature specification makes automated verification a hard
requirement (FR-010 build gate, FR-020 accessibility gate, VR-1 docstring gate), so test
tasks exist to prove the gates *fail closed* — a checker that silently passes is worse than
no checker, which is the failure mode observed during research in a rejected alternative.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/`, `docs/`, `scripts/`, `.github/workflows/` at repository root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and tooling for the documentation feature.

- [X] T001 [P] Add a `docs` optional-dependency group to `pyproject.toml` pinning `mkdocs`, `mkdocs-material`, `mkdocstrings[python]`, `mkdocs-gen-files`, `mkdocs-literate-nav`, and `mkdocs-section-index`, with a comment recording each package's verified license (BSD-2-Clause / MIT / ISC)
- [X] T002 [P] Create the documentation source tree at the repository root: `docs/index.md`, `docs/installation.md`, `docs/usage/cli.md`, `docs/stylesheets/extra.css`, and `docs/overrides/`, leaving `docs/reference/` to be generated
- [X] T003 [P] Create `package.json` at the repository root declaring `html-validate` and `@playwright/test` as devDependencies, each with a comment recording its verified license (MIT / Apache-2.0), plus npm scripts `a11y:static` and `a11y:rendered`
- [X] T004 [P] Add `site/`, `docs/reference/*.md`, and `.playwright-artifacts/` to `.gitignore`, with a comment noting that generated reference pages are build output, never committed (VR-3)

**Checkpoint**: Tooling is installable via `pip install -e ".[docs]"` and `npm install`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T005 Implement the API reference generator in `scripts/gen_api.py`: walk `src/**/*.py`, skip `__pycache__` and `__init__.py`, derive the dotted module path relative to `src/`, emit one `docs/reference/<path>.md` containing a single `::: <module>` directive, and append each page to a generated `docs/reference/SUMMARY.md` for `literate-nav` (FR-004, FR-009)
- [X] T006 Create `mkdocs.yml` at the repository root with `site_name`, `site_url` set to the repository's Pages URL, `theme: material`, and plugins `search`, `gen-files` (scripts `[scripts/gen_api.py]`), `literate-nav`, `section-index`, and `mkdocstrings` with `paths: [src]`, `docstring_style: google`, `show_source: false`, `show_if_no_docstring: false`, `filters: ["!^_"]`, and `strict: true`
- [X] T007 [P] Configure the docstring gate in `pyproject.toml` by adding `"D"` to `[tool.ruff.lint] select` and a `[tool.ruff.lint.pydocstyle]` section with `convention = "google"`, so `ruff check --select D100,D101,D102,D103,D104,D105,D106,D107 src` fails on any undocumented public entity (FR-006, VR-1)
- [X] T008 [P] Extend `.github/workflows/ci.yml` with a `docs` job that installs the `docs` extra, runs `ruff check --select D100,D101,D102,D103,D104,D105,D106,D107 src`, runs `mkdocs build --strict`, and runs `npx html-validate "site/**/*.html"` (FR-010)
- [X] T009 Implement the `aetheris docs build`, `aetheris docs serve`, and `aetheris docs open` subcommands in `src/cli/main.py` per `contracts/docs-build.md`, wiring the four gates in order (docstrings → secret scan → build → accessibility) and exiting `3` on any gate failure, with `serve` binding to localhost only

**Checkpoint**: `aetheris docs build` runs all four gates and produces `site/`.

---

## Phase 3: User Story 1 — Browse the published documentation (Priority: P1) 🎯 MVP

**Goal**: A visitor arriving at the site can read what the project does, install and run it,
and reach any public module's API reference — without reading source code or cloning.

**Independent Test**: Serve the built site, open the root URL, navigate to the API reference
for `graph.repository`, and confirm it renders with signatures, parameters, and return types
— reaching it in 3 clicks or fewer, with both accessibility gates passing.

### Tests for User Story 1 (write FIRST; ensure they FAIL before implementation) ⚠️

- [X] T010 [P] [US1] Write the deliberately-inaccessible fixture at `tests/fixtures/docs/a11y-bad.html` containing missing alt text, skipped heading levels, an unlabelled input, and low-contrast text, plus a pytest test in `tests/contract/test_a11y_gates.py` asserting that `html-validate` exits non-zero on it and that `scripts/check_a11y_rendered.py` exits non-zero on it (VR-4)
- [X] T011 [P] [US1] Write the fail-closed regression test in `tests/contract/test_a11y_gates.py` asserting that a checker that cannot execute must not report success, guarding against the fail-open behaviour documented in `research.md` §C

### Implementation for User Story 1

- [X] T012 [US1] Implement the rendered accessibility checker in `scripts/check_a11y_rendered.py` using Playwright to load each built HTML page and verify computed colour contrast, keyboard focus order, and focus visibility, exiting non-zero on violation and listing the covered WCAG rule ids in its module docstring so breadth is auditable (FR-019, FR-020, VR-8)
- [ ] T013 [US1] Implement the secret-scan gate in `src/cli/main.py` that scans the built `site/` directory for credential-shaped patterns and exits non-zero on a match, because the site is publicly readable (FR-017, VR-5)
- [ ] T014 [P] [US1] Author the landing page in `docs/index.md` covering what the project does, its current status, and links to installation, CLI usage, and the API reference
- [ ] T015 [P] [US1] Author the installation and first-run guide in `docs/installation.md` covering environment setup, the `docs` extra, and a dry-run invocation
- [ ] T016 [P] [US1] Author the CLI reference in `docs/usage/cli.md` documenting each `aetheris` subcommand with its flags and exit codes, mirroring `contracts/cli.md`
- [ ] T017 [US1] Run the §1–§3 scenarios in `.specify/specs/001-docs-site-cicd/quickstart.md` and record the measured build duration against SC-004 and the click depth to `graph.repository` against SC-001 in `docs/validation.md`

**Checkpoint**: User Story 1 is fully functional — the site builds, renders every page, passes both accessibility gates on a clean build, and fails closed on the bad fixture.

---

## Phase 4: User Story 2 — Regenerate API reference automatically (Priority: P2)

**Goal**: A maintainer changes a docstring or adds a module, pushes, and the deployed
reference reflects it — with no hand-editing of any documentation or navigation page.

**Independent Test**: Add a temporary public module, rebuild, and confirm its page appears
with signature and docstring without any manual edit; then remove its docstring and confirm
the build fails naming the file and rule.

### Tests for User Story 2 (write FIRST; ensure they FAIL before implementation) ⚠️

- [ ] T018 [P] [US2] Write the generation test in `tests/contract/test_api_reference.py` asserting that a newly added public module produces a page in the built site with its signature, parameters, and return type rendered, and that no page or navigation file was manually edited (FR-004, FR-005)
- [ ] T019 [P] [US2] Write the missing-docstring test in `tests/contract/test_api_reference.py` asserting that removing one public docstring fails gate 1 with the offending path and rule named (FR-006, VR-1)

### Implementation for User Story 2

- [ ] T020 [US2] Verify module coverage by asserting in `tests/contract/test_api_reference.py` that every module file under `src/` has a corresponding page in the built site, so no layer can be silently omitted (FR-009)
- [ ] T021 [US2] Add a private-name exclusion assertion to `tests/contract/test_api_reference.py` confirming no generated page renders an entity whose name begins with `_`, verifying the `filters: ["!^_"]` configuration works without a per-module allowlist (FR-008)
- [ ] T022 [US2] Add a determinism assertion to `tests/contract/test_api_reference.py` checking that two consecutive `aetheris docs build` runs over unchanged source produce byte-identical output in `site/reference/`
- [ ] T023 [US2] Run the §4–§6 scenarios in `.specify/specs/001-docs-site-cicd/quickstart.md`, remove every temporary probe file created from `src/graph/`, and record the result in `docs/validation.md`

**Checkpoint**: User Stories 1 AND 2 work independently — the API reference regenerates itself and cannot silently drift.

---

## Phase 5: User Story 3 — Deploy automatically on push (Priority: P3)

**Goal**: A change merged to main updates the live site within minutes, with no manual
publishing step, and a broken build never publishes a partial site.

**Independent Test**: Push a trivial documentation change to main and confirm the site
updates; then force a failure and confirm the previously published site keeps serving.

### Tests for User Story 3 (write FIRST; ensure they FAIL before implementation) ⚠️

- [ ] T024 [P] [US3] Write the branch-filter test in `tests/contract/test_docs_workflow.py` asserting that `.github/workflows/docs.yml` triggers on pushes to `main` only and not on pull requests or other branches
- [ ] T025 [P] [US3] Write the publish-isolation test in `tests/contract/test_docs_workflow.py` asserting that the deploy job declares `needs:` on the build-and-gate job, so a failed build cannot reach the publish step (FR-012, VR-7)

### Implementation for User Story 3

- [ ] T026 [US3] Create `.github/workflows/docs.yml` with a `build` job that installs the `docs` extra, runs all four gates in order, and uploads the built site as a Pages artifact, restricted to pushes on `main`
- [ ] T027 [US3] Add a `deploy` job to `.github/workflows/docs.yml` guarded by `needs: build` and `if: github.ref == 'refs/heads/main'`, deploying the uploaded artifact to GitHub Pages with `id-token: write` and `pages: write` permissions
- [ ] T028 [US3] Add the `AETHERIS_TRACE_STDOUT`-free, network-free documentation build note to `.github/workflows/docs.yml`, confirming the build requires no provider credentials and no network access (FR-013, FR-014)
- [ ] T029 [US3] Run the §9–§10 scenarios in `.specify/specs/001-docs-site-cicd/quickstart.md` to verify publication on main, verify-only behaviour on a pull request, and that a forced build failure leaves the previously published site intact, recording each result in `docs/validation.md` (FR-011, FR-012, SC-003, SC-005)

**Checkpoint**: All three user stories work — the site publishes automatically and failures never produce a partial deploy.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories, plus the gates that keep the feature honest.

- [ ] T030 [P] Update `README.md` at the repository root to link the published documentation site and add a short "Documentation" section explaining how to build and preview locally via `aetheris docs serve`
- [ ] T031 [P] Add the newly permitted documentation dependencies to the license gate by running `aetheris check-licenses --lenient` and recording that `mkdocs`, `mkdocs-material`, `mkdocstrings`, `mkdocs-gen-files`, `mkdocs-literate-nav`, `mkdocs-section-index`, `html-validate`, and `@playwright/test` report no *unaccepted* violation, adding any newly-flagged package to `src/cli/licenses.py` with its justification (FR-015)
- [ ] T032 [P] Document the contrast palette in `docs/stylesheets/extra.css` with the measured foreground/background pairs and their contrast ratios, so the rendered accessibility gate's thresholds are justified rather than guessed
- [ ] T033 [P] Record the covered WCAG rule ids in `docs/usage/accessibility.md`, stating plainly which rules the bespoke checker verifies and that rules outside that list are unverified rather than compliant, per the accepted trade-off in `plan.md` Complexity Tracking
- [ ] T034 Run the full `quickstart.md` validation from §0 through §10 and record the outcome, confirming SC-001 through SC-007 and explicitly noting that SC-007 must be verified against the covered-rule list rather than assumed

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately. All four tasks are `[P]`.
- **Foundational (Phase 2)**: Depends on Setup completion — **BLOCKS all user stories**.
  T007 and T008 are `[P]`; T005 must precede T006, because `mkdocs.yml` references
  `scripts/gen_api.py` and would fail to build without it.
- **User Stories (Phases 3–5)**: All depend on Foundational completion.
  - **US1 (Phase 3, P1)** first: it authors the content and builds both accessibility gates.
  - **US2 (Phase 4, P2)** depends on US1's rendered checker, because its determinism and
    coverage assertions run against the built site.
  - **US3 (Phase 5, P3)** depends on US1 and US2, since the deploy job must run the
    complete gate set before publishing.
- **Polish (Phase 6)**: Depends on all desired user stories being complete.

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational — no dependencies on other stories.
- **User Story 2 (P2)**: Depends on US1 for the built-site assertions; independently
  testable once the rendered checker exists.
- **User Story 3 (P3)**: Depends on US1 and US2 so that every gate is in place before the
  first publish; the workflow itself is otherwise independent.

### Within Each User Story

- Tests MUST be written first and confirmed FAILING before implementation.
- Fixtures before the checks that consume them.
- Generation before assertions over generated output.
- Workflow configuration before the verification push.

### Parallel Opportunities

- All four Phase 1 Setup tasks are `[P]`.
- In Phase 2, T007 and T008 are `[P]`; T005 must precede T006.
- In US1, the two test tasks are `[P]`, as are the three authored content pages
  (T014–T016). T010 and T011 are written first and will fail until T012 lands — that is
  the correct red-green-refactor sequence, so T012 follows its own tests.
- In US2, both test tasks are `[P]`; T020–T022 are independent assertions in the same file
  and can be written together.
- In US3, both test tasks are `[P]`; T026 must precede T027.

---

## Parallel Example: User Story 1

```bash
# Fixture and fail-closed guard first:
Task: "Write the deliberately-inaccessible fixture at tests/fixtures/docs/a11y-bad.html"
Task: "Write the fail-closed regression test in tests/contract/test_a11y_gates.py"

# Then the content pages in parallel:
Task: "Author the landing page in docs/index.md"
Task: "Author the installation and first-run guide in docs/installation.md"
Task: "Author the CLI reference in docs/usage/cli.md"
```

## Parallel Example: User Story 2

```bash
# Both generation assertions in parallel:
Task: "Write the generation test in tests/contract/test_api_reference.py"
Task: "Write the missing-docstring test in tests/contract/test_api_reference.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (**CRITICAL — blocks all user stories**)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: build the site, confirm navigation depth, both accessibility
   gates on a clean build, and both gates failing closed on the bad fixture
5. The site is now publishable and trustworthy, even before automation

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → **a browsable, accessible, gate-protected site (MVP)**
3. Add User Story 2 → Test independently → the reference can no longer silently drift
4. Add User Story 3 → Test independently → automatic publishing on main
5. Polish closes out with docs, license verification, and the palette record

### Why this order

US1 is the only story that produces something a visitor can use. US2 and US3 protect that
asset over time — regeneration stops the reference going stale, and deployment stops it
going unreachable. Building the gates before the deploy job matters: a publish pipeline
whose gates were added later has a window in which broken or inaccessible docs shipped.

---

## Notes

- `[P]` tasks = different files, no dependencies
- `[Story]` label maps task to a specific user story for traceability
- Each user story is independently completable and testable
- **Constitution note**: Principle I is unchanged by this feature. The WCAG 2.1 AA
  requirement is met by composing two permissively-licensed gates rather than amending the
  constitution to permit `axe-core` (MPL-2.0). See `plan.md` Constitution Check.
- **Accepted trade-off**: the bespoke Playwright checker covers fewer rules than a mature
  engine's ~90. T033 makes the covered list explicit so breadth is auditable, and T011
  guards against the fail-open mode that disqualified a permissive alternative during
  research.
- `tests/fixtures/docs/a11y-bad.html` is a **permanent** fixture, not a temporary one — it
  is what keeps the accessibility gates honest.
- Commit after each task or logical group
- Stop at any checkpoint to validate the story independently
