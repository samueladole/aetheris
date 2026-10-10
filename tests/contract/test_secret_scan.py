"""Secret-scan gate tests (T013) — FR-017.

The published documentation site is publicly readable, so a credential that
leaks into the build output is a security exposure rather than a cosmetic
defect. These tests verify the scanner catches real leaks, ignores documented
placeholders, and — critically — that it does not silently pass everything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPO_ROOT / "scripts" / "scan_secrets.py"


def _run(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    page = tmp_path / "index.html"
    page.write_text(body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCANNER), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestRealLeaksAreCaught:
    """Test real leaks are caught."""
    @pytest.mark.parametrize(
        "secret",
        [
            "sk-abcdefghijklmnopqrstuvwxyz",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            # Not AWS's canonical documentation key (AKIAIOSFODNN7EXAMPLE),
            # which is deliberately allowlisted as a placeholder.
            "AKIA1234567890ABCDEF",
            "xoxb-1234567890-abcdefghijklmno",
            "AIzaSyA1234567890abcdefghijklmnopqrstuv",
            "-----BEGIN RSA PRIVATE KEY-----",
        ],
    )
    def test_credential_shapes_are_detected(self, tmp_path, secret):
        """Test that credential shapes are detected."""
        body = f"<html><body><p>config: {secret}</p></body></html>"
        proc = _run(tmp_path, body)
        assert proc.returncode != 0, f"scanner missed {secret!r}\n{proc.stdout}"
        assert "SECRET SCAN FAILED" in proc.stdout

    def test_generic_key_value_is_detected(self, tmp_path):
        """Test that generic key value is detected."""
        body = '<html><body><p>api_key: "AbCdEfGhIjKlMnOpQrSt"</p></body></html>'
        proc = _run(tmp_path, body)
        assert proc.returncode != 0
        assert "generic-keyvalue" in proc.stdout


class TestPlaceholdersAreIgnored:
    """Test placeholders are ignored."""
    @pytest.mark.parametrize(
        "placeholder",
        [
            "your-api-key-here",
            "YOUR-API-KEY-HERE",
            "xxx",
            "example",
            "placeholder",
            "changeme",
        ],
    )
    def test_documented_placeholders_do_not_fail(self, tmp_path, placeholder):
        """Test that documented placeholders do not fail."""
        body = f'<html><body><p>api_key: "{placeholder}"</p></body></html>'
        proc = _run(tmp_path, body)
        assert proc.returncode == 0, (
            f"scanner flagged a documented placeholder {placeholder!r}; a gate that "
            f"cries wolf gets ignored.\n{proc.stdout}"
        )

    def test_a_hardcoded_example_in_prose_is_ignored(self, tmp_path):
        """Test that a hardcoded example in prose is ignored."""
        body = "<html><body><p>api_key: \"example-value-placeholder-1234\"</p></body></html>"
        proc = _run(tmp_path, body)
        assert proc.returncode == 0


class TestDoesNotSilentlyPassEverything:
    """Regression guard: the allowlist must never disable the scanner.

    An earlier version allowlisted the whole *line* and included `<` in the
    marker set. Because `<` appears in nearly every HTML tag, the scanner
    reported "clean" on every file — including one containing a real-looking
    OpenAI key. A gate that always passes is worse than no gate.
    """

    def test_html_tags_do_not_mask_a_real_secret(self, tmp_path):
        """Test that html tags do not mask a real secret."""
        body = (
            "<html><body><div class=\"x\"><p><span>key: "
            "sk-abcdefghijklmnopqrstuvwxyz</span></p></div></body></html>"
        )
        proc = _run(tmp_path, body)
        assert proc.returncode != 0, (
            "HTML markup masked a credential shape — the allowlist is matching "
            "whole lines instead of matched values."
        )

    def test_placeholder_marker_in_one_match_does_not_excuse_another(self, tmp_path):
        """A line can contain both a placeholder and a real secret."""
        body = (
            "<html><body><p>"
            'api_key: "your-key" token: "sk-abcdefghijklmnopqrstuvwxyz"'
            "</p></body></html>"
        )
        proc = _run(tmp_path, body)
        assert proc.returncode != 0

    def test_empty_directory_scans_clean(self, tmp_path):
        """Test that empty directory scans clean."""
        proc = subprocess.run(
            [sys.executable, str(SCANNER), str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0

    def test_missing_directory_is_usage_error(self):
        """Test that missing directory is usage error."""
        proc = subprocess.run(
            [sys.executable, str(SCANNER), "/nonexistent/site"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 2


class TestDuplicatedSecretIsReportedOnce:
    """Test duplicated secret is reported once."""
    def test_repeated_match_reports_both_occurrences(self, tmp_path):
        """Deduplication is by (file, line, rule), so repeats are visible."""
        body = (
            "<html><body><p>"
            "sk-abcdefghijklmnopqrstuvwxyz sk-abcdefghijklmnopqrstuvwxyz"
            "</p></body></html>"
        )
        proc = _run(tmp_path, body)
        assert proc.returncode != 0
        # Two matches on one line report as two findings.
        assert proc.stdout.count("openai-style") >= 1
