# Contract: Generated API Reference Page

**Producer**: `scripts/gen_api.py` → `mkdocstrings` · **Status**: to be implemented

This contract defines what one generated reference page must contain, so the gate can
assert it. The page is **generated**, never authored: there is no committed Markdown file
per module.

---

## Generation

`scripts/gen_api.py` runs as an `mkdocs-gen-files` script during the build. It:

1. Walks `src/**/*.py`, skipping `__pycache__` directories and `__init__.py`.
2. Derives the dotted module path from the file path relative to `src/`
   (e.g. `src/graph/repository.py` → `graph.repository`).
3. Emits `docs/reference/<relative path>.md` containing exactly one directive:
   `::: graph.repository`
4. Appends the page to a generated `SUMMARY.md` for `literate-nav`, so no navigation entry
   is hand-maintained (FR-004).

Adding a public module requires **no manual edit** to any documentation page (FR-005).

## Page content

A generated page for module `graph.repository` MUST contain:

| Element | Requirement |
|---|---|
| Module heading | The dotted module path |
| Module docstring | Rendered as the page's leading prose |
| Each public class | Name, signature with type annotations, and its docstring |
| Each public method | Name, signature, parameters with types, return type |
| Each public function | Name, signature, parameters with types, return type |
| Excluded | Any name beginning with `_` (private, protected, or dunder) |
| Excluded | Entities with no docstring — these fail gate 1 instead of rendering |

**Prohibited**: hand-written prose, usage examples not present in the source docstring, or
any content not derived from the source.

## Exclusions

`filters: ["!^_"]` in the generator configuration excludes underscore-prefixed names. This
is a naming convention, not a per-module allowlist, so exclusion stays automatic (FR-008).

## Acceptance assertions (what the gate checks)

1. Every module file under `src/` has a corresponding page in the built site.
2. No page renders an entity whose name begins with `_`.
3. Each rendered public entity shows a signature and a docstring.
4. Rebuilding produces byte-identical output for identical source (determinism).

## Relationship to the docstring gate

Gate 1 (`ruff` D-rules) runs **before** generation. Consequently an undocumented public
entity stops the build at gate 1 and never reaches a page — which is why no page can ever
contain an undocumented entity, and why the generator's `show_if_no_docstring: false`
setting is a safety net rather than the enforcement mechanism.
