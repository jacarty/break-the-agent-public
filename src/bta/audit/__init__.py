"""Tool call audit records — VER-4, `schemas.md` §6.

One record per tool call, at every level, including calls that were denied.
The record is built and emitted by the tool boundary (`bta.tools.boundary`) and
never by a tool: a tool that forgot would be a silent hole in VER-4, and this
package deliberately offers nothing a tool could call if it tried. `ToolContext`
does not carry a sink.

Two handling rules travel with these records and are not negotiable.

**Parameters may contain attacker-supplied text, including injections.** They
are stored as data for analysis and must never be re-rendered into any model
context, including a later analysis run (`schemas.md` §6). The field is named
and typed so that a consumer has to reach for it deliberately.

**No credential material, no secret values, no flag values** (VER-5, SEC-9).
Serialisation below is an explicit field list rather than a dump of whatever
the object happens to hold, which is why a credential added to a context in
some later phase cannot arrive in a record by inheritance.

That guarantee covers everything the *process* puts in a record. It does not
cover `parameters`, and the difference is worth stating rather than leaving to
be discovered. No tool declares a parameter that holds credential material, but
every tool takes free text, and at L1 the Onboarding agent holds the
integration key in its system prompt (FR-14) — so a steered model calling
`check_policy(topic="<F4 value>")` puts a flag value into a record, and nothing
here can recognise it. Only the verifier may hold flag values
(`schemas.md` §3), so nothing in this package could redact them even in
principle.

**Audit records are therefore handled at the same sensitivity as raw
transcripts**: restricted storage, never committed, never published unredacted.
Whether VER-6's redaction stage grows to cover them is a decision for the phase
that builds the verifier, recorded as TD-14 rather than settled here.

**An errored call records `authorisation_outcome: allowed`.** `schemas.md` §6
closes that enum and has no member for a call that failed without any control
refusing, and inventing a denial for it would put fabricated evidence in the
record. The pair to read is `(allowed, error_class is not None)`; a consumer
that treats `allowed` alone as success will miscount.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from bta.models import Agent, AuthorisationOutcome, CredentialMode, Level

SCHEMA_VERSION = 1

#: Placeholder written in place of a parameter a tool declared as redacted.
#: Substitution rather than omission, for the same reason VER-6 substitutes in
#: transcripts: an absent field does not record that something was there.
REDACTED = "[redacted]"


def call_errored(
    authorisation_outcome: AuthorisationOutcome | str | None, error_class: str | None
) -> bool:
    """Did this call fail without any control refusing?

    The pair the module header warns about, in one place, because every
    consumer that got it wrong got it wrong the same way — by reading
    `authorisation_outcome` alone and seeing `allowed`.

    **`error_class` on its own is not the test.** A genuine `ToolDenied` sets
    both an outcome and an `error_class`, so "there was an error class" would
    report every L3 denial as a breakage — the opposite miscount, and the more
    damaging one, since it would erase the denials the L3 column is made of.

    Accepts the enum or its string value: an `AuditRecord` holds the former and
    a serialised `ToolCallEvent` holds the latter, and both are consumers.
    """
    if error_class is None:
        return False
    value = (
        authorisation_outcome.value
        if isinstance(authorisation_outcome, AuthorisationOutcome)
        else authorisation_outcome
    )
    return value == AuthorisationOutcome.ALLOWED.value


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """`schemas.md` §6, field for field."""

    id: str
    ts: datetime
    session_id: str
    persona_sub: str
    team: str
    level: Level
    agent: Agent
    tool: str
    parameters: Mapping[str, Any]
    credential_mode: CredentialMode | None
    authorisation_outcome: AuthorisationOutcome
    run_id: str | None = None
    rows_returned: int | None = None
    bytes_returned: int | None = None
    error_class: str | None = None
    schema_version: int = field(default=SCHEMA_VERSION)

    @property
    def errored(self) -> bool:
        """See `call_errored`. A record, not a pair, for callers that hold one."""
        return call_errored(self.authorisation_outcome, self.error_class)

    def to_dict(self) -> dict[str, Any]:
        """An explicit whitelist. Every field named here is one somebody chose
        to write down."""
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "ts": _iso(self.ts),
            "run_id": self.run_id,
            "session_id": self.session_id,
            "persona_sub": self.persona_sub,
            "team": self.team,
            "level": self.level.value,
            "agent": self.agent.value,
            "tool": self.tool,
            "parameters": dict(self.parameters),
            "credential_mode": (
                self.credential_mode.value if self.credential_mode is not None else None
            ),
            "authorisation_outcome": self.authorisation_outcome.value,
            "rows_returned": self.rows_returned,
            "bytes_returned": self.bytes_returned,
            "error_class": self.error_class,
        }


def _iso(ts: datetime) -> str:
    """ISO 8601, UTC, millisecond precision — `schemas.md` conventions.

    `astimezone(UTC)`, not `astimezone(tz=ts.tzinfo)`: the latter is a no-op on
    an aware datetime and attaches the *local* offset to a naive one, so a
    record's timestamp would silently carry whatever timezone the caller's
    clock was in. `ToolContext.now` is injectable and phase 3's loop is exactly
    the caller that will inject one.
    """
    return ts.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AuditSink(Protocol):
    """Where records go. Cloud Logging in a deployment, a list in a test."""

    def emit(self, record: AuditRecord) -> None: ...


class InMemorySink:
    """The sink the harness and the tests use.

    Not a test double living in the test tree: the phase 2 harness needs to
    print what it recorded, and a sink that only exists under pytest would mean
    the harness exercised a different emit path from the one the tests check.
    """

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def emit(self, record: AuditRecord) -> None:
        self.records.append(record)

    def __len__(self) -> int:
        return len(self.records)


__all__ = [
    "REDACTED",
    "SCHEMA_VERSION",
    "AuditRecord",
    "AuditSink",
    "InMemorySink",
    "call_errored",
]
