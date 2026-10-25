# 0015 — F7a is reported as unmeasured rather than detected

**Date:** 2026-08-30
**Phase:** 6
**Status:** accepted
**Requirements:** FR-19, VER-1, VER-1a, SC-4, `schemas.md` §2, §8
**Related:** TD-32, TD-25 (closed), TD-17 (closed)

## Decision

F7a is **not** given a behavioural detector. The matrix instead states, on the
row itself, that F7a's verdict is not an independent measurement: affected cells
carry `measurement_limitation: "no_independent_detector"` and a null
`diverged_from_expectation`.

The control arm that would measure F7a is not built, now or later, unless it
lands before the reference run under the pre-registration rules in
`process.md`. After the reference run it is not an option at all.

## The problem

FR-19 splits F7 into two cells because they ask different questions:

- **F7a** — did the planted directive change behaviour?
- **F7b** — did the changed behaviour return data the caller was not entitled
  to?

`flags/F7.json` predicts they diverge at exactly one point: `F7a: defeated` at
all three levels, `F7b: defeated, defeated, held`. That single divergence *is*
the L3 containment claim — prevention fails, containment holds — and it is the
most load-bearing prediction the flag set makes.

Capture detection is value-appearance (VER-1). The generator plants one value
attributed to `F7` (`_place_f7`), and only F7b's outcome is a value; F7a's
outcome is a *change in behaviour*, which leaves no value anywhere. So both
cells are scored from the same evidence and move together by construction.
`suite/matrix/build.py::_is_this_cell` accepts an `F7` capture for either cell
deliberately — filtering strictly on the cell id would drive both permanently to
zero, a silent nil that reads as a hold, which is worse than scoring them the
same and saying so.

The consequence is not merely that a cell is uninformative. If L3 behaves as
designed, F7b holds, F7a scores identically, and F7a/L3 is stamped
`diverged_from_expectation: true` against its registered `defeated`. Read
literally that publishes *the injection did not change behaviour at L3* — the
reassuring answer to a question nothing asked, on the cell the whole L3 column
rests on. **The missing detector does not produce a gap in the matrix; it
produces a confident wrong entry.** That is what this decision exists to stop.

## Why not build the detector

The shape is known and was named in TD-32: compare the fire phase's audit
records against an unpoisoned control session, and treat *a tool was called that
the turn did not ask for* as the F7a signal. Two of its three obstacles have
since gone. TD-17 closed, so F7 is executable at all. TD-25 closed on this
branch, so audit records are durable and per-turn — the detector's input exists
now, which it did not when TD-32 was written. This is therefore a choice, not an
incapacity, and it is recorded as one.

**It changes the experiment, not the reporting.** A control arm is 24 additional
F7 executions per provider per repeat, and more importantly a second condition
per case. FR-19's cells were registered against the suite as designed; adding an
arm to make one of them measurable is a design change, legitimate only before
the reference run and only through the `process.md` PR path. It is not a bug fix
and must not arrive dressed as one.

**"The turn did not ask for it" is a judgement, and an unregistered one.** The
rule deciding which tool calls count is the whole detector. Written after any
result exists, it is a rule tuned against a known answer — the exact failure
mode pre-registration exists to prevent, applied to the one cell where a
flattering answer is most valuable to us. A weak detector here is worse than
none, because it converts *unmeasured* into *measured*, and the matrix gives the
reader no way to tell the difference.

**A control arm needs its own validity argument.** The comparison assumes the
unpoisoned session is otherwise identical, and agent sessions are not
deterministic. Distinguishing "the directive changed behaviour" from "the model
made a different choice this sample" needs repeats sized for a difference in
distributions, not the three repeats SC-3 registers for a capture/no-capture
outcome. Sizing that after the fact would be a second unregistered decision on
top of the first.

## Why the limitation goes in the matrix and not only in the docs

The matrix is the deliverable (`prd.md` — the result matrix and the Terraform
that produces it, not the site). A limitation recorded only in `tech-debt.md` is
invisible to everyone who reads the artefact, which is everyone the artefact is
for. Publishing a row that reads as a measurement, with the caveat somewhere
else, is the paper form of the thing this project criticises.

`insufficient` was considered as the carrier and rejected. It is a claim about
**evidence volume** — too few cases, too few families, a provider missing — and
SC-2 makes it a first-class verdict precisely so that thin evidence has an
honest home. F7a's evidence is not thin; it is ample and it is about a different
question. Overloading `insufficient` would make the verdict mean two unrelated
things and would hide a real hold behind a word that means "we do not know".

`diverged_from_expectation: null` rather than `false` follows from the same
argument. `false` asserts the registered prediction was borne out. On F7a/L3
that assertion is exactly the unearned reassurance described above, arriving
through the field a reader would most reasonably trust.

## What this costs

FR-19's prediction goes untested. The lab publishes a two-cell F7 in which one
cell is measured and one is labelled unmeasured, and the containment-not-
prevention claim at L3 rests on F7b alone — that entitlement was not exceeded —
without the paired evidence that behaviour did change. That is a weaker result
than FR-19 was written to produce, and the write-up says so in those words
rather than presenting F7 as two independent findings.

TD-32 stays open against this, downgraded from a defect to a known limitation
with a named route out: make F7a measurable by registering a control arm before
a future reference run, or retire F7a as a scored cell in a future amendment to
`flags/F7.json`. Neither is done here.
