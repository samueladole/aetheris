"""Aetheris command-line interface.

Phase 1 implementation wires the global flags, exit codes, and the license gate
command (T015). The pipeline commands (`run`, `ingest`, `score`,
`graph upsert`, `query`, `evaluate`, `status`) are registered incrementally by
T024, T039, T051, T064, and T073 — each task adds its subcommand here.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
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
    """Build the parser."""
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

    # -- graph (T064) --------------------------------------------------
    grp = sub.add_parser("graph", parents=[parent], help="Graph upsert and read-only queries")
    grp_sub = grp.add_subparsers(dest="graph_command", metavar="<subcommand>")

    up = grp_sub.add_parser(
        "upsert", parents=[parent], help="Align and upsert into the knowledge graph"
    )
    up.add_argument("--since", metavar="ISO8601", help="Start of the window")
    up.add_argument("--until", metavar="ISO8601", help="End of the window")
    up.add_argument(
        "--rebuild-edges",
        action="store_true",
        help="Recompute derived edges for the window (idempotent by primary key)",
    )

    q = grp_sub.add_parser(
        "query", parents=[parent], help="Read-only graph query"
    )
    q.add_argument(
        "kind",
        choices=["entity", "sentiment", "edges", "influencers", "assets"],
        help="Which query to run",
    )
    q.add_argument("--id", metavar="NODE_ID", help="Node id for entity/edges/influencers")
    q.add_argument("--entity", metavar="ENTITY_ID", help="Entity for sentiment series")
    q.add_argument("--from", dest="from_id", metavar="NODE_ID", help="Source node for edges")
    q.add_argument("--rel", metavar="REL_TYPE", help="Filter by relation type")
    q.add_argument("--start", metavar="ISO8601", help="Window start (sentiment)")
    q.add_argument("--end", metavar="ISO8601", help="Window end (sentiment)")
    q.add_argument(
        "--min-confidence", type=float, default=0.0, metavar="FLOAT",
        help="Minimum edge or score confidence",
    )
    q.add_argument("--top", type=int, default=10, metavar="N", help="Result limit")
    q.add_argument("--class", dest="asset_class", metavar="CLASS", help="Filter assets by class")

    # -- score (T051) --------------------------------------------------
    sc = sub.add_parser(
        "score", parents=[parent], help="Score ingested news text with the sentiment model"
    )
    sc.add_argument("--entity", metavar="ENTITY_ID", help="Limit to one entity")
    sc.add_argument("--since", metavar="ISO8601", help="Only score text from this time")
    sc.add_argument(
        "--model",
        default="stub",
        help="Model id from the permitted manifest (default: stub)",
    )
    sc.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="Calibration temperature for confidence (VR-2)",
    )
    sc.add_argument(
        "--text",
        action="append",
        default=[],
        metavar="TEXT",
        help="Score literal text instead of reading ingested payloads (repeatable)",
    )

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


def _run_gate(argv: list[str], *, name: str) -> tuple[bool, str]:
    """Run one gate, returning (passed, output). Never raises."""
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

    # The site must ALWAYS be built; --no-gates skips the *checks*, not the
    # build. Skipping the build would leave `serve` and the a11y gates (which
    # run over site/) with nothing to work on.
    ok, detail = _run_gate(_MKDOCS_BUILD, name="site build (FR-010)")
    if not ok:
        print(f"error: {detail}", file=sys.stderr)
        return EXIT_LICENSE

    if args.no_gates:
        print(
            "warning: --no-gates skips the docstring, secret, and accessibility "
            "checks (development convenience; never usable in CI)",
            file=sys.stderr,
        )
    else:
        ok, detail = _run_gate(_DOCSTRING_GATE, name="docstring gate (FR-006)")
        if not ok:
            print(f"error: {detail}", file=sys.stderr)
            return EXIT_LICENSE

        # VR-5 / FR-017: the published site is publicly readable, so a
        # credential in the output is a security exposure, not a cosmetic
        # defect. Runs on the built site, because build steps can introduce
        # values that are not visible in any single source file.
        ok, detail = _run_gate(
            [sys.executable, "scripts/scan_secrets.py", "site/"],
            name="secret scan (FR-017)",
        )
        if not ok:
            print(f"error: {detail}", file=sys.stderr)
            return EXIT_LICENSE

    # Repair defects the theme and generator introduce in the built HTML
    # before the structural gate judges it.
    ok, detail = _run_gate(
        ["node", "scripts/fix_built_html.js", "site/"],
        name="landmark label fix",
    )
    if not ok:
        print(f"error: {detail}", file=sys.stderr)
        return EXIT_RUNTIME

    if not args.no_gates:
        for argv, name in [
            (["npx", "html-validate", "site/**/*.html", "--formatter", "stylish"],
             "accessibility gate — structural (FR-019)"),
            (["node", "scripts/check_a11y_rendered.js", "site/"],
             "accessibility gate — rendered (FR-019)"),
        ]:
            ok, detail = _run_gate(argv, name=name)
            if not ok:
                print(f"error: {detail}", file=sys.stderr)
                return EXIT_LICENSE

    print("Documentation built into site/")
    return EXIT_OK


def _cmd_score(args: argparse.Namespace, settings: Settings) -> int:
    """`aetheris score` — turn ingested news text into sentiment payloads (T051)."""
    from datetime import datetime

    from src.observability.telemetry import init_telemetry, span
    from src.sentiment.analyzer import score_text
    from src.sentiment.models import ModelManifestError
    from src.sentiment.schema import (
        Calibration,
        Provenance,
        build_payload,
    )

    init_telemetry(settings)

    # Refuse an unlisted model BEFORE scoring anything: an absent model has no
    # verified license (Principle I, V3).
    if args.model != "stub":
        from src.sentiment.models import get as get_model

        try:
            get_model(args.model)
        except ModelManifestError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_LICENSE

    if args.temperature <= 0:
        print("error: --temperature must be positive", file=sys.stderr)
        return EXIT_USAGE

    if not args.text:
        print(
            "error: no text to score. Pass --text '...' (repeatable), or run "
            "`aetheris ingest` first so there are payloads to read.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    entity = args.entity or "equity:UNKNOWN:UNLISTED"
    if args.since:
        # Literal --text carries no timestamps, so --since cannot filter it.
        # It is accepted here for contract compatibility with the future
        # payload-reading path (task T065).
        try:
            datetime.fromisoformat(args.since)
        except ValueError as exc:
            print(f"error: --since is not ISO-8601: {exc}", file=sys.stderr)
            return EXIT_USAGE

    payloads = []
    with span(
        "sentiment.score",
        **{"aetheris.model": args.model, "aetheris.entity": entity},
    ):
        for index, text in enumerate(args.text):
            # T052: one span per inference, so slow scores are attributable.
            with span("sentiment.inference", **{"aetheris.index": index}):
                polarity, _probs, spec = score_text(
                    text, model_id=args.model, temperature=args.temperature
                )

            payload = build_payload(
                entity_id=entity,
                polarity_score=float(polarity),
                confidence=polarity.confidence,
                timestamp=datetime.now(UTC),
                source_reference=f"cli:text:{index}",
                model=spec,
                calibration=Calibration(
                    method="temperature_scaling",
                    temperature=args.temperature,
                    calibrated_on="cli",  # fitted value recorded at deploy time
                ),
                provenance=Provenance(
                    payload_id=f"cli:text:{index}",
                    text_hash=_hash(text),
                    text_length_chars=len(text),
                ),
            )
            payloads.append(payload)

    if args.json:
        import json

        print(json.dumps(payloads, indent=2))
    else:
        for payload in payloads:
            print(
                f"{payload['entity_id']} score={payload['polarity_score']:+.4f} "
                f"confidence={payload['confidence']:.4f} "
                f"model={payload['model']['id']}"
            )
    return EXIT_OK


def _hash(text: str) -> str:
    from src.sentiment.schema import text_hash

    return text_hash(text)


def _parse_iso(value: str, label: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, refusing anything ambiguous."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"{label} is not ISO-8601: {exc}") from exc
    if parsed.tzinfo is None:
        raise ConfigError(
            f"{label} is timezone-naive. The no-lookahead invariant (VR-5) cannot "
            "be enforced without an unambiguous clock — pass e.g. 2026-10-01T00:00:00+00:00."
        )
    return parsed


def _cmd_graph(args: argparse.Namespace, settings: Settings) -> int:
    """`aetheris graph upsert|query` (T064)."""
    if args.graph_command is None:
        # No subcommand: print help rather than reaching into a Namespace
        # attribute that does not exist.
        print("usage: aetheris graph {upsert,query} ...", file=sys.stderr)
        return EXIT_USAGE

    from src.graph.api import (
        GraphAPIError,
        InvalidWindowError,
        edges_from,
        get_entity,
        list_assets,
        sentiment_for,
        top_influencers,
    )
    from src.graph.schema import get_database
    from src.observability.telemetry import annotate, init_telemetry, span

    if args.graph_command == "upsert":
        return _cmd_graph_upsert(args, settings)

    init_telemetry(settings)
    db = get_database(settings.db_path)

    with span("graph.query", **{"aetheris.query": args.kind}):
        try:
            rows = _run_query(args, db, sentiment_for, edges_from, get_entity,
                              top_influencers, list_assets)
        except (GraphAPIError, InvalidWindowError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE

    if not 0.0 <= args.min_confidence <= 1.0:
        print("error: --min-confidence must be within [0.0, 1.0]", file=sys.stderr)
        return EXIT_USAGE

    annotate(**{"aetheris.rows": len(rows) if isinstance(rows, list) else 0})

    if args.json:
        import json

        print(json.dumps(rows, indent=2, default=str))
    elif rows is None:
        print("(not found)")
    elif not rows:
        print("(no results)")
    else:
        for row in rows:
            print(row if isinstance(row, str) else _format_row(row))
    return EXIT_OK


def _format_row(row: dict) -> str:
    """One line per row, readable without a JSON viewer."""
    if "node_type" in row and "node_id" in row:
        return f"{row['node_id']}  ({row['node_type']})"
    if "score" in row and "timestamp" in row:
        return (
            f"{row['timestamp']}  score={float(row['score']):+.4f}  "
            f"confidence={float(row['confidence']):.4f}"
        )
    if "src_node" in row and "dst_node" in row:
        return (
            f"{row['src_node']} —[{row['rel_type']} w={float(row['weight']):+.4f}"
            f" c={float(row['confidence']):.4f}]→ {row['dst_node']}"
        )
    if "asset_id" in row:
        return f"{row['asset_id']}  ({row.get('asset_class', '?')})"
    return ", ".join(f"{k}={v}" for k, v in row.items())


def _run_query(args, db, sentiment_for, edges_from, get_entity,
               top_influencers, list_assets):
    """Dispatch one read-only query, returning rows or a formatted entity."""
    if args.kind == "entity":
        entity = get_entity(args.id, db=db) if args.id else None
        if entity is None:
            return None
        return [
            {
                "node_id": entity.node_id,
                "node_type": entity.node_type,
                **entity.attributes,
            }
        ]

    if args.kind == "sentiment":
        start = _parse_iso(args.start or "1970-01-01T00:00:00+00:00", "--start")
        end = _parse_iso(args.end or "9999-12-31T00:00:00+00:00", "--end")
        return sentiment_for(
            args.entity or args.id,
            start=start,
            end=end,
            min_confidence=args.min_confidence,
            limit=args.top or None,
            db=db,
        )

    if args.kind == "edges":
        node = args.from_id or args.id
        return edges_from(
            node,
            rel_type=args.rel,
            min_confidence=args.min_confidence,
            limit=args.top or None,
            db=db,
        )

    if args.kind == "influencers":
        return top_influencers(
            args.id,
            limit=args.top,
            min_confidence=args.min_confidence or 0.5,
            db=db,
        )

    return list_assets(
        asset_class=args.asset_class, limit=args.top or 100, db=db
    )


def _cmd_graph_upsert(args: argparse.Namespace, settings: Settings) -> int:
    """Align and upsert a window into the graph (T064).

    The pipeline's step 4. It requires already-ingested raw payloads and
    already-scored sentiment, so with neither available the command reports that
    honestly rather than writing an empty graph and calling it success.
    """
    from src.observability.metrics import get_metrics
    from src.observability.telemetry import init_telemetry, span

    init_telemetry(settings)

    since = _parse_iso(args.since, "--since")
    until = _parse_iso(args.until, "--until")


    with span("graph.upsert", **{"aetheris.rebuild": bool(args.rebuild_edges)}):
        # Raw payload storage lives behind the ingestion layer; importing it
        # lazily keeps `aetheris graph query` usable without a DB.
        try:
            from src.ingestion.payloads import RawPayloadStore

            store = RawPayloadStore(settings.raw_db_path)
        except Exception:  # noqa: BLE001 - an absent store is a state, not a crash
            store = None

        if store is None or store.count() == 0:
            print(
                "nothing to upsert: no raw payloads. Run `aetheris ingest` first, "
                "then `aetheris score`, then `aetheris graph upsert`.",
                file=sys.stderr,
            )
            return EXIT_USAGE

        get_metrics().graph_upserts.add(store.count())
        print(
            f"graph upsert: window "
            f"[{since.isoformat() if since else 'beginning'}, "
            f"{until.isoformat() if until else 'now'}] — "
            f"{store.count()} raw payload(s) eligible"
        )
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
    """Handle the main."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already printed help or usage. `--help` exits 0 and a real
        # error exits 2, so the code must not be defaulted with `or`: that
        # idiom turns the legitimate 0 from `--help` into EXIT_USAGE.
        if exc.code is None:
            return EXIT_USAGE
        return int(exc.code)

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
        if args.command == "score":
            return _cmd_score(args, settings)
        if args.command == "graph":
            return _cmd_graph(args, settings)
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
        # Malformed user input is a usage error, not a runtime failure.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:  # pragma: no cover
        print("\ninterrupted", file=sys.stderr)
        return EXIT_RUNTIME


def replace_db(settings: Settings, field: str, value: str) -> Settings:
    """Dataclasses.replace without importing dataclasses at call sites."""
    from dataclasses import replace

    return replace(settings, **{field: value})


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
