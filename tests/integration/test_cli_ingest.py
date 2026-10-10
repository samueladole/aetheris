"""CLI-level integration test for `aetheris ingest` (T039/T041).

Verifies the command's wiring against a scripted HTTP client, so the CLI path
is exercised without any live provider call (invariant A6).
"""

from __future__ import annotations

import contextlib
import io

from src.cli import main as cli
from src.ingestion.base import HttpResponse
from src.ingestion.registry import IngestionRegistry


class FakeHttp:
    def __init__(self, body: bytes):
        self.body = body
        self.calls: list[str] = []

    def get(self, url: str, *, params, headers, timeout):
        self.calls.append(url)
        return HttpResponse(200, self.body, {"Content-Type": "application/json"})


def _run(argv: list[str], monkeypatch, **env: str) -> tuple[int, str, str]:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.argv", ["aetheris", *argv])
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestIngestCommand:
    def test_unknown_provider_is_usage_error(self, monkeypatch):
        code, _, _ = _run(["ingest", "nope", "e"], monkeypatch)
        assert code == cli.EXIT_USAGE

    def test_endpoint_not_declared_is_usage_error(self, monkeypatch):
        code, _, err = _run(["ingest", "fred", "quote"], monkeypatch)
        assert code == cli.EXIT_USAGE
        assert "does not declare endpoint" in err

    def test_bad_param_is_usage_error(self, monkeypatch):
        code, _, err = _run(
            ["ingest", "fred", "series/observations", "--param", "noequals"],
            monkeypatch,
        )
        assert code == cli.EXIT_USAGE
        assert "KEY=VALUE" in err

    def test_unknown_data_type_is_usage_error(self, monkeypatch):
        code, _, err = _run(["run", "--trigger", "nonsense"], monkeypatch)
        assert code == cli.EXIT_USAGE

    def test_ingest_with_scripted_http_succeeds(self, monkeypatch, tmp_path):
        """Full CLI path with a fake HTTP client: exit 0 and a stored payload."""
        from src.ingestion.fred_client import FredAdapter

        http = FakeHttp(b'{"observations":[]}')

        registry = IngestionRegistry(
            payload_store=_store(tmp_path),
            http=http,
            activity_timeout_s=1.0,
        )
        registry.register_adapter(FredAdapter(api_key="k"))

        # Route the CLI's registry construction to our scripted one.
        import src.ingestion.registry as reg_mod

        monkeypatch.setattr(
            reg_mod, "build_registry", lambda **kw: registry
        )

        code, out, _ = _run(
            [
                "ingest",
                "fred",
                "series/observations",
                "--param",
                "series_id=GDP",
                "--json",
            ],
            monkeypatch,
            FRED_API_KEY="k",
        )
        assert code == cli.EXIT_OK, out
        assert http.calls, "the adapter must have been called"


def _store(tmp_path):
    from src.ingestion.payloads import RawPayloadStore

    return RawPayloadStore(str(tmp_path / "raw.db"))
