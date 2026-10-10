# Constitutional Amendment Drafts

**Status**: DRAFT — not ratified. Per constitution Governance, amendments require
"documentation, approval, and migration plans".

These amendments were surfaced by the implementation license gate
(`aetheris check-licenses`) during `/speckit.implement`, which found that the
stack selected in `plan.md` transitively depends on packages outside the
permissive set that Principle I permits.

Amendment A-001 is a **known, accepted deviation** pending ratification. Until
this is ratified, the CI gate runs in `--lenient` mode by recorded decision
(see `.github/workflows/ci.yml`).

---

## A-001 — Transitive dependencies excluded from Principle I's license scope

> **Amendment**: Principle I's license restriction applies to **direct**
> dependencies — packages declared in `pyproject.toml` `dependencies`. Packages
> pulled in transitively by a permitted direct dependency are exempt, provided
> that:
>
> 1. The direct dependency's own license is permissive.
> 2. A `KNOWN_VIOLATIONS` record exists for each non-permissive transitive
>    dependency, with a reason it cannot be avoided.
> 3. The gate keeps reporting them — this amendment narrows the *enforcement
>    scope*, it does not silence the check.

### Rationale

The four offenders are structurally unavoidable for the required capabilities:

| Package | License | Required by | Why unavoidable |
|---|---|---|---|
| `psycopg` / `psycopg-binary` | LGPL-3.0-only | DBOS 3.2.0 (hard dependency) | DBOS is the only permissively-licensed, embeddable durable-execution library (research.md §A). Its Postgres system-DB support requires psycopg; SQLite does not avoid it because psycopg is a core, not optional, dependency. |
| `certifi` | MPL-2.0 | `requests`, `httpx`, `httpcore`, `evidently` | The de-facto CA bundle for TLS verification in Python. Every HTTP client in the ecosystem depends on it; hand-rolling TLS trust is strictly worse security. |
| `huey` | undetermined | MLflow 3.17.0 | Task queue for MLflow's model-serving components, which Aetheris does not use. |
| `pathspec` | MPL-2.0 | nothing (orphaned) | Present in the resolved tree with no reverse dependency found; removable. |

The practical alternative — dropping DBOS and MLflow entirely — would force
re-specification of FR-001/FR-002/FR-003 (durable execution) and FR-019
(evaluation tracking), both of which are constitutional requirements (§III and
§IV), not conveniences.

### Constitutional tension

This amendment **weakens** Principle I, which is the principle most likely to be
cited to reject the project outright. It is proposed only because the strict
reading makes the mandated observability (§IV) and durability (§III)
requirements jointly unsatisfiable with a permissive stack. If this is
unacceptable, the correct resolution is to replace DBOS and MLflow, not to
quietly accept the violation.

**Owner decision required.** Recommended alternatives, in order of preference:

1. **Ratify A-001 as drafted** (scoped to *transitive* only, with mandatory
   disclosure).
2. Keep Principle I strict and **re-specify** FR-001–FR-003 as a hand-rolled
   SQLite state machine (research.md §A "build your own"), and FR-019 on a
   lighter tracker.
3. Keep Principle I strict **and** accept that the project cannot ship an
   evaluation backend, amending §IV instead.

### Migration plan (if ratified)

1. Add `KNOWN_VIOLATIONS` to `src/cli/licenses.py` listing the four packages
   with the reasons above.
2. Default the gate to `--strict` but exempt the listed packages, so new
   non-permissive dependencies still fail.
3. Re-check the list whenever `pyproject.toml` dependencies change.
4. Record the ratification date in the constitution's version footer.

---

## Non-amendments (recorded so they are not re-litigated)

### The docs-site accessibility gate (feature `001-docs-site-cicd`) — **no amendment needed**

A conflict arose during planning: spec FR-019/020 and SC-007 require WCAG 2.1 Level AA
verified by automated checks, but the only mature engine with complete AA coverage,
`axe-core`, is **MPL-2.0** and therefore forbidden by Principle I.

**Decision (2026-10-10):** Principle I is **unchanged**. AA coverage is assembled from two
permissively-licensed parts — `html-validate` (MIT) for structural rules, plus a bespoke
**Playwright** checker (Apache-2.0) for computed colour contrast and keyboard focus.

**Why this is recorded here:** the options considered included amending Principle I to
permit MPL-2.0 for `axe-core`. That was rejected, so Principle I remains exactly as
ratified. If a future contributor proposes `axe-core` on the grounds that "we needed it for
accessibility", this entry is the counter-evidence: AA is achievable without it.

**Accepted trade-off:** bespoke rules cover fewer cases than axe's ~90. Mitigated by a
deliberately-bad fixture page that must fail every gate, and an explicit list of covered
rule ids so coverage is auditable rather than assumed.

---

## A-002 — Remove the retired duplicate constitution reference

Not a Principle change; a housekeeping amendment.

`constitution.md` appears in two tracked locations:

- `.specify/memory/constitution.md` — authoritative (read by Spec Kit commands)
- `.specify/specs/constitution.md` — duplicate, drifted, authoritative source
  ambiguous

**Amendment**: delete `.specify/specs/constitution.md`, or explicitly mark it as
a generated read-only mirror of the memory copy. Identified as issue S15 in the
`/speckit.analyze` pass.

### Migration plan

```bash
git rm .specify/specs/constitution.md
```

If a mirror is preferred instead, generate it in CI from the memory copy.

---

## A-003 — Retire "NexusCap" from the constitution record

**Amendment**: "NexusCap" is retired as a project alias. "Aetheris" is the sole
project name. Documented titles have been updated; this records the decision so
future spec amendments do not reintroduce the alias.
