# 0008 — VER-6's redaction stage covers audit records, not only transcripts

**Date:** 2026-08-25
**Phase:** 4
**Status:** accepted — **requires an amendment to VER-6's text in `prd.md`**
**Requirements:** VER-6, VER-5, `schemas.md` §3, §6, SEC-7
**Related:** TD-14, TD-19, ADR 0002

## Decision

VER-6's redaction stage runs over **audit records as well as transcripts**.
Every run emits a redacted copy of both. `verifier/redaction.py` is the single
implementation; `redact()` takes a serialised document and does not care which
of the two it was given.

TD-14 posed this as an either/or: grow VER-6, or state the sensitivity
classification in `prd.md` rather than only in a module docstring. This takes
the first.

## Why

**The exposure is ordinary, not exotic.** `schemas.md` §6 records
`parameters` verbatim because the analysis they exist for needs the original.
At L1 the Onboarding agent holds F4 in its own system prompt (FR-14), so
`check_policy(topic="<F4 value>")` is what a steered model does on a good day.
Any value the model read from a tool result and echoed into a later call lands
the same way. TD-19's `result_payload` now puts tool output in the transcript
too, which widens the same surface rather than creating a new one.

**Only the verifier can do it.** `schemas.md` §3 permits one component to hold
live flag values. Recognising a flag value at the emitter is therefore not
possible even in principle — the same argument VER-6 already makes for putting
the redactor inside the verifier.

**One redaction path is a smaller surface than two handling rules.** The
alternative leaves a class of artefacts that may never be published, protected
by a convention. Conventions are what §"Structural separation over discipline"
in `build-plan.md` §1 says not to rely on. A single stage that runs on every
run is continuously exercised, and publication has no decision left to make —
which is VER-6's own argument for why redaction is a stage rather than a step.

**The cost of being wrong is asymmetric.** A redacted audit record that did not
need redacting costs a marker in a field nobody reads. An unredacted one
published costs a reseed of the estate and a value in `git` history for good.

## What this obliges

**`prd.md` VER-6 needs amending by PR**, since it currently says "Each run
emits two transcripts". That is a requirement change and goes to the PRD in the
diff, not to this file — `docs/process.md` §"What gets written down". This ADR
records the decision; it does not enact it.

## How it fails

`redact()` re-scans its own output with the full VER-2 normalisation set and
raises `RedactionIncomplete` if any value is still reachable. It never returns
a partially redacted document. The exception names flag ids and never values,
because a failure message reaches a CI log and a CI log is a transcript.

The substitution covers the folding transforms by pattern (separators and
zero-width characters interleaved through a value) and the decoding transforms
by generating each encoded form and replacing it literally. Anything outside
both — a depth-two chain nobody anticipated — is caught by the residual check
and refuses the artefact rather than leaking it.

## Costs

Redaction now runs over every audit record in a run, which at SC-3 volumes is
the largest artefact class the pipeline produces. The residual re-scan doubles
the normalisation work on each. Measured at depth two the fan-out is bounded at
133 forms per string (`tests/test_verifier_normalisation.py`), so this is a
constant-factor cost on a batch stage, not on the interactive path.

## Revisit if

The audit corpus grows to where the residual re-scan dominates a run. The fix
then is to scope the re-scan to fields that were substituted, not to drop it.
