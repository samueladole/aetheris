"""Trigger surface (FR-016).

Spec §4 step 1: "Pipeline schedule or event triggers the Workflow Orchestrator."
Two trigger kinds are supported, matching contracts/cli.md's
``--trigger schedule|event`` flags. Both ultimately start the same durable
workflow; they differ only in provenance, which is recorded as a workflow
attribute so runs can be attributed afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from src.config.settings import Settings


class TriggerKind(StrEnum):
    SCHEDULE = "schedule"
    EVENT = "event"


class TriggerError(ValueError):
    """Raised when a trigger specification is unknown."""


@dataclass(frozen=True)
class Trigger:
    """Where a run came from."""

    kind: TriggerKind
    detail: str = ""

    def describe(self) -> str:
        return f"{self.kind.value}{':' + self.detail if self.detail else ''}"


def parse_trigger(raw: str | TriggerKind | Trigger) -> Trigger:
    """Parse ``schedule``, ``event``, ``event:foo`` into a :class:`Trigger`."""
    if isinstance(raw, Trigger):
        return raw
    if isinstance(raw, TriggerKind):
        return Trigger(raw)

    text = str(raw).strip().lower()
    if not text:
        return Trigger(TriggerKind.SCHEDULE)
    kind, _, detail = text.partition(":")
    try:
        parsed = TriggerKind(kind)
    except ValueError as exc:
        raise TriggerError(
            f"unknown trigger {raw!r}; expected one of "
            f"{[k.value for k in TriggerKind]}"
        ) from exc
    return Trigger(parsed, detail.strip())


class ScheduleTrigger:
    """Schedule-based trigger.

    A schedule entry pairs a cron-like expression with the cadence it implies.
    Aetheris deliberately does not ship a cron daemon: the CLI is invoked by any
    external scheduler (cron, launchd, systemd timer, GitHub Actions), which
    keeps zero external services running (constitution).
    """

    def __init__(self, expressions: dict[str, str] | None = None) -> None:
        # cadence -> cron expression
        self._expressions = {
            "hourly": "0 * * * *",
            "daily": "0 6 * * *",
            "weekly": "0 6 * * 1",
            **(expressions or {}),
        }

    def cadences(self) -> tuple[str, ...]:
        return tuple(sorted(self._expressions))

    def expression(self, cadence: str) -> str:
        try:
            return self._expressions[cadence]
        except KeyError as exc:
            raise TriggerError(
                f"unknown cadence {cadence!r}; known: {sorted(self._expressions)}"
            ) from exc

    def install_note(self) -> str:
        """Human-readable snippet for wiring this into a real scheduler."""
        lines = ["# Example crontab lines for the Aetheris pipeline:"]
        for cadence in self.cadences():
            lines.append(f"# {self.expression(cadence)}   aetheris run --trigger schedule")
        return "\n".join(lines)


class EventTrigger:
    """Event-based trigger.

    Events are a flat set of named sources; anything that can invoke a command
    is a valid source. The set is intentionally open — the constitution's
    "future agentic interactions" requirement means event producers are not
    knowable today.
    """

    DEFAULT_EVENTS = ("manual", "provider_release", "webhook", "agent_signal")

    def __init__(self, events: tuple[str, ...] = DEFAULT_EVENTS) -> None:
        self._events = events

    def events(self) -> tuple[str, ...]:
        return self._events

    def accepts(self, detail: str) -> bool:
        return detail in self._events


def iter_triggers(settings: Settings | None = None) -> Iterator[Trigger]:
    """Yield every configured trigger source.

    Used by ``aetheris status`` to report the trigger surface, and by tests.
    """
    schedule = ScheduleTrigger()
    for cadence in schedule.cadences():
        yield Trigger(TriggerKind.SCHEDULE, cadence)
    for event in EventTrigger().events():
        yield Trigger(TriggerKind.EVENT, event)


__all__ = [
    "EventTrigger",
    "ScheduleTrigger",
    "Trigger",
    "TriggerError",
    "TriggerKind",
    "iter_triggers",
    "parse_trigger",
]
