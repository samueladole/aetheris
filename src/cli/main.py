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
