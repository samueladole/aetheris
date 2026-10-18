"""License gate for constitution Principle I (T015).

Every runtime dependency and every sentiment model checkpoint must be licensed
under a permissive license. This module owns that permitted set and walks the
installed dependency tree to verify it — exposed as `aetheris check-licenses`
and wired into CI so Principle I cannot regress silently.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata
from typing import Any

# ---------------------------------------------------------------------------
# The permitted set — constitution Principle I.
# SPDX identifiers are canonical; several strings map onto one identifier.
# ---------------------------------------------------------------------------
PERMITTED_SPDX = frozenset(
    {
        "MIT",
        "MIT-0",
        "Apache-2.0",
        "Apache-1.1",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "BSD-3-Clause-Clear",
        "ISC",
        "PSF-2.0",
        "Python-2.0",
        "Zlib",
        "Unlicense",
        "CC0-1.0",
        "0BSD",
        "BlueOak-1.0.0",
        # Permissive licences found in the resolved tree (torch, pillow, ujson,
        # regex). They passed before only through a substring match on a
        # compound expression; naming them makes the decision explicit.
        "BSL-1.0",
        "MIT-CMU",
        "TCL",
        "CNRI-Python",
        "HPND",
    }
)

# Non-SPDX spellings of permitted licences: trove classifier names and the
# free-text forms packages put in their License field. Matched case-insensitively.
PERMITTED_ALIASES = frozenset(
    name.lower()
    for name in (
        "MIT License",
        "BSD License",
        "3-Clause BSD License",
        "BSD 3-Clause License",
        "Apache Software License",
        "Apache 2.0 License",
        "Apache License 2.0",
        "Apache License, Version 2.0",
        "Python Software Foundation License",
        "ISC License (ISCL)",
        "ISC License",
        "The Unlicense (Unlicense)",
        "Historical Permission Notice and Disclaimer (HPND)",
    )
)

# Substrings that identify a non-permissive licence in a non-SPDX name.
_REJECTED_MARKERS = (
    "GPL",
    "GENERAL PUBLIC",
    "MPL",
    "MOZILLA PUBLIC",
    "SSPL",
    "BUSL",
    "BUSINESS SOURCE",
    "ELASTIC",
    "PROPRIETARY",
    "NON-COMMERCIAL",
)

# Licenses that are *not* acceptable and why. Used for actionable error text.
KNOWN_BAD = {
    "GPL-2.0": "GPLv2 copyleft — not permissive",
    "GPL-3.0": "GPLv3 copyleft — not permissive",
    "GPL-3.0-only": "GPLv3 copyleft — not permissive",
    "GPL-3.0-or-later": "GPLv3 copyleft — not permissive",
    "AGPL-3.0": "AGPLv3 copyleft — not permissive",
    "LGPL-2.1": "LGPL copyleft — not permissive",
    "LGPL-3.0": "LGPL copyleft — not permissive",
    "MPL-2.0": "MPL weak copyleft — not permissive",
    "SSPL-1.0": "SSPL service copyleft — explicitly excluded by the constitution",
    "BUSL-1.1": "Business Source License — not open source",
    "Elastic-2.0": "Elastic License — not open source",
    "CC-BY-NC-4.0": "Non-commercial — not permitted",
}

# Strings that name no licence at all. "OSI Approved" and "DFSG approved" are
# trove categories that include copyleft licences, so they decide nothing.
AMBIGUOUS = {"", "UNKNOWN", "OSI Approved", "DFSG approved"}


# ---------------------------------------------------------------------------
# Known, accepted deviations — constitution-amendments.md A-001.
#
# These transitive dependencies are outside Principle I's permitted set but are
# structurally unavoidable for the required capabilities, and have been accepted
# by explicit decision. Listing them here scopes the *exemption* to exactly
# these packages: a NEW non-permissive dependency still fails the gate, which is
# the whole point of keeping the check running.
# ---------------------------------------------------------------------------
KNOWN_VIOLATIONS: dict[str, str] = {
    "psycopg": (
        "LGPL-3.0-only. Hard dependency of DBOS 3.2.0 for its Postgres system "
        "database. DBOS is the only permissive, embeddable durable-execution "
        "library (research.md section A)."
    ),
    "psycopg-binary": (
        "LGPL-3.0-only. Binary wheel of psycopg, required alongside it."
    ),
    "certifi": (
        "MPL-2.0. CA bundle pulled in by requests/httpx/httpcore/evidently. "
        "Present in essentially every Python HTTP stack; hand-rolling TLS trust "
        "would be a security regression."
    ),
    "huey": (
        "License undetermined. Task queue used by MLflow's model-serving "
        "components, which Aetheris does not use."
    ),
    "pathspec": (
        "MPL-2.0. Orphaned in the resolved tree with no reverse dependency "
        "found; removable, tracked for removal."
    ),
    "tqdm": (
        "MPL-2.0 AND MIT. Progress bars; a hard dependency of transformers and "
        "huggingface_hub, so unavoidable while the sentiment engine uses "
        "transformers. Same MPL-2.0 situation as certifi."
    ),
}

# ---------------------------------------------------------------------------
# Licences verified by hand where a package's metadata names none.
#
# Name -> (SPDX id, how it was verified). Used instead of the metadata and
# listed in every report, so an override is never silent. This is not an
# exemption: only packages whose LICENSE file is permissive belong here.
# ---------------------------------------------------------------------------
VERIFIED_LICENSES: dict[str, tuple[str, str]] = {
    "skops": (
        "MIT",
        "LICENSE in github.com/skops-dev/skops is MIT; the package metadata "
        "declares only 'OSI Approved'. Required by mlflow.",
    ),
}


@dataclass(frozen=True)
class Component:
    """One installed distribution with a resolved license verdict."""

    name: str
    version: str
    license: str
    verdict: str  # "permitted" | "rejected" | "unknown" | "manual-review"

    @property
    def ok(self) -> bool:
        """Handle the ok."""
        return self.verdict == "permitted"


@dataclass(frozen=True)
class LicenseReport:
    """License report."""
    components: list[Component]
    strict: bool
    exemptions: frozenset[str] = frozenset()

    @property
    def _relevant(self) -> list[Component]:
        """Components that count toward the verdict (exempt ones do not)."""
        return [c for c in self.components if c.name.lower() not in self.exemptions]

    @property
    def rejected(self) -> list[Component]:
        """Handle the rejected."""
        return [c for c in self._relevant if c.verdict == "rejected"]

    @property
    def unknown(self) -> list[Component]:
        """Handle the unknown."""
        return [c for c in self._relevant if c.verdict == "unknown"]

    @property
    def exempted(self) -> list[Component]:
        """Components excluded by an accepted A-001 deviation."""
        return [c for c in self.components if c.name.lower() in self.exemptions]

    @property
    def passed(self) -> bool:
        """Strict mode also fails on unparseable licenses (principle of caution)."""
        if self.rejected:
            return False
        return not (self.strict and self.unknown)

    @property
    def coverage(self) -> tuple[int, int]:
        """Handle the coverage."""
        return sum(1 for c in self._relevant if c.ok), len(self._relevant)


class _ParseError(ValueError):
    """An SPDX expression that does not parse."""


# Verdict order for combining operands: AND keeps the worst, OR the best.
_RANK = {"rejected": 0, "unknown": 1, "permitted": 2}
_OPERATOR = re.compile(r"\s+(AND|OR)\s+")


def _classify_name(name: str) -> str:
    """Verdict for one licence name, which may carry an SPDX ``WITH`` exception."""
    # An exception only grants extra permissions, so the base licence decides.
    lic = name.split(" WITH ")[0].strip()
    if lic in AMBIGUOUS:
        return "unknown"
    if lic in PERMITTED_SPDX or lic.lower() in PERMITTED_ALIASES:
        return "permitted"
    if lic in KNOWN_BAD:
        return "rejected"
    upper = lic.upper()
    if any(marker in upper for marker in _REJECTED_MARKERS):
        return "rejected"
    return "unknown"


def _parse_or(text: str, i: int) -> tuple[str, int]:
    verdict, i = _parse_and(text, i)
    while (op := _OPERATOR.match(text, i)) and op.group(1) == "OR":
        right, i = _parse_and(text, op.end())
        verdict = max(verdict, right, key=_RANK.__getitem__)
    return verdict, i


def _parse_and(text: str, i: int) -> tuple[str, int]:
    verdict, i = _parse_operand(text, i)
    while (op := _OPERATOR.match(text, i)) and op.group(1) == "AND":
        right, i = _parse_operand(text, op.end())
        verdict = min(verdict, right, key=_RANK.__getitem__)
    return verdict, i


def _parse_operand(text: str, i: int) -> tuple[str, int]:
    """A parenthesised group, or a licence name running to the next operator.

    A "(" that opens an operand groups; one inside a name, as in
    "ISC License (ISCL)", is part of the name.
    """
    while i < len(text) and text[i].isspace():
        i += 1
    if text.startswith("(", i):
        verdict, i = _parse_or(text, i + 1)
        if not text.startswith(")", i):
            raise _ParseError(text)
        return verdict, i + 1
    start, depth = i, 0
    while i < len(text):
        if depth == 0 and _OPERATOR.match(text, i):
            break
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            if depth == 0:
                break  # closes an enclosing group
            depth -= 1
        i += 1
    if depth or i == start:
        raise _ParseError(text)
    return _classify_name(text[start:i]), i


def _classify(license_string: str) -> str:
    """Verdict for a licence name or SPDX expression: permitted, rejected, or unknown.

    Compound expressions follow SPDX semantics: every ``AND`` operand applies,
    so the worst verdict wins; ``OR`` lets the licensee choose, so the best
    wins; ``AND`` binds tighter than ``OR``. Anything unrecognised is unknown,
    which strict mode fails — the gate never passes a licence it cannot name.
    """
    text = (license_string or "").strip()
    if not text:
        return "unknown"
    try:
        verdict, end = _parse_or(text, 0)
    except _ParseError:
        return "unknown"
    return verdict if end == len(text) else "unknown"


def _license_of(dist: Any) -> str:
    """The licence a distribution declares, as a name or SPDX expression.

    Sources in order of precision: PEP 639's ``License-Expression``; then every
    specific ``License ::`` classifier, all of which apply; then the first line
    of the legacy free-text ``License`` field, which some packages fill with the
    whole licence text.
    """
    expr = str(dist.metadata.get("License-Expression") or "").strip()
    if expr:
        return expr
    names: list[str] = []
    for classifier in dist.metadata.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            name = classifier.split("::")[-1].strip()
            if name not in AMBIGUOUS and name not in names:
                names.append(name)
    if names:
        return " AND ".join(names)
    text = (dist.metadata.get("License") or "").strip()
    first_line = text.splitlines()[0].strip() if text else ""
    if first_line.lower() in {"unknown", "none"}:
        return ""
    return first_line


def audit_installed(
    *,
    strict: bool = True,
    exempt: bool = True,
    distributions: Iterable[Any] | None = None,
) -> LicenseReport:
    """Walk every installed distribution and classify its license.

    With ``exempt=True`` the packages recorded in :data:`KNOWN_VIOLATIONS`
    (amendment A-001) are excluded from the verdict but still reported, so the
    gate keeps flagging genuinely new non-permissive dependencies. Packages in
    :data:`VERIFIED_LICENSES` are classified by their hand-verified licence.
    ``distributions`` defaults to everything installed.
    """
    components: list[Component] = []
    if distributions is None:
        distributions = metadata.distributions()
    for dist in distributions:
        name = dist.metadata["Name"] or "<unknown>"
        version = dist.version or ""
        verified = VERIFIED_LICENSES.get(name.lower())
        lic = verified[0] if verified else _license_of(dist)
        components.append(Component(name, version, lic, _classify(lic)))
    components.sort(key=lambda c: c.name.lower())
    exemptions = (
        frozenset(k.lower() for k in KNOWN_VIOLATIONS) if exempt else frozenset()
    )
    return LicenseReport(components, strict, exemptions)


def audit_model_manifest(manifest: dict[str, str]) -> list[Component]:
    """Check the pinned sentiment model manifest against the permitted set.

    ``manifest`` maps model id -> license string. Called by the sentiment layer
    before loading any weights (V3 gate).
    """
    out: list[Component] = []
    for model_id, lic in manifest.items():
        out.append(Component(model_id, "pinned", lic, _classify(lic)))
    return out


def format_report(report: LicenseReport) -> str:
    """Human-readable output for `aetheris check-licenses`."""
    ok, total = report.coverage
    mode = "strict" if report.strict else "lenient"
    lines = [
        f"Aetheris license gate (Principle I) — {mode} mode",
        f"Distributions scanned: {total} ({len(report.exempted)} exempted)",
        f"Permissively licensed: {ok}",
        "",
    ]
    if report.rejected:
        lines.append("REJECTED (non-permissive license):")
        for c in sorted(report.rejected, key=lambda c: c.name.lower()):
            lines.append(f"  ✗ {c.name} {c.version} — {c.license}")
            reason = KNOWN_BAD.get(c.license)
            if reason:
                lines.append(f"      reason: {reason}")
        lines.append("")
    if report.unknown:
        lines.append("UNKNOWN (license could not be determined):")
        for c in sorted(report.unknown, key=lambda c: c.name.lower()):
            lines.append(f"  ? {c.name} {c.version} — {c.license or '(none declared)'}")
        lines.append("")
    verified = [c for c in report.components if c.name.lower() in VERIFIED_LICENSES]
    if verified:
        lines.append("VERIFIED by hand (metadata names no license; see VERIFIED_LICENSES):")
        for c in sorted(verified, key=lambda c: c.name.lower()):
            lines.append(f"  ✓ {c.name} {c.version} — {c.license}")
            lines.append(f"      {VERIFIED_LICENSES[c.name.lower()][1]}")
        lines.append("")
    if report.exempted:
        lines.append("EXEMPTED by accepted amendment A-001 (constitution-amendments.md):")
        for c in sorted(report.exempted, key=lambda c: c.name.lower()):
            reason = KNOWN_VIOLATIONS.get(c.name.lower(), "")
            lines.append(f"  ~ {c.name} {c.version} — {c.license}")
            if reason:
                lines.append(f"      {reason}")
        lines.append("")
    if not report.rejected and not report.unknown:
        lines.append(
            "✓ No unaccepted license violations."
            + (" (see exemptions above)" if report.exempted else "")
        )
    return "\n".join(lines)


def report_as_json(report: LicenseReport) -> str:
    """Handle the report as json."""
    return json.dumps(
        {
            "strict": report.strict,
            "passed": report.passed,
            "scanned": len(report._relevant),
            "exempted_count": len(report.exempted),
            "permitted": report.coverage[0],
            "rejected": [
                {"name": c.name, "version": c.version, "license": c.license}
                for c in report.rejected
            ],
            "unknown": [
                {"name": c.name, "version": c.version, "license": c.license}
                for c in report.unknown
            ],
            "exempted": [
                {"name": c.name, "version": c.version, "license": c.license}
                for c in report.exempted
            ],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    """Handle the main."""
    argv = list(sys.argv[1:] if argv is None else argv)
    strict = "--lenient" not in argv
    report = audit_installed(strict=strict)
    print(format_report(report))
    return 0 if report.passed else 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
