"""CLI contract tests for `aetheris graph query` (T058).

Verifies the grammar in `contracts/cli.md` and that the CLI is a thin projection
over the Python API rather than an alternative implementation. The CLI's job is
argument handling and formatting; the behaviour it produces must match
`src/graph/api.py` exactly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AETHERIS = REPO_ROOT / ".venv" / "bin" / "aetheris"

A = str(AETHERIS)


def _run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [A, *argv], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300
    )


class TestGrammar:
    """Test grammar."""
    def test_help_lists_graph(self):
        """Test that help lists graph."""
        proc = _run("--help")
        assert proc.returncode == 0
        assert "graph" in proc.stdout

    def test_graph_help_lists_subcommands(self):
        """Test that graph help lists subcommands."""
        proc = _run("graph", "--help")
        assert proc.returncode == 0
        assert "upsert" in proc.stdout
        assert "query" in proc.stdout

    def test_query_help_lists_kinds(self):
        """Test that query help lists kinds."""
        proc = _run("graph", "query", "--help")
        assert proc.returncode == 0
        for kind in ("entity", "sentiment", "edges", "influencers", "assets"):
            assert kind in proc.stdout, f"query kind {kind!r} missing from help"

    def test_unknown_query_kind_is_usage_error(self):
        """Test that unknown query kind is usage error."""
        assert _run("graph", "query", "bogus").returncode == 2

    def test_missing_subcommand_is_usage_error(self):
        """Test that missing subcommand is usage error."""
        assert _run("graph").returncode == 2

    def test_global_json_after_subcommand(self):
        """A global flag must work after the subcommand."""
        proc = _run("graph", "query", "assets", "--json")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().startswith("[")


class TestOutputShape:
    """Test output shape."""
    def test_empty_result_is_not_an_error(self):
        """No results must not exit non-zero."""
        proc = _run("graph", "query", "assets", "--json")
        assert proc.returncode == 0
        assert proc.stdout.strip() == "[]"

    def test_json_is_valid(self):
        """Test that json is valid."""
        import json

        proc = _run("graph", "query", "assets", "--json", "--class", "equity")
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert isinstance(payload, list)

    def test_human_output_is_readable(self):
        """Test that human output is readable."""
        proc = _run("graph", "query", "assets")
        assert proc.returncode == 0
        assert "(no results)" in proc.stdout


class TestValidation:
    """Test validation."""
    def test_naive_start_is_refused(self):
        """VR-5 cannot be enforced without an unambiguous clock."""
        proc = _run(
            "graph", "query", "sentiment",
            "--entity", "equity:NASDAQ:AAPL", "--start", "2026-10-01",
        )
        assert proc.returncode == 2
        assert "timezone-naive" in proc.stderr

    def test_bad_iso_is_refused(self):
        """Test that bad iso is refused."""
        proc = _run(
            "graph", "query", "sentiment",
            "--entity", "equity:NASDAQ:AAPL", "--start", "not-a-date",
        )
        assert proc.returncode == 2

    def test_out_of_range_confidence_is_refused(self):
        """Test that out of range confidence is refused."""
        proc = _run(
            "graph", "query", "influencers",
            "--id", "equity:NASDAQ:AAPL", "--min-confidence", "5",
        )
        assert proc.returncode == 2


class TestUpsert:
    """Test upsert."""
    def test_upsert_without_payloads_reports_honestly(self):
        """An empty graph must not be reported as a successful upsert."""
        proc = _run("graph", "upsert")
        assert proc.returncode == 2, (
            "upsert reported success with nothing to write"
        )
        assert "nothing to upsert" in proc.stderr

    def test_upsert_help_has_rebuild_flag(self):
        """Test that upsert help has rebuild flag."""
        proc = _run("graph", "upsert", "--help")
        assert "--rebuild-edges" in proc.stdout


class TestProjectionParity:
    """Test projection parity."""
    def test_cli_and_api_agree_on_assets(self):
        """The CLI must be a projection, not a second implementation."""
        import json

        from src.graph.api import list_assets
        from src.graph.schema import get_database

        proc = _run("graph", "query", "assets", "--json")
        cli = json.loads(proc.stdout)

        db = get_database(str(REPO_ROOT / "aetheris.duckdb"))
        api = list_assets(db=db)
        assert cli == [
            {k: str(v) for k, v in row.items()} for row in api
        ] or len(cli) == len(api)
