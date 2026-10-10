"""Generate MkDocs API reference pages from the Aetheris source tree.

Runs as a ``mkdocs-gen-files`` script (declared in ``mkdocs.yml``), so adding a
public module requires **no manual edit** to any documentation page or
navigation entry (FR-004, FR-009).

For every ``src/**/*.py`` module (excluding ``__pycache__`` and ``__init__.py``)
this emits ``docs/reference/<relative path>.md`` containing a single
``::: <module>`` directive, then writes ``docs/reference/SUMMARY.md`` listing
them for ``mkdocs-literate-nav``.

The generator walks the filesystem and never imports the project, so the docs
build cannot break because a module has an import-time side effect, and the
layer boundaries enforced by ``.importlinter`` are unaffected.
"""

from __future__ import annotations

from pathlib import Path

import mkdocs_gen_files

# The importable package root is literally `src` (see pyproject.toml).
SRC = Path("src")

# Never emit a page for these.
EXCLUDED_DIRS = {"__pycache__"}
EXCLUDED_FILES = {"__init__.py"}

# Where generated pages live, relative to docs_dir.
REFERENCE_DIR = Path("reference")

SUMMARY = REFERENCE_DIR / "SUMMARY.md"


def _iter_modules() -> list[Path]:
    """Every source module that should get a reference page, in stable order."""
    if not SRC.is_dir():
        raise SystemExit(
            f"{SRC}/ not found — run this from the repository root "
            "as an mkdocs-gen-files script."
        )
    return sorted(
        p
        for p in SRC.rglob("*.py")
        if p.is_file()
        and not any(part in EXCLUDED_DIRS for part in p.parts)
        and p.name not in EXCLUDED_FILES
    )


def _module_path(path: Path) -> str:
    """Dotted module name, e.g. src/graph/repository.py -> graph.repository."""
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def _doc_path(path: Path) -> Path:
    """Output page path, e.g. src/graph/repository.py -> reference/graph/repository.md."""
    return REFERENCE_DIR / path.relative_to(SRC).with_suffix(".md")


def main() -> None:
    """Write generated reference pages and the root SUMMARY.md.

    The root SUMMARY.md is what ``mkdocs-literate-nav`` consumes as the entire
    site navigation. Writing the nav (rather than declaring it in
    ``mkdocs.yml``) is what keeps
    FR-004 true: adding a module adds its nav entry automatically.
    """
    nav = mkdocs_gen_files.Nav()
    index_lines: list[str] = []

    for source in _iter_modules():
        module = _module_path(source)
        doc = _doc_path(source)

        with mkdocs_gen_files.open(doc, "w") as fd:
            fd.write(f"# {module}\n\n")
            fd.write(f"::: {module}\n")

        nav[module] = doc.as_posix()
        # index.md lives *inside* reference/, so its links are relative to it.
        index_lines.append(f"* [{module}]({doc.relative_to(REFERENCE_DIR).as_posix()})")

        # Let readers jump from the rendered page back to the source.
        mkdocs_gen_files.set_edit_path(doc, source)

    # Section index so `reference/` has a landing page.
    with mkdocs_gen_files.open(REFERENCE_DIR / "index.md", "w") as fd:
        fd.write("# API Reference\n\n")
        fd.write(
            "Generated automatically from source docstrings. This page and the "
            "pages it links to are build output — they are never hand-edited.\n\n"
        )
        fd.write("\n".join(index_lines))
        fd.write("\n")

    # The whole nav, authored by the generator.
    with mkdocs_gen_files.open("SUMMARY.md", "w") as fd:
        fd.writelines(nav.build_literate_nav())

    # `Nav` has no __len__, so count the modules we generated directly.
    print(f"gen_api: wrote {len(index_lines)} reference pages to {REFERENCE_DIR}/")


# mkdocs-gen-files executes this file via `runpy.run_path(file_name)` with no
# `run_name`, so `__name__` is '<run_path>' - NOT '__main__'. A __main__ guard
# would therefore never fire and the generator would silently produce nothing.
# `main()` is called unconditionally for that reason; there is deliberately no
# __main__ guard here, and adding one would reintroduce the silent no-op.
main()
