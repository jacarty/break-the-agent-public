"""Load and validate the pre-registered case set.

Every failure here is loud and happens **before** a run starts. A case that
cannot be parsed, names a family outside `schemas.md` §1.1, or targets a cell
that does not exist is a bug in a reviewed artefact, and the cost of letting it
through is not a crash — it is a cell that quietly runs eleven cases instead of
twelve and reports `insufficient` for a reason nobody can see in the output.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from bta.models import Agent, Level
from suite.cases.schema import (
    EXPECTED_OUTCOMES,
    SCHEMA_VERSION,
    SCORED_CELLS,
    TECHNIQUE_FAMILIES,
    AttackCase,
    InvalidCase,
    Phase,
)

CASES_DIR = Path(__file__).resolve().parent


def _require(record: dict, key: str, where: str) -> object:
    if key not in record:
        raise InvalidCase(f"{where}: missing required field {key!r}")
    return record[key]


def parse_case(record: dict, *, where: str) -> AttackCase:
    """One case object → one `AttackCase`, or `InvalidCase` naming the field."""
    version = _require(record, "schema_version", where)
    if version != SCHEMA_VERSION:
        raise InvalidCase(f"{where}: schema_version {version!r}, expected {SCHEMA_VERSION}")

    case_id = str(_require(record, "id", where))
    where = f"{where} [{case_id}]"

    flag_id = str(_require(record, "flag_id", where))
    if flag_id not in SCORED_CELLS:
        raise InvalidCase(
            f"{where}: flag_id {flag_id!r} is not a scored cell. "
            f"F7 scores as F7a and F7b (FR-19), never as F7."
        )

    family = str(_require(record, "technique_family", where))
    if family not in TECHNIQUE_FAMILIES:
        raise InvalidCase(
            f"{where}: technique_family {family!r} is not in schemas.md §1.1. "
            f"SC-3 counts families, so an unregistered one would inflate a "
            f"threshold with a spelling."
        )

    expected = str(_require(record, "expected_outcome", where))
    if expected not in EXPECTED_OUTCOMES:
        raise InvalidCase(f"{where}: expected_outcome {expected!r} is not in schemas.md §4")

    try:
        agent = Agent(str(_require(record, "agent", where)))
    except ValueError as exc:
        raise InvalidCase(f"{where}: {exc}") from None

    raw_levels = _require(record, "levels", where)
    if not isinstance(raw_levels, list) or not raw_levels:
        raise InvalidCase(f"{where}: levels must be a non-empty list")
    try:
        levels = tuple(Level(str(level)) for level in raw_levels)
    except ValueError as exc:
        raise InvalidCase(f"{where}: {exc}") from None

    raw_phases = _require(record, "phases", where)
    if not isinstance(raw_phases, list) or not raw_phases:
        raise InvalidCase(f"{where}: phases must be a non-empty list")

    phases = []
    for index, raw in enumerate(raw_phases):
        turns = raw.get("turns")
        if not isinstance(turns, list) or not turns or not all(isinstance(t, str) for t in turns):
            raise InvalidCase(f"{where}: phase {index} needs a non-empty list of string turns")
        raw_session = raw.get("session", index + 1)
        try:
            session = int(raw_session)
        except (TypeError, ValueError):
            raise InvalidCase(
                f"{where}: phase {index} has a non-integer session {raw_session!r}"
            ) from None
        phases.append(Phase(session=session, turns=tuple(turns)))

    # A single-phase case cannot test FR-18's two-phase mechanism, and F7 is
    # defined by it. Caught here because the failure is otherwise invisible:
    # the case runs, the fire phase never happens, and the cell reports a
    # `held` that was never attacked the way it claims to have been.
    if flag_id.startswith("F7") and len(phases) < 2:
        raise InvalidCase(
            f"{where}: F7 is a two-phase case (FR-18). A single phase plants a "
            f"directive and never opens the fresh session that tests it."
        )

    raw_fixtures = record.get("requires_fixtures", ())
    # A string is iterable, so `tuple("foo")` silently becomes `("f", "o",
    # "o")` — three single-character fixture names rather than one, and every
    # downstream check (`test_declared_fixtures_are_used`,
    # `execute.RunConfig.fixtures`) would look for placeholders that never
    # appear and pass or fail for reasons unrelated to what the author wrote.
    # A case file is a reviewed artefact, but the review is of the attack, not
    # of Python's iteration protocol — this is caught here instead.
    if not isinstance(raw_fixtures, (list, tuple)) or not all(
        isinstance(f, str) for f in raw_fixtures
    ):
        raise InvalidCase(
            f"{where}: requires_fixtures must be a list of strings, got {raw_fixtures!r}"
        )

    return AttackCase(
        schema_version=SCHEMA_VERSION,
        id=case_id,
        flag_id=flag_id,
        agent=agent,
        technique_family=family,
        levels=levels,
        expected_outcome=expected,
        phases=tuple(phases),
        notes=str(record.get("notes", "")),
        requires_fixtures=tuple(raw_fixtures),
    )


def load_all(directory: Path | None = None) -> tuple[AttackCase, ...]:
    """Every case in the registered set, sorted by id.

    Sorted so a run's ordering is a property of the set rather than of a
    filesystem, which matters for resumability: a resumed run has to agree with
    the original about which executions are already done.
    """
    directory = directory or CASES_DIR
    cases: list[AttackCase] = []
    for path in sorted(directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InvalidCase(f"{path.name}: {exc}") from None
        if not isinstance(document, list):
            raise InvalidCase(f"{path.name}: expected a list of case objects")
        cases.extend(parse_case(record, where=path.name) for record in document)

    duplicates = [id_ for id_, count in Counter(c.id for c in cases).items() if count > 1]
    if duplicates:
        raise InvalidCase(
            f"duplicate case ids: {', '.join(sorted(duplicates))}. "
            f"`(case_id, level, model, repeat_index)` is the unique key of a "
            f"run result (schemas.md §5), so a duplicate id silently merges two "
            f"cases into one row of evidence."
        )
    return tuple(sorted(cases, key=lambda case: case.id))


def coverage(cases: tuple[AttackCase, ...]) -> dict[tuple[str, str], dict[str, object]]:
    """Cases and families per `(cell, level)`, for checking against SC-3.

    `build-plan.md` §6 names underestimated case authoring as the route to
    `insufficient` verdicts by accident, and says the count is tracked "from
    the start of Phase 5, not at the end". This is that count. It is computed
    from the cases rather than from a run, so it answers "could this cell reach
    `held`?" before any inference is paid for.
    """
    tally: dict[tuple[str, str], dict[str, object]] = {}
    for case in cases:
        for level in case.levels:
            key = (case.flag_id, level.value)
            entry = tally.setdefault(key, {"cases": 0, "families": set()})
            entry["cases"] = int(entry["cases"]) + 1
            families = entry["families"]
            assert isinstance(families, set)
            families.add(case.technique_family)
    return tally


__all__ = ["AttackCase", "InvalidCase", "coverage", "load_all", "parse_case"]
