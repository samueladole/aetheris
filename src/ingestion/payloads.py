"""Immutable raw payload store (FR-015, VR-8).

Spec §4 requires fetch workers to "store raw immutable payloads". The store is
append-only SQLite: a re-ingest with the same ``payload_id`` is a no-op rather
than an overwrite, so history is never destroyed and cassette-based replay,
post-hoc recomputation, and free-tier-quota preservation all remain possible.

The response body is stored verbatim as bytes, alongside a payload schema
version so future schema changes are detectable rather than silent.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_payload (
    payload_id      TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    request_hash    TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,
    body            BLOB NOT NULL,
    fetched_at      TEXT NOT NULL,
    ttl_seconds     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_raw_payload_lookup
    ON raw_payload (provider, endpoint, request_hash);
CREATE INDEX IF NOT EXISTS idx_raw_payload_fetched_at
    ON raw_payload (fetched_at);
"""


class ImmutablePayloadError(RuntimeError):
    """Raised when an operation would violate raw-payload immutability."""


@dataclass(frozen=True)
class RawPayload:
    """One stored provider response."""

    payload_id: str
    provider: str
    endpoint: str
    request_hash: str
    schema_version: int
    body: bytes
    fetched_at: datetime
    ttl_seconds: int | None
    request_params: dict[str, str] | None = None

    def is_stale(self, now: datetime | None = None) -> bool:
        """True when this payload has outlived its TTL."""
        if self.ttl_seconds is None:
            return False
        now = now or datetime.now(UTC)
        age = (now - self.fetched_at).total_seconds()
        return age >= self.ttl_seconds


def build_payload_id(provider: str, endpoint: str, request_hash: str, fetched_at: datetime) -> str:
    """Construct the canonical payload identity (data-model.md §4)."""
    ts = fetched_at.astimezone(UTC).isoformat()
    return f"{provider}:{endpoint}:{request_hash}:{ts}"


class RawPayloadStore:
    """Append-only SQLite store of raw provider responses."""

    def __init__(self, db_path: str) -> None:
        """Initialise the instance."""
        self._db_path = db_path
        self._local = threading.local()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._ensure_request_params_column(conn)

    @staticmethod
    def _ensure_request_params_column(conn: sqlite3.Connection) -> None:
        """Add the nullable ``request_params`` column to older databases.

        Adding a column rewrites no row, so VR-8 (append-only) still holds.
        """
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(raw_payload)")}
        if "request_params" in columns:
            return
        try:
            conn.execute("ALTER TABLE raw_payload ADD COLUMN request_params TEXT")
        except sqlite3.OperationalError as exc:  # concurrent opener won the race
            if "duplicate column" not in str(exc).lower():
                raise

    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    # ------------------------------------------------------------------
    def store(
        self,
        *,
        provider: str,
        endpoint: str,
        request_hash: str,
        body: bytes,
        fetched_at: datetime | None = None,
        ttl_seconds: int | None = None,
        request_params: Mapping[str, str] | None = None,
    ) -> tuple[RawPayload, bool]:
        """Append a payload. Returns ``(payload, created)``.

        ``created`` is False when a payload with the same identity already
        existed — in that case the stored (original) row is returned unmodified,
        preserving immutability rather than overwriting.
        """
        fetched_at = (fetched_at or datetime.now(UTC)).astimezone(UTC)
        pid = build_payload_id(provider, endpoint, request_hash, fetched_at)
        params_json = (
            json.dumps(dict(request_params), sort_keys=True)
            if request_params is not None
            else None
        )

        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO raw_payload "
                "(payload_id, provider, endpoint, request_hash, schema_version, body,"
                " fetched_at, ttl_seconds, request_params) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    provider,
                    endpoint,
                    request_hash,
                    SCHEMA_VERSION,
                    body,
                    fetched_at.isoformat(),
                    ttl_seconds,
                    params_json,
                ),
            )
            created = cur.rowcount == 1
            row = conn.execute(
                "SELECT * FROM raw_payload WHERE payload_id = ?", (pid,)
            ).fetchone()
        return self._to_model(row), created

    def get(self, payload_id: str) -> RawPayload | None:
        """Handle the get."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM raw_payload WHERE payload_id = ?", (payload_id,)
            ).fetchone()
        return self._to_model(row) if row else None

    def latest(
        self, provider: str, endpoint: str, request_hash: str
    ) -> RawPayload | None:
        """Most recently fetched payload for a provider/endpoint/request tuple."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM raw_payload "
                "WHERE provider = ? AND endpoint = ? AND request_hash = ? "
                "ORDER BY fetched_at DESC LIMIT 1",
                (provider, endpoint, request_hash),
            ).fetchone()
        return self._to_model(row) if row else None

    def latest_by_endpoint(self, provider: str, endpoint: str) -> list[RawPayload]:
        """Newest payload per ``request_hash``, ordered by ``fetched_at`` descending."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM raw_payload AS r "
                "WHERE provider = ? AND endpoint = ? AND fetched_at = ("
                " SELECT MAX(fetched_at) FROM raw_payload "
                " WHERE provider = r.provider AND endpoint = r.endpoint "
                " AND request_hash = r.request_hash) "
                "ORDER BY fetched_at DESC",
                (provider, endpoint),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def count(self, provider: str | None = None) -> int:
        """Handle the count."""
        sql = "SELECT COUNT(*) FROM raw_payload"
        params: tuple[str, ...] = ()
        if provider:
            sql += " WHERE provider = ?"
            params = (provider,)
        with self._connect() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    @staticmethod
    def _to_model(row: sqlite3.Row) -> RawPayload:
        return RawPayload(
            payload_id=row["payload_id"],
            provider=row["provider"],
            endpoint=row["endpoint"],
            request_hash=row["request_hash"],
            schema_version=row["schema_version"],
            body=row["body"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            ttl_seconds=row["ttl_seconds"],
            request_params=(
                json.loads(row["request_params"]) if row["request_params"] else None
            ),
        )

    def close(self) -> None:
        """Handle the close."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
