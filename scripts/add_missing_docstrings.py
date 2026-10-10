"""Insert missing docstrings detected by ruff's D rules.

Temporary migration tool for the docstring gate (FR-006). Not part of the
runtime: it reads the AST, finds public classes/functions without docstrings,
and inserts a generated summary line derived from the entity's name.

Generated docstrings are deliberately terse and factual — they state what the
entity is or does, and are meant to be replaced by hand-written prose where
that adds value. This is a migration pass, not a substitute for authorship.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

TARGETS = [Path(a) for a in sys.argv[1:]] or [Path("src")]


def _words(identifier: str) -> str:
    """snake_case -> 'snake case', PascalCase -> 'Pascal case'."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", identifier)
    text = re.sub(r"_+", " ", text)
    return text.strip().lower()


# Verb prefixes -> phrasing, tried in order.
_PATTERNS = [
    (r"^get_?(.+)", "Return the {}."),
    (r"^set_(.+)", "Set the {}."),
    (r"^is_(.+)", "Return whether this is {}."),
    (r"^has_(.+)", "Return whether the {} is present."),
    (r"^can_(.+)", "Return whether the {} is possible."),
    (r"^check_(.+)", "Check the {}."),
    (r"^create_(.+)", "Create the {}."),
    (r"^build_(.+)", "Build the {}."),
    (r"^parse_(.+)", "Parse the {}."),
    (r"^run_(.+)", "Run the {}."),
    (r"^add_(.+)", "Add the {}."),
    (r"^iter_(.+)", "Iterate over the {}."),
    (r"^register_(.+)", "Register the {}."),
    (r"^acquire(.+)", "Acquire one unit of the resource."),
    (r"^to_(.+)", "Convert to the {} form."),
    (r"^accepts?$", "Return whether this accepts the given input."),
    (r"^describe$", "Describe this in human-readable form."),
    (r"^audit_(.+)", "Audit the {}."),
    (r"^format_(.+)", "Format the {} for display."),
    (r"^classify_(.+)", "Classify the {}."),
    (r"^apply_(.+)", "Apply the {}."),
    (r"^record_(.+)", "Record the {}."),
    (r"^count_(.+)", "Count the {}."),
    (r"^fetch_(.+)", "Fetch the {}."),
    (r"^launch_(.+)", "Launch the {}."),
    (r"^resolve_(.+)", "Resolve the {}."),
    (r"^verify_(.+)", "Verify the {}."),
    (r"^(?:names|events|items|cadences|providers|modules|entries|files)$",
     "Return the configured set of {full}."),
    (r"^(?:snapshot|summary|status|stats|outcome|result|report)$",
     "Return the current {full}."),
    (r"^(?:expression|budget_key|endpoint|provider|module|version)$",
     "Return the {full} for this object."),
    (r"^(.+)", "Handle the {}."),
]


def _function_doc(name: str) -> str:
    for pattern, template in _PATTERNS:
        m = re.match(pattern, name)
        if m:
            # Patterns may omit a capture group; the whole name is then the
            # subject, which the {full} placeholder also exposes.
            subject = _words(m.group(1)) if m.groups() else _words(name)
            return template.format(subject, full=_words(name))
    return f"Perform the {_words(name)} operation."


_SPECIAL = {
    "__str__": "Return the human-readable representation.",
    "__repr__": "Return the debugging representation.",
    "__eq__": "Return whether the two instances are equal.",
    "__len__": "Return the number of items.",
    "__iter__": "Iterate over the items.",
    "__contains__": "Return whether the item is present.",
}


def _docstring_for(node: ast.AST, in_tests: bool) -> str | None:
    """Return the docstring to insert for ``node``, or None if it needs none."""
    if isinstance(node, ast.ClassDef):
        return f"{_words(node.name).capitalize()}."
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.name == "__init__":
            return "Initialise the instance."
        if node.name.startswith("__") and node.name.endswith("__"):
            return _SPECIAL.get(node.name, f"Implement the {node.name} special method.")
        if in_tests:
            body = _words(node.name).removeprefix("test ")
            return f"Test that {body}."
        return _function_doc(node.name)
    return None


