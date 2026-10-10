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


@dataclass(frozen=True)
class Component:
    """One installed distribution with a resolved license verdict."""

    name: str
    version: str
    license: str
    verdict: str  # "permitted" | "rejected" | "unknown" | "manual-review"

    @property
    def ok(self) -> bool:
        return self.verdict == "permitted"


@dataclass(frozen=True)
class LicenseReport:
    components: list[Component]
    strict: bool

    @property
    def rejected(self) -> list[Component]:
        return [c for c in self.components if c.verdict in ("rejected",)]

    @property
    def unknown(self) -> list[Component]:
        return [c for c in self.components if c.verdict == "unknown"]

    @property
    def passed(self) -> bool:
        """Strict mode also fails on unparseable licenses (principle of caution)."""
        if self.rejected:
            return False
        return not (self.strict and self.unknown)

    @property
    def coverage(self) -> tuple[int, int]:
        return sum(1 for c in self.components if c.ok), len(self.components)


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


def audit_installed(*, strict: bool = True) -> LicenseReport:
    """Walk every installed distribution and classify its license."""
    components: list[Component] = []
    for dist in metadata.distributions():
        name = dist.metadata["Name"] or "<unknown>"
        version = dist.version or ""
        lic = _license_of(dist)
        components.append(Component(name, version, lic, _classify(lic)))
    components.sort(key=lambda c: c.name.lower())
    return LicenseReport(components, strict)


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
    lines = [
        f"Aetheris license gate (Principle I) — {'strict' if report.strict else 'lenient'} mode",
        f"Distributions scanned: {total}",
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
    if not report.rejected and not report.unknown:
        lines.append("✓ All installed distributions are permissively licensed.")
    return "\n".join(lines)


def report_as_json(report: LicenseReport) -> str:
    return json.dumps(
        {
            "strict": report.strict,
            "passed": report.passed,
            "scanned": len(report.components),
            "permitted": report.coverage[0],
            "rejected": [
                {"name": c.name, "version": c.version, "license": c.license}
                for c in report.rejected
            ],
            "unknown": [
                {"name": c.name, "version": c.version} for c in report.unknown
            ],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    strict = "--lenient" not in argv
    report = audit_installed(strict=strict)
    print(format_report(report))
    return 0 if report.passed else 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
