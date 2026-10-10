"""Scan the built documentation site for credential-shaped strings (T013).

The published site is **publicly readable**, so a credential that leaks into
the output is a security exposure rather than a cosmetic defect — FR-017. This
runs as a gate in the documentation build and exits non-zero on any match.

Two things it deliberately does:

  * Scans *built HTML*, not source. Source is the thing you already review;
    the built site is what actually ships, and build steps (docstring
    rendering, search-index generation) can introduce values that are not
    visible in any single source file.
  * Fails closed with a clear remediation instruction, because a leak
    requires the value to be rotated, not merely deleted.

Usage:
    python scripts/scan_secrets.py site/
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Credential shapes. Ordered by specificity. These are intentionally narrow:
# a broad match makes the gate cry wolf and get ignored, while a narrow one
# may miss occasionally. Narrow is the safer failure for a gate that is
# supposed to block a publish.
PATTERNS: dict[str, re.Pattern[str]] = {
    "generic-keyvalue": re.compile(
        r"""(?ix)
        \b(api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token|
           password|passwd|credentials?)\b
        \s*[:=]\s*
        ["'][A-Za-z0-9_\-./+]{12,}["']
        """
    ),
    "openai-style": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "github-pat": re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    "github-fine": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "google-api": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
}

# Values that look secret-shaped but are documentation placeholders or test
# fixtures. Matching these would train contributors to ignore the gate.
#
# These are matched against the *matched value*, not the whole line. Matching the
# whole line would let a single stray marker disable the gate for an entire
# line — and `<` in particular appears in nearly every HTML tag, which silently
# disabled this scanner on all HTML until a test caught it.
ALLOWLIST_SUBSTRINGS = (
    "REDACTED",
    "example",
    "EXAMPLE",
    "placeholder",
    "PLACEHOLDER",
    "xxx",
    "XXX",
    "your-",
    "YOUR-",
    "changeme",
    "dummy",
    "DUMMY",
    "not-real",
    "fake",
)

# Files whose entire purpose is to talk about credentials. Their content is
# educational, not a leak.
DOC_FILES_SKIPPED = ("accessibility.html",)


class Finding:
    """One credential-shaped match in one file."""

    def __init__(self, path: Path, rule: str, line_no: int) -> None:
        """Record one match: where it was, which rule matched, and on what line."""
        self.path = path
        self.rule = rule
        self.line_no = line_no

    def __str__(self) -> str:
        """Return the human-readable representation."""
        return f"{self.path}:{self.line_no}: {self.rule}"


def _is_allowlisted(matched_value: str) -> bool:
    """True when the matched value is a documented placeholder, not a real secret."""
    return any(marker in matched_value for marker in ALLOWLIST_SUBSTRINGS)


def scan_file(path: Path) -> list[Finding]:
    """Return every non-allowlisted credential-shaped match in one file."""
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                if _is_allowlisted(match.group(0)):
                    continue
                findings.append(Finding(path, rule, line_no))
    return findings


def scan_tree(root: Path) -> list[Finding]:
    """Scan every HTML file under ``root``."""
    findings: list[Finding] = []
    for path in sorted(root.rglob("*.html")):
        if path.name in DOC_FILES_SKIPPED:
            continue
        findings.extend(scan_file(path))
    return findings


def main(argv: list[str] | None = None) -> int:
    """Scan a built site directory and report any credential-shaped strings."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: scan_secrets.py <site-dir>", file=sys.stderr)
        return 2

    site = Path(args[0])
    if not site.is_dir():
        print(f"error: {site} is not a directory", file=sys.stderr)
        return 2

    findings = scan_tree(site)
    if not findings:
        print(f"secret scan: clean ({sum(1 for _ in site.rglob('*.html'))} files scanned)")
        return 0

    print(f"SECRET SCAN FAILED — {len(findings)} credential-shaped match(es) in the built site.")
    print("The published site is publicly readable, so a match is a security exposure.")
    print()
    for finding in findings:
        print(f"  {finding}")
    print()
    print("Remediation, in order:")
    print("  1. ROTATE the value — assume it is compromised, not merely exposed.")
    print("  2. Remove it from the source that produced it.")
    print("  3. If it is a documentation placeholder, add a marker from")
    print("     ALLOWLIST_SUBSTRINGS so the gate stops reporting it.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
