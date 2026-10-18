"""Licence gate tests — Constitution Principle I.

The gate is only worth running if it fails closed. These tests pin the defects
that let non-permissive packages through: an unrecognised licence string got a
``manual-review`` verdict that neither failed the gate nor appeared in its
report, a substring test read "MPL-2.0 AND MIT" (and "LIMITED") as MIT, and
only the first ``License ::`` classifier was ever read.
"""

from __future__ import annotations

from email.message import Message
from types import SimpleNamespace

import pytest

from src.cli import licenses
from src.cli.licenses import (
    KNOWN_VIOLATIONS,
    VERIFIED_LICENSES,
    _classify,
    _license_of,
    audit_installed,
    format_report,
)


def _dist(
    name: str,
    *,
    classifiers: tuple[str, ...] = (),
    expression: str | None = None,
    license_text: str | None = None,
    version: str = "1.0",
) -> SimpleNamespace:
    """A stand-in for ``importlib.metadata.Distribution``."""
    msg = Message()
    msg["Name"] = name
    for classifier in classifiers:
        msg["Classifier"] = classifier
    if expression is not None:
        msg["License-Expression"] = expression
    if license_text is not None:
        msg["License"] = license_text
    return SimpleNamespace(metadata=msg, version=version)


class TestClassify:
    """One licence string in, one of three verdicts out."""

    @pytest.mark.parametrize(
        "lic",
        [
            "MIT",
            "MIT License",
            "MIT-CMU",
            "Apache Software License",
            "Apache 2.0 License",
            "Apache License 2.0",
            "Python Software Foundation License",
            "ISC License (ISCL)",
            "3-Clause BSD License",
            "BSD License",
            "Apache-2.0 WITH LLVM-exception",
        ],
    )
    def test_permissive_names_are_permitted(self, lic):
        """Test that SPDX ids and common classifier names are permitted."""
        assert _classify(lic) == "permitted"

    def test_and_with_a_copyleft_part_is_rejected(self):
        """Every AND operand applies, so one copyleft part rejects (tqdm)."""
        assert _classify("MPL-2.0 AND MIT") == "rejected"

    def test_or_with_a_permissive_choice_is_permitted(self):
        """An OR lets the licensee choose the permissive option."""
        assert _classify("GPL-3.0-only OR MIT") == "permitted"

    def test_parentheses_group_operands(self):
        """Test that parenthesised sub-expressions are evaluated first."""
        assert _classify("(MIT OR GPL-2.0) AND Apache-2.0") == "permitted"
        assert _classify("(MIT OR Apache-2.0) AND LGPL-3.0") == "rejected"

    def test_and_binds_tighter_than_or(self):
        """SPDX precedence: ``A OR B AND C`` is ``A OR (B AND C)``."""
        assert _classify("MIT OR GPL-3.0 AND Apache-2.0") == "permitted"

    def test_proprietary_is_rejected(self):
        """NVIDIA's CUDA wheels declare a proprietary licence."""
        assert _classify("Other/Proprietary License") == "rejected"
        assert _classify("NVIDIA Proprietary Software") == "rejected"

    @pytest.mark.parametrize("lic", ["", "OSI Approved", "Limited Use License", "Custom"])
    def test_unrecognised_is_unknown_not_permitted(self, lic):
        """An unrecognised string fails closed; "LIMITED" is not MIT."""
        assert _classify(lic) == "unknown"


class TestLicenseOf:
    """Reading the licence from a distribution's metadata."""

    def test_expression_wins_over_classifiers(self):
        """PEP 639's License-Expression is the most precise source."""
        dist = _dist(
            "x", classifiers=("License :: OSI Approved :: MIT License",), expression="MPL-2.0"
        )
        assert _license_of(dist) == "MPL-2.0"

    def test_generic_classifiers_are_skipped(self):
        """'OSI Approved' and 'DFSG approved' name no licence."""
        dist = _dist(
            "x",
            classifiers=("License :: DFSG approved", "License :: OSI Approved :: MIT License"),
        )
        assert _classify(_license_of(dist)) == "permitted"

    def test_every_classifier_applies(self):
        """A copyleft classifier after a permissive one is not ignored."""
        dist = _dist(
            "x",
            classifiers=(
                "License :: OSI Approved :: MIT License",
                "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
            ),
        )
        assert _classify(_license_of(dist)) == "rejected"

    def test_free_text_uses_its_first_line(self):
        """A License field holding the whole licence text is named by line one."""
        dist = _dist(
            "x", license_text="MIT License\n\nCopyright (c) 2022\nPermission is hereby granted"
        )
        assert _classify(_license_of(dist)) == "permitted"

    def test_generic_only_metadata_is_unknown(self):
        """Test that metadata naming no licence classifies as unknown."""
        dist = _dist("x", classifiers=("License :: OSI Approved",))
        assert _classify(_license_of(dist)) == "unknown"


class TestAudit:
    """The report built from every installed distribution."""

    def test_strict_mode_fails_on_unknown_and_reports_it(self):
        """An unknown licence fails strict mode and is printed, not hidden."""
        report = audit_installed(
            strict=True, distributions=[_dist("mystery", license_text="Custom")]
        )
        assert not report.passed
        assert "mystery" in format_report(report)

    def test_proprietary_wheel_is_rejected(self):
        """Test that a proprietary CUDA wheel fails the gate."""
        dist = _dist(
            "nvidia-cublas", classifiers=("License :: Other/Proprietary License",)
        )
        report = audit_installed(strict=False, distributions=[dist])
        assert [c.name for c in report.rejected] == ["nvidia-cublas"]
        assert not report.passed

    def test_tqdm_is_an_accepted_deviation(self):
        """A-001 records tqdm (MPL-2.0 AND MIT), which transformers requires."""
        assert "tqdm" in KNOWN_VIOLATIONS
        dist = _dist("tqdm", license_text="MPL-2.0 AND MIT")
        report = audit_installed(strict=True, distributions=[dist])
        assert report.passed
        assert [c.name for c in report.exempted] == ["tqdm"]

    def test_verified_licence_overrides_vague_metadata_visibly(self):
        """A hand-verified licence is used and listed in the report."""
        assert VERIFIED_LICENSES["skops"][0] == "MIT"
        dist = _dist("skops", classifiers=("License :: OSI Approved",))
        report = audit_installed(strict=True, distributions=[dist])
        assert report.passed
        assert "VERIFIED" in format_report(report)
        assert "skops" in format_report(report)

    def test_no_component_is_left_without_a_failing_or_passing_verdict(self):
        """There is no fourth, invisible verdict."""
        dists = [_dist(f"p{i}", license_text=lic) for i, lic in enumerate(
            ["MIT", "GPL-3.0", "Custom", "Other/Proprietary License", ""]
        )]
        report = audit_installed(strict=True, distributions=dists)
        assert {c.verdict for c in report.components} <= {"permitted", "rejected", "unknown"}
        assert licenses.LicenseReport  # the public type is unchanged
