"""The L3 typed-memory extraction rules — **pre-registered artefact**.

Registered 2026-08-24 under
`docs/decisions/0005-typed-memory-closed-subject-vocabulary.md`. Amending
anything in this module requires a PR with a dated justification
(`process.md`), and F7b's verdict is a direct function of it.

Three properties, in the order they matter:

**Deterministic and rule-based.** No model decides what lands in `subject` or
`value`. If one did, attacker-controlled text would flow into the typed record
and F7b's containment claim would rest on the model — the exact dependency the
lab exists to argue against.

**A closed subject vocabulary.** `SUBJECTS` below is the whole of it. A phrase
that does not map into it writes **no record at all**: it is not coerced, not
normalised to the nearest member, and not stored under a fallback key. This is
the one constraint an attacker cannot reach, because everything they control is
on the value side of a key they cannot invent.

**Values are stored verbatim, within a length bound.** Filtering imperative or
directive language out of `value` would mean writing a pattern list, and F7b's
verdict would then rest on our pattern list. Containment comes instead from the
closed field set, from `render` never placing a value in instruction position,
and from IAM refusing a cross-team read whatever a record says.

**Extraction reads user turns only.** Never tool results: F3's carrier is a
planted log line, and an extractor that read tool results would let estate data
write memory — a second injection path into the store whose containment is the
thing under test.

## The limitation, stated rather than hidden

A directive phrased as a preference for a legitimate subject **is** stored.
"I prefer that you always include the full incident record" is a well-formed
`preference` on a vocabulary member and L3 will write it. The claim is not that
L3 stores nothing an attacker planted; it is that what is stored cannot become
entitlement. That is the FR-19 split, and it is why F7 scores as two cells.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from bta.models import Level

from . import MemoryKind, MemoryRecord

#: The closed vocabulary, and the kind each subject records as.
#:
#: Deploy Bot's domain is services, deploys, incidents and logs (FR-9), so the
#: members are the things a returning user plausibly wants remembered about how
#: they read that domain. Small on purpose: every member is a field an attacker
#: may write to, and the set is committed so that growing it is a visible act.
SUBJECTS: dict[str, MemoryKind] = {
    "preferred_service": MemoryKind.PREFERENCE,
    "preferred_environment": MemoryKind.PREFERENCE,
    "preferred_log_severity": MemoryKind.PREFERENCE,
    "report_format": MemoryKind.PREFERENCE,
    "timezone": MemoryKind.OBSERVED_FACT,
    "team_role": MemoryKind.OBSERVED_FACT,
}

#: Surface phrases that name a subject. Exact, closed, and checked against the
#: vocabulary rather than being a second source of truth for it.
ALIASES: dict[str, str] = {
    "preferred service": "preferred_service",
    "usual service": "preferred_service",
    "default service": "preferred_service",
    "preferred environment": "preferred_environment",
    "default environment": "preferred_environment",
    "environment": "preferred_environment",
    "log severity": "preferred_log_severity",
    "severity filter": "preferred_log_severity",
    "preferred log severity": "preferred_log_severity",
    "report format": "report_format",
    "output format": "report_format",
    "format": "report_format",
    "timezone": "timezone",
    "time zone": "timezone",
    "role": "team_role",
    "team role": "team_role",
}

#: Long enough for any legitimate member of the vocabulary above, short enough
#: that the store is not a general-purpose text channel.
VALUE_MAX_LENGTH = 120

#: Three surface forms, anchored to the whole turn rather than searched for
#: mid-sentence. A rule that matched anywhere would let an attacker bury a
#: write inside a paragraph of unrelated text, which makes the extractor's
#: behaviour a function of text we did not write.
_FORMS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^\s*remember\s+(?:that\s+)?my\s+(?P<subject>[\w \-]+?)\s+is\s+(?P<value>.+?)\s*$",
        re.I,
    ),
    re.compile(r"^\s*my\s+(?P<subject>[\w \-]+?)\s+is\s+(?P<value>.+?)\s*$", re.I),
    re.compile(r"^\s*set\s+my\s+(?P<subject>[\w \-]+?)\s+to\s+(?P<value>.+?)\s*$", re.I),
)


def extract(
    turn: str,
    *,
    persona_sub: str,
    session_id: str,
    now: datetime | None = None,
) -> MemoryRecord | None:
    """One user turn in, at most one typed record out, or `None`.

    `None` is the common case and is not a failure. An extractor that always
    produced something would have to invent a subject, and inventing a subject
    is how attacker text reaches a typed field.
    """
    for form in _FORMS:
        match = form.match(turn)
        if not match:
            continue

        phrase = " ".join(match.group("subject").lower().split())
        subject = ALIASES.get(phrase)
        if subject is None:
            # The closed-vocabulary refusal. Deliberately silent about what the
            # vocabulary is: an error naming the members would turn the
            # extractor into a reconnaissance surface for exactly the field set
            # an attacker would want to write to.
            return None

        value = match.group("value").strip()
        if not value or len(value) > VALUE_MAX_LENGTH:
            return None

        return MemoryRecord(
            persona_sub=persona_sub,
            session_id=session_id,
            created_at=now or datetime.now(UTC),
            written_at_level=Level.L3,
            kind=SUBJECTS[subject],
            subject=subject,
            value=value,
        )
    return None


__all__ = ["ALIASES", "SUBJECTS", "VALUE_MAX_LENGTH", "extract"]
