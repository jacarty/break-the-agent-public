# 0002 — Schema records are frozen dataclasses, not pydantic models

**Date:** 2026-08-21
**Phase:** 2
**Status:** accepted
**Requirements:** VER-5, SEC-9, `schemas.md` §6
**Amends:** `build-plan.md` §7, decisions table, "Schema models — transcribed to pydantic in Phase 2"

## Decision

`AuditRecord` (`schemas.md` §6) is a frozen dataclass with an explicit
`to_dict()`. No pydantic dependency is added. The remaining record schemas —
run result (§5), memory record (§7), result matrix (§8) — are not transcribed
at all yet; each arrives with the phase that has a consumer for it.

## Why

**An explicit serialiser is a stronger VER-5 control than a model.** The rule
is that no credential material, secret value or flag value reaches an audit
record. `to_dict()` names fourteen fields, so a field added to the class does
not reach a record until somebody adds it to that list — and adding it to that
list is a reviewable line in a diff. A model that serialises whatever it holds
inverts that: the safe case becomes the one somebody has to remember.

**The validation pydantic would add is not the validation this record needs.**
The fields are enums from `bta.models` and strings. Type-checking them at
construction catches a class of bug that the enums already prevent.

**`build-plan.md` §7 argued the other half of this itself.** Its rationale for
phase 2 transcription warns that models with no consumer drift from the
documents they mirror, with nothing to catch it. That argument applies to three
of the four schemas today. This decision takes the warning and drops the
conclusion it was attached to.

**The repository already has an idiom.** `bta.generator` is frozen dataclasses
with hand-written `to_dict()` throughout, including for the fixture manifest —
the one artefact more sensitive than an audit record. A second serialisation
style for the second sensitive artefact would be a coin flip for every reader.

## Costs

Hand-written serialisers drift from the schema they mirror. The mitigation is
in `tests/test_tool_boundary.py`: `SCHEMA_FIELDS` is written out from
`schemas.md` §6 and compared against `to_dict()`'s keys, rather than derived
from the dataclass — a test that compared the class to itself would pass
through any drift.

## Revisit if

The run result (§5) and result matrix (§8) arrive in phases 5 and 6 and are
larger, more nested, and consumed by more code. If either wants real parsing —
reading a run result back from JSON, validating a matrix built by aggregation —
that is the moment to weigh a dependency, on those schemas, with a consumer in
hand. This decision is about the audit record and about not transcribing ahead
of need; it is not an argument against pydantic.
