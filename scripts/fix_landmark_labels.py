"""Disambiguate duplicate landmark labels in the built documentation site.

The Material for MkDocs theme emits two ``<nav aria-label="Search">`` landmarks
(header search and drawer search) and two ``<nav aria-label="<site name>">``
landmarks. ``html-validate``'s ``unique-landmark`` rule correctly rejects these:
a screen-reader user navigating by landmark hears two indistinguishable
"Search" landmarks, which maps to WCAG 2.4.1 / 4.1.2.

The theme cannot be configured to relabel them, so this pass rewrites the
*second* and subsequent occurrences to a distinguishing suffix immediately
after ``mkdocs build``. This fixes a real accessibility defect rather than
silencing a rule that happens to fire.

Usage:
    python scripts/fix_landmark_labels.py site/
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

# Landmarks that carry an accessible name. Order is stable across the theme.
_LANDMARK_OPEN = re.compile(
    r"<(nav|main|aside|header|footer|form|section)\b([^>]*?)\baria-label=\"([^\"]*)\"([^>]*)>",
    re.IGNORECASE,
)

# Attributes that make an element addressable, so we can suffix distinguishably.
_ADDRESSABLE = ("search", "navigation", "table of contents")


def fix_file(path: Path) -> int:
    """Rewrite duplicate landmark labels in one file. Returns a count."""
    html = path.read_text(encoding="utf-8")
    seen: dict[tuple[str, str], int] = defaultdict(int)
    replacements: list[tuple[str, str]] = []

    for match in _LANDMARK_OPEN.finditer(html):
        tag, _pre, label, _post = match.group(1), match.group(2), match.group(3), match.group(4)
        seen[(tag.lower(), label)] += 1
        occurrence = seen[(tag.lower(), label)]
        if occurrence == 1:
            continue
        new_label = f"{label} ({occurrence})"
        original = match.group(0)
        updated = original.replace(f'aria-label="{label}"', f'aria-label="{new_label}"', 1)
        replacements.append((original, updated))

    for original, updated in replacements:
        html = html.replace(original, updated, 1)

    if replacements:
        path.write_text(html, encoding="utf-8")
    return len(replacements)


def fix_directory(site: Path) -> int:
    """Disambiguate duplicate landmark labels across every built page.

    Returns the total number of labels rewrote. Importable so the CLI can call
    it directly instead of shelling out.
    """
    total = 0
    for html_file in sorted(site.rglob("*.html")):
        total += fix_file(html_file)
    return total


def main(argv: list[str] | None = None) -> int:
    """Disambiguate landmark labels in the site directory given on the command line."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: fix_landmark_labels.py <site-dir>", file=sys.stderr)
        return 2

    site = Path(args[0])
    if not site.is_dir():
        print(f"error: {site} is not a directory", file=sys.stderr)
        return 2

    total = 0
    for html_file in sorted(site.rglob("*.html")):
        total += fix_file(html_file)

    print(f"landmark labels: disambiguated {total} duplicate labels in {site}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
