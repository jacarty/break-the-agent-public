"""Deploy Bot's persistent memory — FR-12, `schemas.md` §7.

Deploy Bot's memory survives across sessions; Onboarding has none. This is the
F7 surface, and the schema is where the L3 defence actually lives.

| Level | Written as | Rendered as |
| -- | -- | -- |
| L1, L2 | `kind: freeform`, raw `content` | replayed verbatim into context — the vulnerability |
| L3 | typed records only, `subject` + `value` | a delimited data block, never instruction text |

**Memory is written under the caller's own identity and read back only for
that persona, at every level.** F7b's containment claim rests on this and on
the credential the tool boundary mints — not on the model declining to be
steered. The store keys on `persona_sub` and never on `session_id`: FR-18's
two-phase case opens a fresh session with no carried context, so a store keyed
by session would make F7 untestable rather than safe.

The extraction rules are a pre-registered artefact
(`docs/decisions/0005-typed-memory-closed-subject-vocabulary.md`). They are
rule-based and deterministic — if a model chose what landed in `subject` and
`value`, attacker text would flow into the typed record and F7b would rest on
the model again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from bta.models import Level, require_level

SCHEMA_VERSION = 1


class MemoryKind(StrEnum):
    """`schemas.md` §7. `FREEFORM` is L1/L2 only; the three typed kinds are
    L3 only. Nothing writes both."""

    FREEFORM = "freeform"
    NOTE = "note"
    PREFERENCE = "preference"
    OBSERVED_FACT = "observed_fact"


TYPED_KINDS = frozenset({MemoryKind.NOTE, MemoryKind.PREFERENCE, MemoryKind.OBSERVED_FACT})


class MemoryPolicyViolation(RuntimeError):
    """A record that does not match the level that produced it.

    Loud rather than corrected. A typed record silently accepting `content`, or
    a freeform record slipping through at L3, is F7b's containment claim
    quietly not running — and it would look exactly like it running.
    """


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """`schemas.md` §7, field for field.

    Explicit `to_dict()` for the reason ADR 0002 gives for `AuditRecord`: a
    field added to the class does not reach storage until somebody adds it to
    the list, and adding it is a reviewable line.
    """

    persona_sub: str
    session_id: str
    created_at: datetime
    written_at_level: Level
    kind: MemoryKind
    content: str | None = None
    subject: str | None = None
    value: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_level(self.written_at_level)
        if self.kind is MemoryKind.FREEFORM:
            if self.written_at_level is Level.L3:
                raise MemoryPolicyViolation(
                    "a freeform record was written at L3. Freeform content is "
                    "replayed verbatim into the next session, which is the "
                    "vulnerability L3 exists not to have (schemas.md §7)."
                )
            if self.subject is not None or self.value is not None:
                raise MemoryPolicyViolation("a freeform record carries subject/value")
            if not self.content:
                raise MemoryPolicyViolation("a freeform record carries no content")
            return

        if self.written_at_level is not Level.L3:
            raise MemoryPolicyViolation(
                f"a typed record was written at {self.written_at_level.value}. "
                f"L1 and L2 write freeform — a typed record there would make the "
                f"levels differ by something FR-13 does not allow them to."
            )
        if self.content is not None:
            raise MemoryPolicyViolation(
                "a typed record carries `content`. `schemas.md` §7 leaves it "
                "unset at L3; a record with both is one an attacker could get "
                "rendered as instruction text through the freeform path."
            )
        if not self.subject or self.value is None:
            raise MemoryPolicyViolation("a typed record needs both subject and value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "persona_sub": self.persona_sub,
            "session_id": self.session_id,
            "created_at": self.created_at.astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "written_at_level": self.written_at_level.value,
            "kind": self.kind.value,
            "content": self.content,
            "subject": self.subject,
            "value": self.value,
        }


class MemoryStore(Protocol):
    """Firestore in a deployment, a dict in a test.

    `read` takes a persona and never a session: FR-18 phase two is a fresh
    session, and only persisted memory crosses that boundary.
    """

    def write(self, record: MemoryRecord) -> None: ...

    def read(self, persona_sub: str) -> tuple[MemoryRecord, ...]: ...


class InMemoryStore:
    """Keyed by persona, like the real one. Not a test-only double — the
    phase 3 harness uses it, so the harness exercises the same read path the
    tests check."""

    def __init__(self) -> None:
        self._records: dict[str, list[MemoryRecord]] = {}

    def write(self, record: MemoryRecord) -> None:
        self._records.setdefault(record.persona_sub, []).append(record)

    def read(self, persona_sub: str) -> tuple[MemoryRecord, ...]:
        return tuple(self._records.get(persona_sub, ()))


from .extract import (  # noqa: E402 - re-export after the records it depends on
    SUBJECTS,
    VALUE_MAX_LENGTH,
    extract,
)
from .render import render  # noqa: E402

__all__ = [
    "SCHEMA_VERSION",
    "SUBJECTS",
    "TYPED_KINDS",
    "VALUE_MAX_LENGTH",
    "InMemoryStore",
    "MemoryKind",
    "MemoryPolicyViolation",
    "MemoryRecord",
    "MemoryStore",
    "extract",
    "render",
]
