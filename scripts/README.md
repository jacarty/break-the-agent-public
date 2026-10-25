# scripts

Operational checks that run against a deployment. Nothing here is imported by
`src/bta/` or `verifier/`, and nothing here reads a flag value.

| Script | Requirement |
| -- | -- |
| `lab.sh` | NFR-5, NFR-6 — deploy and tear the lab down from a laptop, in one command, with **one** input resolver so the two cannot disagree |
| `verify-teardown.sh` | NFR-6 — `terraform destroy` removes every provisioned resource, verified rather than assumed |
| `check-ci-authority.sh` | NFR-5, SEC-6 — an apply CI cannot perform is a failed deploy, and local applies cannot reveal it |
| `mint-persona-token.py` | TD-11, FR-3 — the tool harness needs an Identity Platform ID token, and only the running site could mint one |
| `vendor-screener-patterns.py` | FR-16, ADR 0004 — the L2 pattern set is vendored from public rulesets at pinned revisions |

---

## lab.sh

The whole lab from a laptop: `plan`, `up`, `down`, `verify`, `env`.

### Why it exists

Deploy and teardown used to resolve their inputs by different paths. `ci.yml`
read estate outputs and real secrets; `teardown.yml` passed literal
placeholders, because `terraform destroy` evaluates every variable and
validation even though a destroy works from state and needs none of them.

That created a second, unenforced contract — *every variable and validation
must have a placeholder that satisfies it* — which could only fail during a
teardown. Of hosting's six placeheld variables, two were shaped to pass
validation, two were not, and one fed a provider-side format check that no
placeholder can satisfy. The teardown broke on all three at once, and it had
been broken for as long as those validations had existed.

**So there are no placeholders.** `resolve_hosting_vars` is one function and
`up`, `down` and `plan` all call it. Teardown cannot drift from deploy, because
they read the same values from the same places.

### Rehearse before you run

```sh
./scripts/lab.sh plan
```

Resolves every input and plans every module, changing nothing. This is the
command that would have caught the teardown failure before it happened; run it
before `up` and before `down`.

```sh
./scripts/lab.sh up      # estate -> seed -> image -> hosting
./scripts/lab.sh down    # hosting -> estate -> billing guard -> NFR-6 check
./scripts/lab.sh env     # what resolved, with secrets and flag values withheld
```

`env` is the one command that reports rather than refuses. `up`, `down` and
`plan` stop on the first terraform output they cannot resolve; `env` resolves
everything it can, lists what is missing, and exits non-zero. Before the estate
has been applied it will name all eight estate outputs, which is expected — the
same list after an apply is not.

### Configuration

One gitignored file, `.env.lab`, at the repository root. No committed template
(POR-6c) — this block is the reference, and `lab.sh env` names anything
missing.

```sh
BTA_PROJECT_ID=your-project-id
BTA_REGION=your-region
BTA_STATE_BUCKET=your-project-id-tfstate
BTA_BIGQUERY_LOCATION=your-bq-location
BTA_ARTEFACT_BUCKET_NAME=your-project-id-artefacts
BTA_ARTEFACT_BUCKET_LOCATION=your-bucket-location
BTA_FIRESTORE_LOCATION=your-firestore-location
BTA_ESTATE_SEED=...                  # determinism is a claim about two runs
BTA_ACCESS_CODE=...                  # >= 16 chars (FR-1)
BTA_SUITE_BYPASS_TOKEN=...           # >= 32 chars (NFR-3a)
BTA_MODEL_ANTHROPIC=...
BTA_MODEL_GOOGLE=...
BTA_MODEL_XAI=...
BTA_VERTEX_LOCATION=...
BTA_ANTHROPIC_VERTEX_LOCATION=...
BTA_XAI_VERTEX_LOCATION=...
BTA_SITE_PROVIDER=google

# Optional. Set to include the billing guard in `down`.
BTA_BILLING_ACCOUNT_ID=...
BTA_GUARD_SOURCE_BUCKET=...
# Optional. Redeploy a known digest instead of building (SC-6).
BTA_IMAGE=...@sha256:...
```

### What it never prints

Flag values, the fixture manifest, the access code and the bypass token all
pass through it. None is echoed: `set -x` is never used, and `env` reports
those as `set (withheld)`. `schemas.md` §3 keeps the set of components that see
flag values small; this script is deliberately inside it rather than working
around it.

