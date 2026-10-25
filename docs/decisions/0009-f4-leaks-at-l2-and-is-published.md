# 0009 — F4's L2 divergence is accepted and published, not engineered away

**Date:** 2026-08-25
**Phase:** 4 — **before any attack case has run**
**Status:** accepted
**Requirements:** FR-15, FR-16, FR-17, SC-2, SC-5, `flags/F4.json`, `build-plan.md` §4
**Related:** TD-16, ADR 0004

## Decision

The registered L2 screener pattern set does not match F4, and it will not be
changed to. `flags/F4.json` predicts `held` at L2; the observed matrix is
expected to read `defeated`, and that divergence is **published as a finding**
per `schemas.md` §2 rather than corrected.

The alternative — reopening FR-17's `BTA{...}` flag format — is declined.

## Why now

TD-16 says this must be settled **before any attack case runs**, and it is: no
case has executed, and the reference run is phase 6. Settled afterwards, either
choice is indistinguishable from tuning a pre-registered artefact to reach a
nicer result, which is the one thing `docs/process.md` says the friction exists
to prevent.

## Why accept rather than reopen the format

**The cause is understood and was established factorially.** Two independent
causes, either sufficient alone: gitleaks' `generic-api-key` allowlist contains
`bit_` and the retailer is Orbit Commerce, *and* — independently — that rule's
capture group is `[\w.=-]{10,150}`, which excludes `{` and `}`. An earlier
reading blamed the first alone, having tested the unwrapped form only with the
colliding prefix; a prefix change drafted on that reading was reverted when the
confound was found.

**The second cause is a finding about the lab's design, not a defect in it.**
Wrapping a secret in a flag marker is what makes it invisible to a real-world
secret scanner. That generalises past F4 to *any* credential-shaped flag under
*any* rule whose capture class excludes braces, which is most of them. It is
arguably the most interesting result the L2 column can produce, and engineering
it away would delete it.

**The fixes available are all forbidden or worse.** Teaching the screener to
strip the wrapper before matching is flag-format knowledge in the screener:
FR-16 forbids it, it would block all eight flags at L2, and C1 would stop being
a control (SC-5). Removing `bit_` from our vendored copy is the same error in
smaller form and breaks ADR 0004's provenance claim. Reopening FR-17 is a PRD
change touching the verifier's normalisation set and every other flag's value
shape, to make one cell match its prediction.

## What is published

- `flags/F4.json` keeps `"L2": "held"`. **The prediction is not edited.**
  `schemas.md` §2 is explicit that divergence between `expected` and the
  observed matrix is a finding, reported and not silently corrected.
- The F4/L2 cell carries `diverged_from_expectation: true` and appears in the
  write-up (`build-plan.md` §3 Phase 6).
- The write-up states that the L2 column has **no `held` produced by the output
  screener**, and why: the format that makes a flag scoreable is the format
  that makes it unscannable.

## Costs

The L2 column loses the one cell that would have demonstrated output screening
working against a credential-shaped secret. What replaces it is a sharper
claim about the limits of pattern-class screening, and an honest one, made
before the run rather than after seeing it.

A reader may take "the screener never caught anything" as evidence the L2
mitigation stack is inert. It is not — the injection classifier decides F3's L2
cell and the confirmation step decides F6's — so the write-up must attribute
per control rather than per level.

## Revisit if

Never for this run. If FR-17's format is ever reopened for an unrelated reason,
this decision is re-examined *then*, and the original result is published
alongside any revised one (`docs/process.md`).
