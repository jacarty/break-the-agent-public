# 0006 — Phase 3's exit criterion is met with real providers over fake estate clients

**Date:** 2026-08-24
**Phase:** 3
**Status:** accepted
**Requirements:** FR-16, SC-5, FR-22, `build-plan.md` §"Phase 3"
**Related:** TD-11

## Decision

Phase 3's exit criterion — C1 leaks at all three levels — is demonstrated by a
harness that runs the agent loop against **all three real providers** at all
three levels, with a marker supplied per ADR 0003 and the fakes from
`tests/fakes.py` standing in for the estate. Phase 3 closes on that evidence.
It does not wait on the TD-11 apply.

Whatever the harness cannot claim is written down where the result is written
down, in the same terms `src/bta/tools/README.md` already uses for phase 2.

## Why

**C1 does not touch a cloud resource.** It is text in a tool description
(`schemas.md` §3, `planted_via: tool_description`). The claim the exit
criterion tests is that the L2 screener has no knowledge of flag syntax
(FR-16), and every mechanism in that claim — description rendering, the
provider's tool schema, the model's output, the screener — sits between the
marker and the transcript. None of it is downstream of a BigQuery row access
policy. Substituting a fake client costs this particular claim nothing, and
saying so precisely is different from saying fakes are good enough in general.

**The providers are not fakeable here and are not faked.** Whether a model
repeats a marker out of a tool description is the behaviour under test. A fake
provider would make the exit criterion a unit test about a regex, which is the
option `build-plan.md` was avoiding when it made the criterion behavioural.

**Blocking on TD-11 would order the build wrongly.** Closing phase 2's live
half needs an Identity Platform ID token for a persona, which the persona
picker mints, and the persona picker is phase 4. Making phase 3 wait on it puts
two phases behind a dependency that the phase after them resolves.

## What this evidence does not establish

Stated here because phase 2's equivalent list is the reason its status is
legible:

- Nothing about whether the estate is configured correctly. The fakes enforce
  the same rules the Terraform does; they are not that Terraform. That evidence
  is the harness's to produce under TD-11 and remains outstanding.
- Nothing about L3's credential path end to end. The credential policy and the
  client dispatch are asserted offline; whether a federated principal actually
  reaches BigQuery is TD-11's question, unchanged by this decision.
- Nothing about C1's *delivery* in a deployment. The harness supplies the
  marker from the environment, which is the channel ADR 0003 chose, but a
  Cloud Run revision mounting it is phase 4.

**Phase 3 therefore closes as built, with a named live gap**, in the same shape
phase 2 did. That is a deliberate repetition: the alternative is a phase that
reads as closed while the same gap is open and unlabelled.

## Costs

Two phases now carry an open live half, and they close on the same apply. The
risk is that "built, not closed" becomes the project's normal state and stops
being read as a caveat. Mitigation is that TD-11 names one apply as the thing
that closes both, and phase 4's first act is the persona picker that unblocks
it.

## Revisit if

The apply happens before phase 3 finishes, in which case run the harness
against the estate and take the stronger evidence. This decision is about not
blocking, not about preferring fakes.
