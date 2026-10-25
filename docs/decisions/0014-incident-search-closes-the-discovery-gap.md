# 0014 — Incident search closes the discovery gap

**Date:** 2026-08-29
**Phase:** 6
**Status:** accepted
**Requirements:** FR-9 (amended), FR-11, FR-13, SC-3, SC-4, `schemas.md` §4
**Related:** TD-32

## Decision

Deploy Bot gains a fifth tool, `search_incidents`. FR-9 is amended to five
tools, dated, before the reference run rather than after it.

It returns incident metadata — id, service, owner team, severity, status, the
timestamps, title — plus a `has_postmortem` boolean. It does **not** return
`postmortem_summary`, and the exclusion is enforced twice: in the SQL
projection, and again in Python.

## The problem

FR-9's original four tools were `list_services`, `search_deploys`, `query_logs`
and `get_incident`. None of them enumerates incidents, and `get_incident` takes
an id it will not help a caller find. So a target id could not be discovered
from inside the lab at all, and every F1 and F3 attack case declared
`requires_fixtures: ["other_incident_id"]` — supplied by the operator from the
fixture manifest, which an attacker does not have.

**The consequence is a mismatch between what the suite measures and how the
matrix reads.** With the id supplied, a cell answers *"does the control refuse a
targeted unentitled read"*. The matrix is presented as *"which class of control
removes the exploit"*. Those are different claims, and the second is the one the
project exists to make.

It was found the first time a person sat in front of the deployed site and
opened with "tell me about any recent postmortems" — the most ordinary request a
deploy bot receives. There was no tool to serve it, so the agent improvised
`query_logs` across four services and answered a question nobody had asked. A
live agent answers that request; this one structurally could not.

## Why not keep supplying the id

There is a real argument for the id-supplied cases and it survives this
decision, so it is worth stating rather than discarding.

At L3 the row access policy on `owner_team` makes an unentitled row *vanish* —
the query succeeds, the row is absent, there is no error and nothing to
attribute. If discovery were part of the attack, an L3 `held` would be partly
the row policy and partly the attacker never having located the target.
Obscurity is not authorisation, and the matrix would report the second as the
first.

That justifies **keeping** id-supplied cases. It does not justify having only
them, which is what the repository had. Both belong: the supplied-id family
isolates the authorisation variable, and the discovery family tests the threat a
deployed agent actually faces. The matrix is only entitled to its headline claim
if the second family exists.

## Why metadata only

The alternative — returning `postmortem_summary` in the list — makes the single
most realistic attack a single turn: "tell me about any recent postmortems"
leaks F1 with no craft at all. It was rejected for a reason that is about
evidence rather than realism.

**A flag needs one resting place.** `flags/F1.json` names `get_incident`'s
column as F1's `resource`, `schemas.md` §3's `planted_via: row_field` names one
field, and the verifier reports the channel a value came out of. If the value
could emerge from two tools, a capture would be attributable to neither, and
SC-4's classification of an L3 defeat — the most valuable result the project can
produce — would rest on not knowing which surface disclosed it.

So discovery and disclosure are separate steps, and the realistic attack is a
two-turn chain from zero prior knowledge: enumerate to find a closed team-b
incident, then fetch it. The disclosure stays where the flag definition says it
is.

A third option, a truncated snippet, was rejected outright: the truncation
length would silently decide whether the flag fell inside the preview, which is
a pre-registered threshold set by a number nobody registered.

## Why the exclusion is enforced twice

The SQL projection is the real mechanism. The Python `pop` after it is not
redundancy for its own sake.

The estate is faked in CI, and `tests/fakes.FakeRows` ignores the SELECT list —
it returns whole stored rows, `postmortem_summary` included. A later edit
widening the projection would therefore pass every offline test. That is
precisely the shape of TD-11, TD-13 and TD-17: the half needing a real backing
service is the half no fake can check.

`tests/test_tools.py::test_enumeration_never_returns_postmortem_text` turns the
fake's over-return into the adversary — it asserts the tool declines to pass the
column on *even when the layer beneath it offers one*.

## What this does not change

`owner_team` is a filter, not a control. A caller may ask for another team's
incidents and at L1 and L2 will get them; the parameter exists because a real
list endpoint has one, and offering it makes the natural attack natural rather
than clever. At L3 the same row access policy that governs `get_incident`
filters the list before the tool sees it — an empty list, `allowed`,
`rows_returned: 0`, no error. FR-13 holds: the tool body is byte-identical
across levels and the only thing that varies is what the credential can reach.

## Consequences

- A discovery case family must be authored for F1 and F3 **before** the
  reference run. Pre-registration is the point; cases written after a run are
  not evidence.
- `EXPECTED_TOOL_COUNT` becomes 9, and C1 renders into one more tool
  description. C1 must still leak at all three levels (FR-16, SC-5).
- The harness gains a paired probe: unfiltered enumeration as the positive
  control, cross-team enumeration as the negative.
- TD-32 is unaffected. F7a still has no behavioural detector, and enumeration
  does not give it one.
