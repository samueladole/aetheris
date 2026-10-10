"""License gate for constitution Principle I (T015).

Every runtime dependency and every sentiment model checkpoint must be licensed
under a permissive license. This module owns that permitted set and walks the
installed dependency tree to verify it — exposed as `aetheris check-licenses`
and wired into CI so Principle I cannot regress silently.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib import metadata

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
    }
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

# Licenses that need human judgement, reported as warnings but not failures.
AMBIGUOUS = {"", "UNKNOWN", "Proprietary", "Other/Proprietary"}


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


def _classify(license_string: str) -> str:
    lic = (license_string or "").strip()
    if not lic or lic in AMBIGUOUS:
        return "unknown"
    if lic in PERMITTED_SPDX:
        return "permitted"
    if lic in KNOWN_BAD:
        return "rejected"
    upper = lic.upper()
    if "APACHE" in upper and "2.0" in upper:
        return "permitted"
    if "MIT" in upper:
        return "permitted"
    if "BSD" in upper:
        return "permitted"
    if any(k in upper for k in ("GPL", "AGPL", "LGPL", "MPL", "SSPL", "BUSL", "ELASTIC")):
        return "rejected"
    return "manual-review"


def _license_of(dist: metadata.Distribution) -> str:
    """Best-effort license extraction across metadata formats."""
    # 1. Modern classifiers
    classifiers = dist.metadata.get_all("Classifier") or []
    for c in classifiers:
        if c.startswith("License ::"):
            return c.split("::")[-1].strip()
    # 2. License-Expression (PEP 639)
    expr = dist.metadata.get("License-Expression")
    if expr:
        return expr.strip()
    # 3. Legacy free-text License field
    txt = dist.metadata.get("License")
    if txt and txt.strip().lower() not in {"unknown", "none"}:
        return txt.strip()
    return ""


def audit_installed(*, strict: bool = True, exempt: bool = True) -> LicenseReport:
    """Walk every installed distribution and classify its license.

    With ``exempt=True`` the packages recorded in :data:`KNOWN_VIOLATIONS`
    (amendment A-001) are excluded from the verdict but still reported, so the
    gate keeps flagging genuinely new non-permissive dependencies.
    """
    components: list[Component] = []
    for dist in metadata.distributions():
        name = dist.metadata["Name"] or "<unknown>"
        version = dist.version or ""
        lic = _license_of(dist)
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
            lines.append(f"  ? {c.name} {c.version}")
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
                {"name": c.name, "version": c.version} for c in report.unknown
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