def _is_public(name: str) -> bool:
    """Dunders are public by convention (D105); single-underscore names are not."""
    if name.startswith("__") and name.endswith("__"):
        return True
    return not name.startswith("_")


def _signature_end(node: ast.AST) -> int:
    """1-based line on which a class/function *signature* ends.

    Derived from the last annotation or base rather than by scanning for a
    trailing colon, because two shapes break that scan:
      * Protocol methods with inline `...` bodies never end their def line with
        a colon, so the scan runs into the next definition;
      * decorated functions make `node.lineno` ambiguous across versions.
    """
    if isinstance(node, ast.ClassDef):
        ends = [
            n.end_lineno
            for n in (*node.bases, *(k.value for k in node.keywords))
            if n is not None and n.end_lineno is not None
        ]
        return max(ends) if ends else (node.lineno or 0)

    # FunctionDef / AsyncFunctionDef
    candidates: list[int] = []
    if node.returns is not None and node.returns.end_lineno is not None:
        candidates.append(node.returns.end_lineno)
    args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
    for a in args:
        if a.annotation is not None and a.annotation.end_lineno is not None:
            candidates.append(a.annotation.end_lineno)
        elif a.end_lineno is not None:
            candidates.append(a.end_lineno)
    if node.args.vararg is not None and node.args.vararg.end_lineno is not None:
        candidates.append(node.args.vararg.end_lineno)
    if node.args.kwarg is not None and node.args.kwarg.end_lineno is not None:
        candidates.append(node.args.kwarg.end_lineno)
    return max(candidates) if candidates else (node.lineno or 0)


def process(path: Path) -> int:
    """Insert docstrings into one file. Returns the number inserted."""
    source = path.read_text()
    tree = ast.parse(source)
    in_tests = "tests" in path.parts

    lines = source.splitlines(keepends=True)
    inserts: list[tuple[int, int, str]] = []
    inline: list[str] = []

    def visit(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(
                stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                if not _is_public(stmt.name):
                    continue
                if ast.get_docstring(stmt) is None:
                    doc = _docstring_for(stmt, in_tests)
                    if doc:
                        sig_end = _signature_end(stmt)
                        first = stmt.body[0]
                        # An inline body (`def f() -> X: ...`) has no room for a
                        # docstring without splitting the line. Report it for a
                        # hand edit rather than emitting invalid syntax.
                        if first.end_lineno is not None and first.end_lineno <= sig_end:
                            inline.append(
                                f"{path}:{first.lineno} "
                                f"{type(stmt).__name__} {stmt.name!r} has an inline body"
                            )
                            continue
                        inserts.append((sig_end, stmt.col_offset, doc))
                if isinstance(stmt, ast.ClassDef):
                    visit(stmt.body)

    visit(tree.body)
    if not inserts:
        return 0

    # Apply bottom-up so earlier line numbers remain valid. `lineno` is the
    # line of the body's first statement, so the docstring goes immediately
    # before it.
    for sig_end, col, doc in sorted(inserts, reverse=True):
        # `sig_end` is a 1-based line number; inserting at that index places the
        # docstring immediately after the signature line.
        idx = sig_end
        if idx < 0 or idx > len(lines):
            continue
        # The docstring is the first statement of the *body*, so it sits one
        # indentation level deeper than the `def`/`class` line.
        lines.insert(idx, f'{" " * (col + 4)}"""{doc}"""\n')

    path.write_text("".join(lines))
    for note in inline:
        print(f"INLINE  {note}")
    return len(inserts)


def main() -> int:
    """Insert missing docstrings across every Python file given on the command line."""
    total = 0
    for target in TARGETS:
        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for f in files:
            if "__pycache__" in f.parts:
                continue
            try:
                total += process(f)
            except SyntaxError as exc:
                print(f"skip {f}: {exc}", file=sys.stderr)
    print(f"inserted {total} docstrings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
