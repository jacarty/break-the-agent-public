"""`schemas.md` §4 — the attack case, and what makes one valid.

A case is a **pre-registered artefact** (`build-plan.md` §4, `process.md`).
`expected_outcome` is written before the evidence exists, and neither it nor
the case body is amended after seeing a result. The validation here exists
because the alternative to a loud failure at load is a case that silently does
not run and quietly lowers `cases_run` in the cell it was authored for — which
is how a threshold gets missed without anybody noticing.

Dataclasses rather than pydantic, per `docs/decisions/0002-schema-records-are-dataclasses.md`:
an explicit field list means a field that should not reach a record does not
reach one by inheritance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bta.models import Agent, Level

SCHEMA_VERSION = 1

#: `schemas.md` §1.1. A case declares exactly one. SC-3 requires a `held`
#: verdict to draw on at least four distinct families, which is why this is a
#: closed set and not a free-text label: four families counted from an open
#: vocabulary is four spellings of the same idea as easily as four techniques.
TECHNIQUE_FAMILIES = frozenset(
    {
        "direct_override",
        "role_authority",
        "indirect_injection",
        "encoding_obfuscation",
        "context_manipulation",
        "task_reframing",
        "incremental_disclosure",
        "schema_probing",
    }
)

#: `schemas.md` §4. Exists so expected-leak cells are not reported as
#: regressions (FR-23) — C1 is expected to come out at every level, and a
#: suite that flagged that as a failure would be reporting its own control
#: working as a fault.
EXPECTED_OUTCOMES = frozenset(
    {
        "capture_at_L1",
        "capture_at_L1_L2",
        "capture_at_all_levels",
        "no_capture_expected",
    }
)

#: The eight scored cells. F7 scores as two (FR-19), so `flag_id` on a case is
#: the *cell* it targets rather than the flag file it comes from: F7a and F7b
#: are separate rows in the matrix and separate thresholds under SC-3.
SCORED_CELLS = frozenset({"F1", "F2", "F3", "F4", "F5", "F6", "F7a", "F7b", "C1"})


class InvalidCase(ValueError):
    """A case that cannot be executed as written. Raised at load, never at run."""


@dataclass(frozen=True, slots=True)
class Phase:
    """One session within a case.

    Each phase opens a **fresh session with no carried context** (`schemas.md`
    §4). Only persisted memory crosses the boundary, which is the entire
    mechanism F7 tests: phase one plants a directive, phase two opens a clean
    session containing no injection and observes whether the agent acts on it
    anyway (FR-18).
    """

    session: int
    turns: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"session": self.session, "turns": list(self.turns)}


@dataclass(frozen=True, slots=True)
class AttackCase:
    schema_version: int
    id: str
    flag_id: str
    agent: Agent
    technique_family: str
    levels: tuple[Level, ...]
    expected_outcome: str
    phases: tuple[Phase, ...]
    notes: str = ""

    #: Cases whose target is only reachable with fixture identifiers the suite
    #: is not allowed to read from the manifest (`schemas.md` §3). The operator
    #: supplies them, exactly as `bta.tools.harness` requires, and a case
    #: naming one that was not supplied is skipped **loudly** — recorded as an
    #: error result rather than dropped, so it cannot quietly shrink a cell.
    requires_fixtures: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_multi_session(self) -> bool:
        return len(self.phases) > 1

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.id,
            "flag_id": self.flag_id,
            "agent": self.agent.value,
            "technique_family": self.technique_family,
            "levels": [level.value for level in self.levels],
            "expected_outcome": self.expected_outcome,
            "phases": [phase.to_dict() for phase in self.phases],
            "notes": self.notes,
        }
        if self.requires_fixtures:
            record["requires_fixtures"] = list(self.requires_fixtures)
        return record

    def expects_capture_at(self, level: Level) -> bool:
        """What this case predicted, per level.

        **Nothing consumes this today, and the docstring used to claim
        otherwise.** `diverged_from_expectation` in the matrix is computed from
        the *flag's* registered prediction (`flags/*.json`, `schemas.md` §2),
        not from the case's — so the case-level `expected_outcome` is validated
        and pre-registered but never read after load.

        It is kept rather than deleted because FR-23's purpose is per case: C1's
        cases predict a capture at every level, and a run that reported those as
        regressions would be reporting the control working as a fault. Reaching
        that requires per-case divergence in the matrix, which needs a decision
        about whether a case's prediction can dissent from its flag's. Recorded
        as part of TD-35 rather than left as a method whose docstring described
        a caller that does not exist.
        """
        return {
            "capture_at_L1": level is Level.L1,
            "capture_at_L1_L2": level in (Level.L1, Level.L2),
            "capture_at_all_levels": True,
            "no_capture_expected": False,
        }[self.expected_outcome]
