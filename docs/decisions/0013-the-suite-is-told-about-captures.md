# 0013 — The suite is told about captures and never detects them

**Date:** 2026-08-28
**Phase:** 5
**Status:** accepted
**Requirements:** FR-6, FR-20, VER-1a, VER-3, VER-6, SC-6, `schemas.md` §3, §5
**Related:** TD-19, TD-31

## Decision

The attack runner learns that a flag was captured from the deployment, over the
same HTTP surface a person uses. The site's SSE `done` frame gains a structured
`captured` field alongside the rendered panel it already carried — flag id,
channel, and the normalisation that matched, and never a value.

`suite -> {bta.generator, verifier}` becomes a forbidden import contract.

## Why not let the runner detect captures itself

It is the obvious design and it is wrong twice.

**It would put live flag values in the suite's process.** `schemas.md` §3 keeps
the set of components that may hold one to two: the generator that plants them
and the verifier that detects them. A runner importing `verifier.manifest` is a
third, and it is the one that writes the artefact SC-6 commits to the
repository. VER-6 keeps raw transcripts out of git precisely because they carry
values; run results are the copy that goes in, and a value reaching one obliges
a reseed of the estate.

**It would measure a different thing.** VER-1a's two channels are a property of
what the *deployment* disclosed — `model_output` is what the model emitted,
`tool_result` is what the tool handed to the interface without the model
emitting it. That distinction lives in `bta.agent.transcript`, where
`assistant_text()` and `tool_result_text()` are kept apart (TD-19). A runner
re-scanning a serialised transcript would have to reconstruct the split, and a
reconstruction that drifted would silently move captures between the
verdict-bearing channel and the one that never affects a verdict.

## Why the SSE frame rather than a suite-only endpoint

A second endpoint would be a second surface on a deliberately vulnerable,
internet-reachable service, and it would let the suite exercise a path no
attacker uses — which contradicts PRD §1's claim that the site exists so a
person can reproduce by hand what the suite reproduces automatically.

The frame already carried the same facts as rendered HTML. This adds no
disclosure: the panel shows the attacker the identical ids and channels. What it
adds is that the suite reads data instead of scraping a template, so a run
result is not hostage to a styling change.

## What this leaves open

`tool_call_ids` on a run result is empty, because the frame carries no audit
identifiers and the audit sink is in process anyway (TD-25). The matrix does not
read the field. SC-4's classification of an L3 defeat does, and cannot be done
without it — recorded as TD-31 rather than closed by adding an id that would
resolve to nothing.

## Consequences

- One field added to `bta.site.app`'s `done` frame. No new route, no new data.
- Two import contracts, and `suite` joins `root_packages`.
- `tests/test_suite_end_to_end.py` drives the real Starlette app in process over
  `httpx.ASGITransport` and asserts both channels arrive separately and that no
  minted value appears anywhere in a serialised run result. The protocol is the
  thing most likely to break silently — both sides look fine independently, and
  a drift presents as a column of `not_captured` rather than as an error.
