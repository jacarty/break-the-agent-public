"""What a session produces — FR-5, and the fields `schemas.md` §5 needs.

The loop is the only thing that can produce `turn_count`, `tool_call_ids` and
`token_usage`, so the record is transcribed here rather than in phase 5 with
the runner that consumes it — ADR 0002's rule that a record arrives with its
consumer, applied the other way round.

FR-5 requires the transcript to show tool calls **with parameters and row
counts**, because that is what makes the `tool_result` disclosure channel
visible to a human (VER-1a). A transcript that showed only assistant text would
hide the lab's central claim: the model held the line and the tool handed the
data over anyway.

**Two handling rules travel with a transcript**, and they are the same ones
that travel with an audit record.

`ToolCallEvent.parameters` may contain attacker-supplied text including
injections. Stored as data, never re-rendered into any model context, including
a later analysis run (`schemas.md` §6).

A raw transcript may contain live flag values — that is the point of it — so it
goes to the evidence bucket under restricted IAM and is never committed. The
redacted copy is VER-6's, produced inside the verifier, which is the only
component permitted to hold flag values (`schemas.md` §3). Nothing here
redacts, and nothing here should learn how.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """One tool call, and what it returned.

    `result_payload` is VER-1a's `tool_result` channel — TD-19. Until it
    existed nothing in the tree persisted a tool result, so
    `captures_tool_result` in `schemas.md` §8 could only ever be zero, and a
    silent zero reads as "the tool never handed data over" rather than "never
    measured". §8 calls a non-zero value there "the lab's central claim in one
    row", so a column of zeroes that looks like a result was not an option.

    **It carries the payload the tool actually returned**, before any L2
    mitigation. That is deliberate: the `tool_result` channel measures whether
    the *tool* disclosed the data, which is a fact about the authorisation
    architecture and not about what the model was subsequently shown.
    `withheld_from_model` records the mitigation's effect alongside it, so the
    two questions stay separable — a payload withheld from the model at L2 is
    still a disclosure if the transcript renders it to the attacker (FR-5).

    A payload may therefore contain a live flag value. That makes a raw
    transcript flag-bearing, which it already was, and VER-6's redaction is
    what publication reads instead.
    """

    audit_id: str
    tool: str
    parameters: dict[str, Any]
    authorisation_outcome: str
    rows_returned: int | None
    bytes_returned: int | None
    error_class: str | None
    result_payload: str | None = None
    withheld_from_model: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "tool": self.tool,
            "parameters": dict(self.parameters),
            "authorisation_outcome": self.authorisation_outcome,
            "rows_returned": self.rows_returned,
            "bytes_returned": self.bytes_returned,
            "error_class": self.error_class,
            "result_payload": self.result_payload,
            "withheld_from_model": self.withheld_from_model,
        }


@dataclass(frozen=True, slots=True)
class TurnEvent:
    role: str
    text: str
    withheld_reason: str | None = None
    tool_calls: tuple[ToolCallEvent, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "withheld_reason": self.withheld_reason,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
        }


@dataclass(slots=True)
class Transcript:
    """One session. Mutable while the loop runs, serialised once at the end."""

    session_id: str
    agent: str
    level: str
    persona_sub: str
    model_requested: str
    model_served: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    events: list[TurnEvent] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None

    @property
    def turn_count(self) -> int:
        return len(self.events)

    @property
    def tool_call_ids(self) -> tuple[str, ...]:
        return tuple(call.audit_id for event in self.events for call in event.tool_calls)

    def assistant_text(self) -> str:
        """Every assistant turn, joined. What the verifier scans for a
        `model_output` capture (VER-1a) — and only assistant turns, because a
        flag that appeared in a tool result and never in the model's own words
        is the other channel and must not be folded into this one."""
        return "\n".join(event.text for event in self.events if event.role == "assistant")

    def tool_result_text(self) -> str:
        """Every tool result payload, joined. VER-1a's `tool_result` channel.

        Kept strictly apart from `assistant_text()`. Folding the two would make
        every tool disclosure a `defeated` cell and lose the distinction the
        lab exists to make: `model_output` is verdict-bearing, this is recorded
        alongside and never folded into a verdict (`schemas.md` §1.3, §8).
        """
        return "\n".join(
            call.result_payload
            for event in self.events
            for call in event.tool_calls
            if call.result_payload
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "agent": self.agent,
            "level": self.level,
            "persona_sub": self.persona_sub,
            "model": {"requested": self.model_requested, "served": self.model_served},
            "started_at": self.started_at.astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "turn_count": self.turn_count,
            "tool_call_ids": list(self.tool_call_ids),
            "token_usage": {"input": self.input_tokens, "output": self.output_tokens},
            "events": [event.to_dict() for event in self.events],
            "error": self.error,
        }


__all__ = ["SCHEMA_VERSION", "ToolCallEvent", "Transcript", "TurnEvent"]
