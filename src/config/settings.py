"""Typed configuration for Aetheris.

All settings are sourced from environment variables, or an optional TOML file
pointed to by ``AETHERIS_CONFIG``. Secrets come from the environment only and
are never written to disk (constitution: Secrets).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


def _load_env_file() -> None:
    """Load a local secret file into the environment, without overriding it.

    The constitution sanctions *"environment variables or local secret files that
    are git-ignored"*. `.env` is git-ignored, so this makes the sanctioned path
    usable without requiring `set -a && . ./.env` in every shell.

    Precedence matters and is deliberate: a value already present in the real
    environment always wins over the file. Otherwise a stale `.env` could
    silently override an explicitly-exported credential — the kind of surprise
    that is very hard to debug from a failed auth error.
    """
    path = _env("AETHERIS_ENV_FILE", ".env")
    if not path or not Path(path).is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency always installed
        return

    # override=False is the whole point: exported variables take precedence.
    #
    # The try/except is not defensive noise. A malformed or non-UTF-8 secret file
    # makes python-dotenv raise, and that would turn every command into a stack
    # trace — a corrupted local file would break the CLI in a way that looks like
    # a program bug rather than a bad credential file. Degrade instead.
    try:
        load_dotenv(dotenv_path=path, override=False)
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the CLI
        print(
            f"warning: could not read credential file {path}: {exc}",
            file=sys.stderr,
        )
        return


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the Aetheris pipeline.

    Every field is populated from the environment or the TOML config file; there
    are no hard-coded credentials anywhere in this module.
    """

    # --- storage ---------------------------------------------------------
    db_path: str = "./aetheris.duckdb"
    raw_db_path: str = "./aetheris-raw.db"

    # --- provider credentials (environment only) --------------------------
    fred_api_key: str | None = None
    alpha_vantage_api_key: str | None = None
    finnhub_api_key: str | None = None
    fmp_api_key: str | None = None
    tiingo_api_key: str | None = None

    # --- orchestration (FR-003) ------------------------------------------
    retry_base_delay_ms: int = 1000
    retry_multiplier: float = 2.0
    retry_max_attempts: int = 5
    retry_jitter: bool = True
    activity_timeout_s: float = 300.0

    # --- telemetry -------------------------------------------------------
    otlp_endpoint: str | None = None
    metrics_port: int = 9464
    service_name: str = "aetheris"

    # --- evaluation ------------------------------------------------------
    mlflow_tracking_uri: str | None = None

    # --- caching / scale -------------------------------------------------
    cache_default_ttl_s: int = 3600
    dry_run: bool = False

    # --- internals -------------------------------------------------------
    config_path: str | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, config_path: str | None = None) -> Settings:
        """Build settings from a TOML file (if any) with the environment overriding."""
        _load_env_file()
        path = config_path or _env("AETHERIS_CONFIG")
        data: dict[str, Any] = {}
        if path:
            p = Path(path)
            if not p.is_file():
                raise ConfigError(f"AETHERIS_CONFIG points at a missing file: {path}")
            with p.open("rb") as fh:
                data = tomllib.load(fh)

        values: dict[str, Any] = {k: v for k, v in data.items() if k in _field_names()}

        # Environment always wins over file.
        env_map: dict[str, Any] = {
            "db_path": _env("AETHERIS_DB", values.get("db_path")),
            "raw_db_path": _env("AETHERIS_RAW_DB", values.get("raw_db_path")),
            "fred_api_key": _env("FRED_API_KEY", values.get("fred_api_key")),
            "alpha_vantage_api_key": _env(
                "ALPHA_VANTAGE_API_KEY", values.get("alpha_vantage_api_key")
            ),
            "finnhub_api_key": _env("FINNHUB_API_KEY", values.get("finnhub_api_key")),
            "fmp_api_key": _env("FMP_API_KEY", values.get("fmp_api_key")),
            "tiingo_api_key": _env("TIINGO_API_KEY", values.get("tiingo_api_key")),
            "retry_base_delay_ms": _env_int(
                "AETHERIS_RETRY_BASE_DELAY_MS", values.get("retry_base_delay_ms", 1000)
            ),
            "retry_multiplier": _env_float(
                "AETHERIS_RETRY_MULTIPLIER", values.get("retry_multiplier", 2.0)
            ),
            "retry_max_attempts": _env_int(
                "AETHERIS_RETRY_MAX_ATTEMPTS", values.get("retry_max_attempts", 5)
            ),
            "retry_jitter": _env_bool(
                "AETHERIS_RETRY_JITTER", bool(values.get("retry_jitter", True))
            ),
            "activity_timeout_s": _env_float(
                "AETHERIS_ACTIVITY_TIMEOUT_S", values.get("activity_timeout_s", 300.0)
            ),
            "otlp_endpoint": _env(
                "OTEL_EXPORTER_OTLP_ENDPOINT", values.get("otlp_endpoint")
            ),
            "metrics_port": _env_int(
                "AETHERIS_METRICS_PORT", values.get("metrics_port", 9464)
            ),
            "service_name": _env("OTEL_SERVICE_NAME", values.get("service_name", "aetheris")),
            "mlflow_tracking_uri": _env(
                "MLFLOW_TRACKING_URI", values.get("mlflow_tracking_uri")
            ),
            "cache_default_ttl_s": _env_int(
                "AETHERIS_CACHE_TTL_S", values.get("cache_default_ttl_s", 3600)
            ),
            "dry_run": _env_bool("AETHERIS_DRY_RUN", bool(values.get("dry_run", False))),
        }

        merged: dict[str, Any] = {}
        for f in fields(cls):
            if f.name in ("config_path",):
                continue
            if f.name in env_map and env_map[f.name] is not None:
                merged[f.name] = env_map[f.name]
            elif f.name in values:
                merged[f.name] = values[f.name]
        merged["config_path"] = path
        return cls(**merged)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    def provider_credentials(self) -> dict[str, str]:
        """Non-empty provider credentials, keyed by registry provider name."""
        raw = {
            "fred": self.fred_api_key,
            "alpha_vantage": self.alpha_vantage_api_key,
            "finnhub": self.finnhub_api_key,
            "fmp": self.fmp_api_key,
            "tiingo": self.tiingo_api_key,
        }
        return {k: v for k, v in raw.items() if v}

    def missing_providers(self, required: list[str]) -> list[str]:
        """Providers in ``required`` that have no credential configured."""
        have = set(self.provider_credentials())
        return [p for p in required if p not in have]

    def redacted(self) -> dict[str, Any]:
        """Settings as a dict with every credential replaced by a presence flag."""
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name == "config_path":
                continue
            val = getattr(self, f.name)
            if "api_key" in f.name:
                out[f.name] = "***set***" if val else None
            else:
                out[f.name] = val
        return out


def _field_names() -> set[str]:
    return {f.name for f in fields(Settings)}


def get_settings(config_path: str | None = None) -> Settings:
    """Convenience loader used across the codebase."""
    return Settings.load(config_path)
