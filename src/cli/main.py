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
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Settings file; defaults to $AETHERIS_CONFIG",
    )
    parser.add_argument(
        "--db", metavar="PATH", help="DuckDB graph file (default ./aetheris.duckdb)"
    )
    parser.add_argument(
        "--raw", metavar="PATH", help="SQLite raw payload file (default ./aetheris-raw.db)"
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--trace", action="store_true", help="Force OpenTelemetry span export"
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # -- check-licenses (T015) -------------------------------------------
    lic = sub.add_parser(
        "check-licenses", help="Constitution Principle I license gate"
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
        help="Only fail on known non-permissive licenses",
    )

    sub.add_parser("status", help="Provider health, quotas, cache ratios")
    sub.add_parser("version", help="Print version and exit")
    return parser


def _cmd_check_licenses(args: argparse.Namespace) -> int:
    report = licenses.audit_installed(strict=args.strict)
    if args.json:
        print(licenses.report_as_json(report))
    else:
        print(licenses.format_report(report))
    return EXIT_OK if report.passed else EXIT_LICENSE


def _cmd_status(args: argparse.Namespace, settings: Settings) -> int:
    """Report configuration and provider posture without contacting providers."""
    from src.config.providers import get_registry

    registry = get_registry()
    missing = settings.missing_providers(registry.required_credentials())
    payload = {
        "service": settings.service_name,
        "db_path": settings.db_path,
        "raw_db_path": settings.raw_db_path,
        "providers_missing_credentials": missing,
        "redistribution_blockers": [
            p.name for p in registry.redistribution_blockers()
        ],
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