### Bootstrap is still by hand

`lab.sh` does not apply `infra/bootstrap/` — that module is applied from a
laptop by an operator, always, and `infra/bootstrap/README.md` §"Why this
module is not automated" is the argument. `lab.sh` reads its outputs and
copies `backend.tf.migrate` into place if it is missing, including the prefix
that `backend.tf.migrate` does not commit.

## mint-persona-token.py

Prints one Identity Platform ID token for a persona, on stdout, from an
operator's machine.

`src/bta/tools/harness.py` is phase 2's live exit criterion and needs
`BTA_HARNESS_SUBJECT_TOKEN` — an ID token carrying the persona's `team` claim —
before it can exercise L3, where that claim becomes `attribute.team` in the STS
exchange. Inside Cloud Run the site mints one per session; outside it there was
no way to get one, because `bta.site.personas._ambient_access_token` reads the
metadata server and a laptop has none. That gap is the whole of what TD-11 had
left.

This script is the missing hop and deliberately nothing more. It calls the same
two functions the site calls, substituting the operator's own bearer token for
the ambient one, and it inherits `resolve()`'s refusal of `bob` and
`svc-platform` rather than working around it — a token for a flag's owner tests
nothing (PRD §3).

```sh
export BTA_RUNTIME_SERVICE_ACCOUNT="$(terraform -chdir=infra/hosting \
    output -raw runtime_service_account_email)"
export BTA_IDENTITY_PLATFORM_API_KEY="$(terraform -chdir=infra/bootstrap \
    output -raw identity_platform_api_key)"

export BTA_HARNESS_SUBJECT_TOKEN="$(uv run python \
    scripts/mint-persona-token.py --persona alice)"
```

**No new grant is needed.** `roles/owner` already carries
`iam.serviceAccounts.signJwt`, so an operator signs as the runtime identity
without widening that account's own bindings. If this is ever needed by a
principal who is not an owner, grant *that person*
`roles/iam.serviceAccountTokenCreator` on the runtime account — do not add a
binding to the service.

The token is a real credential for one persona for one hour, worth what that
persona is worth. Do not commit it or keep it past the run. The API key is the
opposite case and is not redacted anywhere: it is served to every visitor, and
SEC-10 is what makes that safe.

## check-ci-authority.sh

Local applies run as an operator holding `roles/owner`. CI runs as `ci-deploy@`,
which holds a deliberately narrow role set. Everything works locally; the
disagreement only appears on dispatch.

That is not hypothetical. CI run 32292027232 failed on `iam.roles.get` applying a
`google_project_iam_custom_role` that had been validated locally during the SEC-3
rehearsal, under owner, where it applied fine. The rehearsal established which
*permission the workload* needed and could say nothing about which *grant the
deployer* could make.

```sh
scripts/check-ci-authority.sh iam.roles.create resourcemanager.projects.setIamPolicy
```

Non-zero if any permission is not covered. Run it before dispatching an apply
that adds a resource type the repository has not deployed before.

It reads the **live project bindings**, not `ci_deploy_roles` in
`infra/bootstrap/variables.tf`. Parsing the variable was the first attempt and
failed dangerously: that block also contains a `validation` naming the roles
that are *refused*, `roles/owner` among them, and a naive parse swept those into
the granted set. `roles/owner` expands to roughly thirteen thousand permissions,
so the check reported everything as granted — a check that cannot fail, which is
worse than none because it gets trusted. Caught by testing it against a
permission already known to be missing, which is the only reason it was caught.

---

## verify-teardown.sh

`terraform destroy` printing "Destroy complete!" is a statement about state, not
about the project. The resources that turn up on a bill after a lab is torn down
are the ones Terraform never tracked — a table a log sink created inside a
dataset, a ruleset left behind when its release was deleted, something a spike
made by hand. So this script asks the cloud what exists and never asks Terraform
anything.

It also does not assert that the project is empty, because it is not meant to
be: bootstrap deliberately survives an estate destroy (`prevent_destroy` on the
state bucket, `disable_on_destroy = false` on the APIs — see TD-1). Teardown is
verified as a **return to a baseline** instead.

### It is run by CI, not by hand

`.github/workflows/teardown.yml` owns the sequence. Terraform does the
destroying; this script is the step after it, and it writes a log that is
uploaded whether the check passed or failed.

```
mode: capture-baseline      once, after bootstrap, before the estate exists
mode: destroy-and-verify    inventory → terraform destroy → check → log
```

