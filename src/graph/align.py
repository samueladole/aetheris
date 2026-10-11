"""Backward as-of alignment across disparate data frequencies (T060).

The pipeline joins high-frequency sentiment and prices against low-frequency
macro data, which have very different release lags. This module implements the
one rule that keeps that join honest:

  * a **backward as-of join** with a **strict no-lookahead invariant** — a data
    point may only observe values already published at its own timestamp.

The distinction that matters is *observation time* versus *availability time*.
A CPI print released on 12 February covers January, so it is not observable by
a graph state on 31 January. Conflating the two is the classic way a financial
pipeline leaks future information and silently invalidates every downstream
query.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


class AlignmentError(ValueError):
    """Raised when alignment inputs are unusable, e.g. timezone-naive times."""


@dataclass(frozen=True)
class Observation:
    """One value, carrying both times that matter.

    ``observed_at`` is the period the value describes; ``available_at`` is when
    it became publicly knowable. They are different for every economic series
    with a publication lag, which is exactly why both are required.

    Ordering is by availability time, so sorting a series puts the earliest
    *knowable* value first rather than the earliest *described* one.
    """

    value: object
    observed_at: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        """Implement the __post_init__ special method."""
        self._require_aware(self.observed_at, "observed_at")
        self._require_aware(self.available_at, "available_at")
        if self.available_at < self.observed_at:
            raise AlignmentError(
                f"observation {self.observed_at.isoformat()} is available before it "
                f"was published ({self.available_at.isoformat()}); that is a data "
                "defect, not a legitimate revision"
            )

    @staticmethod
    def _require_aware(value: datetime, field: str) -> None:
        # VR-5: without an unambiguous clock the no-lookahead invariant is
        # unenforceable, so a naive datetime is a defect rather than a
        # convenience.
        if value.tzinfo is None or value.utcoffset() is None:
            raise AlignmentError(
                f"{field} is timezone-naive. The no-lookahead invariant cannot be "
                "enforced without an unambiguous clock."
            )

    @property
    def _order_key(self) -> tuple[datetime, datetime]:
        return (_as_utc(self.available_at), _as_utc(self.observed_at))

    def __lt__(self, other: Observation) -> bool:
        """Implement the __lt__ special method."""
        return self._order_key < other._order_key


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def first_available_at_or_before(
    observations: list[Observation], at: datetime
) -> Observation | None:
    """The most recent observation whose *availability* is at or before ``at``.

    Looks at availability time only, never observation time — this is the
    no-lookahead invariant expressed as a function.
    """
    if at.tzinfo is None or at.utcoffset() is None:
        raise AlignmentError(
            "query time is timezone-naive. The no-lookahead invariant cannot be "
            "enforced without an unambiguous clock."
        )

    cutoff = _as_utc(at)
    eligible = [
        obs
        for obs in observations
        if _as_utc(obs.available_at) <= cutoff
    ]
    if not eligible:
        return None

    # Latest availability wins; ties break on the later observation.
    return max(eligible, key=lambda o: (_as_utc(o.available_at), _as_utc(o.observed_at)))


def align(
    series: dict[str, list[Observation]], *, at: datetime
) -> dict[str, object]:
    """Resolve every series to its world-state at ``at``.

    A series with nothing published yet by ``at`` is simply absent from the
    result — it is never filled forward from the future, and never defaulted to
    a placeholder that would read as a real value.
    """
    if at.tzinfo is None or at.utcoffset() is None:
        raise AlignmentError(
            "query time is timezone-naive. The no-lookahead invariant cannot be "
            "enforced without an unambiguous clock."
        )

    aligned: dict[str, object] = {}
    for name, observations in series.items():
        found = first_available_at_or_before(list(observations), at)
        if found is not None:
            aligned[name] = found.value
    return aligned


def sort_by_availability(observations: list[Observation]) -> list[Observation]:
    """Return observations ordered by availability time, ascending."""
    return sorted(observations, key=lambda o: _as_utc(o.available_at))


__all__ = [
    "AlignmentError",
    "Observation",
    "align",
    "first_available_at_or_before",
    "sort_by_availability",
]
