"""Aetheris command-line interface.

Phase 1 implementation wires the global flags, exit codes, and the license gate
command (T015). The pipeline commands (`run`, `ingest`, `score`,
`graph upsert`, `query`, `evaluate`, `status`) are registered incrementally by
T024, T039, T051, T064, and T073 — each task adds its subcommand here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

from src.cli import licenses
from src.config.settings import ConfigError, Settings, get_settings

# Exit codes — part of the CLI contract (contracts/cli.md)
EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_LICENSE = 3

PROG = "aetheris"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Financial Intelligence Knowledge Graph & Sentiment Analysis Engine",
    )
    # Global options. These are ALSO attached to every subparser via a parent
    # parser so that `aetheris --json run` and `aetheris run --json` both work —
    # argparse subparsers otherwise require global flags before the command.
    _add_global_options(parser)

    parent = argparse.ArgumentParser(add_help=False)
    _add_global_options(parent)

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # -- check-licenses (T015) -------------------------------------------
    lic = sub.add_parser(
        "check-licenses", parents=[parent], help="Constitution Principle I license gate"
    )
    lic.add_argument(
        "--strict",
        action="store_true",
        default=True,
        help="Fail on any distribution whose license cannot be determined (default)",
    )
    lic.add_argument(
        "--lenient",
        dest="strict",
        action="store_false",
        help="Only fail on known non-permissive licenses, not undetermined ones",
    )
    lic.add_argument(
        "--no-exemptions",
        action="store_true",
        help="Ignore the accepted A-001 deviations and audit every distribution",
    )

    sub.add_parser("status", parents=[parent], help="Provider health, quotas, cache ratios")

    # -- docs (T009) ---------------------------------------------------
    doc = sub.add_parser(
        "docs", parents=[parent], help="Build, serve, or open the documentation site"
    )
    doc.add_argument(
        "action",
        choices=["build", "serve", "open"],
        help="build: run gates and emit site/; serve: preview; open: print the preview URL",
    )
    doc.add_argument(
        "--host", default="127.0.0.1", help="Preview bind host (localhost only)"
    )
    doc.add_argument("--port", type=int, default=8000, help="Preview port")
    doc.add_argument(
        "--no-gates",
        action="store_true",
        help="Skip the gates (development convenience; never usable in CI)",
    )

    # -- ingest (T039) -------------------------------------------------
    ing = sub.add_parser(
        "ingest", parents=[parent], help="Run a single ingestion adapter"
    )
    ing.add_argument("provider", metavar="<provider>", help="e.g. fred, finnhub, tiingo")
    ing.add_argument("endpoint", metavar="<endpoint>", help="e.g. series/observations")
    ing.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Request parameter (repeatable)",
    )
    ing.add_argument(
        "--data-type",
        metavar="TYPE",
        help="Capability key: macro, prices, fundamentals, news",
    )
    ing.add_argument(
        "--force", action="store_true", help="Bypass the TTL cache (FR-007)"
    )
    ing.add_argument(
        "--ttl", type=int, metavar="SECONDS", help="Cache TTL override for this payload"
    )

    # -- run (T024) -----------------------------------------------------
    run = sub.add_parser("run", parents=[parent], help="Execute the full pipeline durably")
    run.add_argument(
        "--trigger",
        default="schedule",
        metavar="schedule|event|event:<detail>",
        help="Trigger source (FR-016)",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and quotas without calling providers",
    )
    run.add_argument(
        "--resume",
        metavar="WORKFLOW_ID",
        help="Resume a checkpointed run from its first failed step",
    )

    sub.add_parser("version", parents=[parent], help="Print version and exit")
    return parser


def _add_global_options(target: argparse.ArgumentParser) -> None:
    """Attach the global CLI options (contracts/cli.md) to a parser."""
    target.add_argument(
        "--config",
        metavar="PATH",
        help="Settings file; defaults to $AETHERIS_CONFIG",
    )
    target.add_argument(
        "--db", metavar="PATH", help="DuckDB graph file (default ./aetheris.duckdb)"
    )
    target.add_argument(
        "--raw", metavar="PATH", help="SQLite raw payload file (default ./aetheris-raw.db)"
    )
    target.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    target.add_argument(
        "--trace", action="store_true", help="Force OpenTelemetry span export"
    )


def _cmd_check_licenses(args: argparse.Namespace) -> int:
    report = licenses.audit_installed(
        strict=args.strict, exempt=not args.no_exemptions
    )
    if args.json:
        print(licenses.report_as_json(report))
    else:
        print(licenses.format_report(report))
    return EXIT_OK if report.passed else EXIT_LICENSE


# --- Gates (contracts/docs-build.md) ----------------------------------------
# Invoked through the active interpreter so the gate works whether or not
# .venv/bin is on PATH.
_DOCSTRING_GATE = [
    sys.executable,
    "-m",
    "ruff",
    "check",
    "--select",
    "D100,D101,D102,D103,D104,D105,D106,D107",
    "src",
]

# AEN-001: shipped by the docs extra. `mkdocs` is the CLI entrypoint.
# Invoked through the active interpreter, not the console script, so the CLI
# works regardless of whether .venv/bin is on PATH.
_MKDOCS_BUILD = [sys.executable, "-m", "mkdocs", "build", "--strict"]

# Credential-shaped strings. The site is publicly readable (FR-017), so a match
# is a security exposure requiring rotation, not a cosmetic defect.
_SECRET_PATTERNS = [
    r'(?i)(api[_-]?key|apikey|token|secret|password|passwd|credential)\s*[:=]\s*["\'][A-Za-z0-9_\-]{12,}["\']',
    r'\bsk-[A-Za-z0-9]{20,}\b',
    r'\bghp_[A-Za-z0-9]{30,}\b',
    r'\bAKIA[0-9A-Z]{16}\b',
    r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b',
]


def _run_gate(argv: list[str], *, name: str) -> tuple[bool, str]:
    """Run one gate, returning (passed, output). Never raises."""
    import subprocess

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=300
        )
    except FileNotFoundError:
        return False, f"{name}: command not found: {argv[0]}"
    except subprocess.TimeoutExpired:
        return False, f"{name}: timed out after 300s"

    if proc.returncode == 0:
        return True, ""

    detail = (proc.stdout or "") + (proc.stderr or "")
    return False, f"{name} failed (exit {proc.returncode}):\n{detail[:4000]}"


def _cmd_docs(args: argparse.Namespace, settings: Settings) -> int:
    """`aetheris docs build|serve|open` (T009)."""
    from pathlib import Path

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            f"error: --host {args.host!r} is refused. The preview server binds to "
            "localhost only and must never be exposed as a network service.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.action == "open":
        print(f"http://{args.host}:{args.port}")
        return EXIT_OK

    if args.action == "serve":
        import subprocess

        # Invoke mkdocs through the active interpreter: a bare `mkdocs` relies
        # on .venv/bin being on PATH, which it is not when the CLI is installed
        # as a console script.
        cmd = [
            sys.executable,
            "-m",
            "mkdocs",
            "serve",
            "-a",
            f"{args.host}:{args.port}",
        ]
        try:
            return subprocess.call(cmd)
        except FileNotFoundError:
            print(
                "error: mkdocs not installed; run `pip install -e \".[docs]\"`",
                file=sys.stderr,
            )
            return EXIT_RUNTIME

    # --- build ---------------------------------------------------------
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            f"error: --host {args.host!r} is refused. The preview server binds to "
            "localhost only and must never be exposed as a network service.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if not Path("mkdocs.yml").is_file():
        print("error: mkdocs.yml not found — run from the repository root",
              file=sys.stderr)
        return EXIT_USAGE

    if not args.no_gates:
        for argv, name in [
            (_DOCSTRING_GATE, "docstring gate (FR-006)"),
            (_MKDOCS_BUILD, "site build (FR-010)"),
        ]:
            ok, detail = _run_gate(argv, name=name)
            if not ok:
                print(f"error: {detail}", file=sys.stderr)
                return EXIT_LICENSE
    else:
        print("warning: --no-gates skips the docstring and build gates",
              file=sys.stderr)

    # The a11y gates run over the built site, so they need the build first.
    site = Path("site")
    if not args.no_gates and site.is_dir():
        for argv, name in [
            (["npx", "html-validate", "site/**/*.html", "--formatter", "stylish"],
             "accessibility gate — structural (FR-019)"),
            (["python", "scripts/check_a11y_rendered.py", "site/"],
             "accessibility gate — rendered (FR-019)"),
        ]:
            ok, detail = _run_gate(argv, name=name)
            if not ok:
                print(f"error: {detail}", file=sys.stderr)
                return EXIT_LICENSE

    print(f"Documentation built into {site}/")
    return EXIT_OK


def _parse_params(pairs: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ConfigError(f"--param expects KEY=VALUE, got {pair!r}")
        params[key.strip()] = value.strip()
    return params


def _build_adapters(settings: Settings, provider: str) -> list[Any]:
    """Instantiate the concrete adapter(s) for ``provider``.

    Adapters contain no retry logic (invariant A3); they only shape requests.
    A single provider may back both market data and news, so both adapters are
    returned and the registry picks whichever matches the endpoint.
    """
    from src.ingestion.fred_client import FredAdapter
    from src.ingestion.market_client import MarketAdapter
    from src.ingestion.news_client import NewsAdapter

    creds = settings.provider_credentials()
    key = creds.get(provider)

    if provider == "fred":
        return [FredAdapter(api_key=key)]
    if provider in {"alpha_vantage", "finnhub", "fmp", "tiingo"}:
        return [
            MarketAdapter(provider, api_key=key),
            NewsAdapter(provider, api_key=key),
        ]
    raise ConfigError(f"unknown provider {provider!r}")


def _cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    """`aetheris ingest` — run one adapter through the registry (T039)."""
    from src.config.providers import get_registry
    from src.ingestion.base import ProviderUnavailable
    from src.ingestion.registry import build_registry

    registry_spec = get_registry()
    try:
        spec = registry_spec.get(args.provider)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.endpoint not in spec.endpoints:
        print(
            f"error: provider {args.provider!r} does not declare endpoint "
            f"{args.endpoint!r}. Declared: {list(spec.endpoints)}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        params = _parse_params(args.param)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    reg = build_registry(
        raw_db_path=settings.raw_db_path,
        default_ttl_s=settings.cache_default_ttl_s,
        activity_timeout_s=settings.activity_timeout_s,
    )

    # Register the adapter(s) for the requested provider.
    try:
        for adapter in _build_adapters(settings, args.provider):
            reg.register_adapter(adapter)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    data_type = args.data_type or _infer_data_type(args.provider, args.endpoint)

    from src.observability.telemetry import init_telemetry

    init_telemetry(settings)

    try:
        outcome = reg.fetch(
            data_type,
            endpoint=args.endpoint,
            params=params,
            ttl_seconds=args.ttl,
            force=args.force,
        )
    except ProviderUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    stats = reg.snapshot()
    if args.json:
        import json

        print(
            json.dumps(
                {
                    "payload_id": outcome.payload.payload_id,
                    "provider": outcome.provider,
                    "endpoint": outcome.payload.endpoint,
                    "served_from_cache": outcome.served_from_cache,
                    "bytes": len(outcome.payload.body),
                    "schema_version": outcome.payload.schema_version,
                    "fallbacks": outcome.fallbacks,
                    "cache": stats["cache"],
                },
                indent=2,
            )
        )
    else:
        source = "cache" if outcome.served_from_cache else outcome.provider
        print(f"Ingested {outcome.payload.endpoint} from {source}")
        print(f"  payload_id: {outcome.payload.payload_id}")
        print(f"  bytes:      {len(outcome.payload.body)}")
        if outcome.fallbacks:
            print(f"  fallbacks:  {outcome.fallbacks}")
    return EXIT_OK


def _infer_data_type(provider: str, endpoint: str) -> str:
    """Map a provider/endpoint onto a capability key (registry §capability map)."""
    if provider == "fred":
        return "macro"
    if endpoint in {"/news", "fmp/articles", "tiingo/news"}:
        return "news"
    if endpoint in {"stock/profile2"}:
        return "fundamentals"
    return "prices"


def _cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    """`aetheris run` — start or resume a durable pipeline workflow (T024)."""
    from src.config.providers import get_registry
    from src.orchestration import checkpoints, triggers, workflows

    if args.dry_run:
        from dataclasses import replace

        settings = replace(settings, dry_run=True)

    # Validate the trigger before touching the engine.
    try:
        trigger = triggers.parse_trigger(args.trigger)
    except triggers.TriggerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    # Credential pre-check: a live (non-dry) run needs at least one provider.
    registry = get_registry()
    if not settings.dry_run:
        missing = settings.missing_providers(registry.required_credentials())
        if missing:
            print(
                f"error: a live run needs provider credentials; missing: {missing}. "
                "Set them in the environment, or use --dry-run.",
                file=sys.stderr,
            )
            return EXIT_USAGE

    try:
        checkpoints.launch(settings)
        if args.resume:
            handle = checkpoints.resume(args.resume)
            action = "resumed"
        else:
            import asyncio

            handle = asyncio.run(workflows.start_async(settings, wait=True))
            action = "started"
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    if handle.status == "ERROR":
        if args.json:
            import json

            print(json.dumps({"action": action, "workflow_id": handle.workflow_id,
                              "status": "ERROR",
                              "error": (handle.result or {}).get("error")}, indent=2))
        else:
            print(f"Pipeline FAILED: {handle.workflow_id}")
            print(f"  error: {(handle.result or {}).get('error')}")
        return EXIT_RUNTIME

    if args.json:
        import json

        print(json.dumps({"action": action, "trigger": trigger.describe(),
                          "workflow_id": handle.workflow_id,
                          "status": handle.status,
                          "result": handle.result}, indent=2, default=str))
    else:
        print(f"Pipeline {action}: {handle.workflow_id}")
        print(f"  trigger: {trigger.describe()}")
        print(f"  status:  {handle.status}")
        if settings.dry_run:
            print("  mode:    dry-run (no provider calls)")
        for step in (handle.result or {}).get("steps", []):
            print(f"    - {step['name']}: {step['state']}")
    return EXIT_OK


def _cmd_status(args: argparse.Namespace, settings: Settings) -> int:
    """Report configuration and provider posture without contacting providers."""
    from src.config.providers import get_registry
    from src.orchestration import triggers

    registry = get_registry()
    missing = settings.missing_providers(registry.required_credentials())
    payload = {
        "service": settings.service_name,
        "db_path": settings.db_path,
        "raw_db_path": settings.raw_db_path,
        "providers_missing_credentials": missing,
        "redistribution_blockers": [p.name for p in registry.redistribution_blockers()],
        "triggers": [t.describe() for t in triggers.iter_triggers()],
        "settings": settings.redacted(),
    }
    if args.json:
        import json

        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"Aetheris status (db: {settings.db_path})")
        print(f"  Providers without credentials: {missing or 'none'}")
        print(
            "  Redistribution-blocked free tiers: "
            f"{payload['redistribution_blockers'] or 'none'}"
        )
        print(f"  Trigger sources: {', '.join(payload['triggers'])}")
        if missing:
            print(
                "  Note: 'aetheris run --dry-run' works without credentials; "
                "live ingestion requires them."
            )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage
        return int(exc.code or EXIT_USAGE)

    if not args.command:
        parser.print_help()
        return EXIT_USAGE

    try:
        settings = get_settings(args.config)
        # CLI flags override settings
        if args.db:
            settings = replace_db(settings, "db_path", args.db)
        if args.raw:
            settings = replace_db(settings, "raw_db_path", args.raw)

        if args.command == "check-licenses":
            return _cmd_check_licenses(args)
        if args.command == "run":
            return _cmd_run(args, settings)
        if args.command == "ingest":
            return _cmd_ingest(args, settings)
        if args.command == "docs":
            return _cmd_docs(args, settings)
        if args.command == "status":
            return _cmd_status(args, settings)
        if args.command == "version":
            print("aetheris 0.1.0")
            return EXIT_OK

        parser.error(f"unknown command {args.command!r}")
        return EXIT_USAGE

    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except KeyboardInterrupt:  # pragma: no cover
        print("\ninterrupted", file=sys.stderr)
        return EXIT_RUNTIME


def replace_db(settings: Settings, field: str, value: str) -> Settings:
    """Dataclasses.replace without importing dataclasses at call sites."""
    from dataclasses import replace

    return replace(settings, **{field: value})


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