There is no equivalent runbook here on purpose. A documented manual sequence
sitting beside an automated one is a second path that nobody runs and nobody
updates, and the step most likely to be skipped is the baseline — which has to
be captured before the estate is applied and cannot be reconstructed afterwards.
As a workflow input it is a job someone dispatches once and reviews as a diff.

Running the script directly is for developing the script. Both subcommands take
`--project-id` and nothing is defaulted (POR-6).

### What it reports

**orphans** — in scope, still live, no exemption. Any is a failure.

**appeared** — live and in neither inventory. Usually a capture taken too early;
occasionally something a destroyed resource created on its way out. Also a
failure, because an unexplained resource is not an absent one.

**residue** — survives by design, and the reason is printed rather than filtered
out, because an exemption nobody reads is how a carve-out becomes a leak. Two
rules produce it: enabled APIs, and soft-deleted identities, which hold their ID
for 30 days (spike 0.1 finding 6) and so are gone for billing but not for the
next apply.

**stale** — an Asset Inventory record the API that owns the resource
contradicts. Reported, never silently dropped, for the same reason residue is.
Not a failure: the resource is not there. See below for why this category has
to exist.

**unread** — a surface whose API is not enabled, so the question could not be
asked. Expected on a baseline and harmless there: an under-reported baseline
widens the in-scope set and produces false orphans, which costs a re-run. It
fails a *check*, because `disable_on_destroy = false` means every estate API is
still enabled after a destroy, so an API that is off during a check is itself
the surprise. Not being able to see is not the same as there being nothing
there — the same reason `insufficient` is a legitimate verdict elsewhere in this
project.

**errors** — an enumerator that could not answer for any other reason. Always
fatal, for the same reason.

### Evidence

Two things come out of a run: `result-<run>.json` for machines and
`teardown-<run>.log` for people. Both are uploaded as a build artefact on every
run, pass or fail. The check reads `baseline.json` on the next run. Keep it
local for a local deployment; Actions requires a committed baseline in a
private deployment repository. This public export ignores inventory JSON.

The inventory holds resource names, never contents: an estate inventory that
quoted a row would put a live flag value in a build artefact. Project ID and
project number appear and are not redacted in the private inventory, so it can
be used for exact resource comparisons. It can also contain personal email
addresses and IAM bindings. Do not publish it; sanitise a separate copy if
you need to share evidence.

`evidence/raw/` is gitignored and unrelated (VER-6, transcripts).

### Where it can still be wrong

Cloud Asset Inventory is the backstop enumerator — the only one that can find a
resource type nobody wrote a function for. Everything else is a hand-written
list of surfaces, and a hand-written list is only as complete as the person who
wrote it. Asset Inventory is what makes that tolerable rather than a known hole.

It is also the only enumerator that does not ask the API owning the resource,
and it is wrong in two ways that the first run with it enabled (32229645507)
showed at full size — 190 orphans, of which 187 were the backstop's own faults.

**It names resources differently from the API that owns them.** The index calls
a service account `asset:iam.googleapis.com/ServiceAccount` with a full resource
path; `gcloud iam service-accounts list` calls the same one `service_account`
with an email. Nothing in a key-by-key diff joins those, so every resource both
enumerators can see was reported twice and counted as an orphan once.
`asset_native_kind`/`asset_native_id` translate the index into the owning
enumerator's vocabulary before anything is compared.

**It reports resources that no longer exist, for far longer than "minutes".**
After the estate was destroyed the index still returned three `incidents` tables
and a dataset from the phase 0 spikes, weeks after `bq ls` stopped listing them,
along with a secret version and two pool providers already gone. The original
reasoning — the lag errs safe, a false orphan costs a re-run and a missed one
costs a bill — is true of one stale record and false of a hundred, at which
point every run reports `failed` and nobody reads it.

So the backstop is reconciled rather than trusted. An enumerator that reached
its API and answered is authoritative for its kinds: if it does not list what
the index claims, the record is stale. Where that enumerator could *not* answer,
the index fills the gap instead — the case it exists for. An asset type with no
enumerator of its own is asked directly over REST, and only a 404 clears it: a
403 is what both a missing resource and a missing permission return, and a host
with no URL mapping yields no answer at all. Both stay orphans, because the
direction to fail in is still the one that costs a re-run rather than a bill.
