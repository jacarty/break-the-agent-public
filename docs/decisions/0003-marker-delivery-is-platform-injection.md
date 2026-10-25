# 0003 — C1 and F6 markers arrive as platform-injected environment variables

**Date:** 2026-08-24
**Phase:** 3
**Status:** accepted
**Requirements:** `schemas.md` §3, SC-5, FR-16, SEC-9, SEC-11
**Closes:** TD-13

## Decision

The estate generator writes C1's and F6's values to Secret Manager as two
secrets that belong to the **hosting** layer, not the estate. Cloud Run mounts
them into the runtime as environment variables. `bta.config.DeploymentConfig`
gains two required fields, so `from_env` raises `MissingConfiguration` when
either is absent and the process does not start.

`ToolSpec.render_description(marker)` and `EstateClients(write_path_marker=...)`
keep the signatures phase 2 gave them. This decision supplies their argument;
it does not change either end.

## Why

**TD-13's failure mode is silence, and the fix has to be a refusal.** A
deployment that renders tool descriptions with no marker has removed the
control flag. C1 leaking at L2 is the behavioural proof that the screener has
no knowledge of flag syntax (FR-16), and a `held` C1 cell means the fixture or
the verifier is broken (SC-5) — so a run with no marker planted would report a
defect that is not there, against the one flag whose whole job is to be a
control. `from_env` already refuses to construct a config with a missing
input, names every missing variable at once, and has no defaults by design
(POR-6). The refusal TD-13 asks for is the behaviour that file already has.

**Platform injection is the pattern SEC-9 already mandates**, for the provider
API keys, into the same process, in the same phase. Choosing it here sets one
precedent rather than two, and the alternative shapes each had a cost the
project would have had to carry twice.

**No tool goes looking for a value.** `schemas.md` §3 forbids a tool reaching
the manifest. Nothing in `bta.tools` reads configuration; the markers are
constructor arguments supplied by whatever stands the process up, which is the
seam phase 2 left open precisely so this decision could be made without
touching a tool.

## Costs

**The runtime service account gains `secretAccessor` on two secrets, and
SEC-11's wording has to be read narrowly.** SEC-11 says the runtime identity
holds no permission on any estate resource — "not Secret Manager" among them.
The marker secrets are hosting-layer resources created alongside the Cloud Run
service, and the grant is scoped to those two secret IDs. That reading is
correct and it is also load-bearing, so it is stated here rather than left to
be inferred: **the SEC-11 claim is about the estate, and the estate is what
`infra/estate/` creates.** The provider keys under SEC-9 need exactly the same
grant in the same phase, so this is the boundary being drawn once, in the open,
for both.

What SEC-11 protects is unaffected: a metadata-server token obtained inside the
runtime still reaches no BigQuery table, no bucket, no Firestore document and
no estate secret. It reaches two values that are already rendered into every
tool schema the model sees.

**POR-4 is not breached, but the direction matters.** Hosting reads two values
the generator produced. `infra/estate/` takes nothing from hosting and exports
only resource identifiers, which is the coupling POR-4 forbids; the dependency
here runs hosting → estate, which is the permitted direction and the same one
every other hosting input already runs in.

## Rejected

**Plain environment variables in the Cloud Run revision spec.** No IAM grant at
all, so SEC-11 stays literally untouched — but both values become readable by
anyone with `run.viewer`. It costs the matrix nothing (C1 is expected to leak
at every level, and F6 is captured through the agent rather than through a
console), and it was still declined: a project whose central discipline is that
flag values live in one place does not put two of them in a field that is not
that place, to save a scoped grant it has to make for the provider keys anyway.

**A startup read under the shared estate credential.** Uses only the residual
`infra/estate/identity.tf` already documents, and adds no grant. Declined
because it puts a manifest-derived read on the startup path of the process that
also serves L3 sessions, which makes the shared credential's blast radius a
question a reader has to re-open every time they read the L3 column.

## Revisit if

The hosting layer lands in phase 4 and Cloud Run's secret mounting turns out to
authenticate as something other than the revision's service account, which
would change the grant this decision costs. Verify against the deployment
before the grant is written, not against this document.
