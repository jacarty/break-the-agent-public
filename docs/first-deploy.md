# First deploy

Standing the lab up in a **new GCP project and a new GitHub repository**, from nothing to a seeded estate and a verified teardown.

This is the ordered spine. It says *what runs, in what order, and what breaks if the order is wrong*. It does not repeat the reasoning — each step points at the module README that carries it, and `infra/bootstrap/README.md` in particular is the argument for why the first apply is by hand and why the trust is created with no authority.

Every value below is a placeholder. Substitute your own; nothing here is a deployment value (POR-6).

---

## 1. Preconditions

| | |
| -- | -- |
| `gcloud` | authenticated as a principal with `roles/owner` on the target project, `beta` component installed |
| `terraform` | `>= 1.9`, and see [Version alignment](#2-version-alignment) before running anything |
| `gh` | authenticated against the repository, with `repo` scope |
| `uv` | for the generator, which the estate apply invokes |
| Project | disposable, on a **separate billing account**, with a budget cap (SEC-2) |
| Billing | you can attach and detach it, and a scratch project you are willing to detach for real |

A separate billing account is a requirement, not a preference. The kill switch detaches billing from a project; rehearsing that against an account carrying anything else is a bad afternoon. If the lab shares an account with real work, say so in the docs rather than leaving SEC-2's claim standing unqualified.

## 2. Version alignment

**Do this before step 4.** Terraform records the writing version in state, and that floor only ratchets upward. Steps 4 and 6 run locally; steps 7–9 run in a workflow at the version pinned in `.github/workflows/*.yml`. A local binary newer than the pin produces, on the first dispatch that touches the state it wrote:

```
state snapshot was created by Terraform vX.Y.Z, which is newer than current vA.B.C
```

The two now write **different state prefixes** — `bootstrap/` locally, `estate/` and `billing-guard/` from CI — so a mismatch no longer breaks the sequence on its own. Align them anyway. The moment it bites is the unplanned one: a local `terraform plan` against the estate to debug a failed apply, which is exactly when you do not want a second problem.

Check both and make them agree — bump the pin or match the local binary, either way:

```sh
terraform version
grep -rn 'terraform_version' .github/workflows/
```

The pin appears four times across two workflows. All four move together.

## 3. Ordering constraints

Three of these are not recoverable by re-running, which is the only reason they are called out here rather than left to the step that needs them.

| Constraint | Why | Recoverable? |
| -- | -- | -- |
| Baseline capture **before** the first estate apply | It is an inventory of the project as it was. Afterwards there is nothing left to inventory. | **No** — reproject or accept a weaker teardown claim |
| The workflows are on the **default branch** before any dispatch | GitHub registers `workflow_dispatch` only for workflows present on the default branch. Until they merge, `gh workflow run` fails with "workflow does not exist" — there is no run, and no token exchange to fail. This is the real gate on step 7, the first step that dispatches anything, and it is invisible until you hit it. | Yes — merge |
| Steps 7–9 dispatch from `main` | Every dispatch impersonates `ci-deploy`, and token-creator on that account is bound to `refs/heads/main` (TD-2). A branch dispatch authenticates and then fails at the exchange. Nothing before step 7 dispatches at all — steps 4 and 6 are local applies — so there is no window in which a branch run would have worked. | Yes — re-dispatch from `main` |
| Pool IDs are chosen once | Deleting a pool soft-deletes it for 30 days and the ID cannot be reused. Soft-deleted pools are hidden from `list` unless you pass `--show-deleted`, so a burned name looks free. | **No** — 30 days, or a new name |
| The Identity Platform key id is chosen once | Destroying an API key soft-deletes it and locks the id for 30 days. It lives in `infra/bootstrap/` (ADR 0012) precisely so that a teardown never destroys one — but a key destroyed by hand, or by an early deployment that created it in `infra/hosting/`, burns `<service-name>-identity-platform` for a month. | **No** — 30 days, or a new `arena_service_name` |
| Firestore location is permanent | Cannot be changed after creation, and neither can the database mode. | **No** — destroy and recreate the database |
| Budget guard applies before the estate | The cap has to exist before the spend does. `ci.yml` sequences this for you. | Yes |

Because of the first two rows, **the workflows are merged to the default branch before step 7**, not after it. The live run is the last thing that happens, not something a PR waits on.

---

## 4. Bootstrap, by hand

**`infra/bootstrap/` is applied from a laptop, always** — this step and step 6 both, and every later change to what CI may reach. It is the one module no workflow drives. The first apply could not be automated (it authenticates through the pool it creates and writes state to the bucket it creates, neither of which exists yet); the applies after it *could* have been, and deliberately are not. `infra/bootstrap/README.md` §"Why this module is not automated" carries the argument and the permission table behind it.

```sh
cd infra/bootstrap

cp .env.local.example .env.local
$EDITOR .env.local          # gitignored; placeholders only in the .example
source .env.local

terraform init
terraform apply
```

`github_owner_id` is the numeric account ID (`gh api users/your-org --jq .id`) and is checked alongside the repository name. Names are reusable after a rename or transfer; account IDs are not.

Keep `.env.local`. Step 6 edits two flags in it and re-applies, and a shell that has silently lost them will plan a *revocation* of the access it granted.

Then migrate state into the bucket that apply just created:

```sh
cp backend.tf.migrate backend.tf
terraform init -migrate-state \
  -backend-config="bucket=${TF_VAR_state_bucket_name}" \
  -backend-config="prefix=bootstrap"
# answer yes, then
rm terraform.tfstate terraform.tfstate.backup
```

`backend.tf` is gitignored so that this step works in a fresh clone. That has a consequence worth internalising: **every later local run, in any clone, needs the `cp` first**, then a plain `terraform init` with the same two `-backend-config` flags and no `-migrate-state`. Skip it and Terraform silently falls back to local state, starting a second empty state file that knows nothing about the pool or the bucket, and the real resources are orphaned from then on.

## 5. Publish the identifiers

These are Actions **variables**, not secrets. They name an identity and a target; they do not authenticate as one (POR-6a, POR-6b). `billing_account_id` and the estate seed are the two secrets.

Use `printf`/`tr -d '\n'` rather than a bare `$( )`. A trailing newline in `GCP_WORKLOAD_IDENTITY_PROVIDER` breaks the token exchange without saying why, and one in `GCP_REGION` reaches resource names.

```sh
# still in infra/bootstrap, with the TF_VARs exported

# bootstrap outputs
gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --body "$(terraform output -raw workload_identity_provider | tr -d '\n')"
gh variable set TF_STATE_BUCKET                --body "$(terraform output -raw state_bucket_name | tr -d '\n')"
gh variable set GCP_PROJECT_NUMBER             --body "$(terraform output -raw project_number | tr -d '\n')"
gh variable set GCP_ARENA_POOL_ID              --body "$(terraform output -raw arena_pool_id | tr -d '\n')"
gh variable set GCP_ARENA_PROVIDER_ID          --body "$(terraform output -raw arena_provider_id | tr -d '\n')"
gh variable set GCP_REGISTRY_REPOSITORY        --body "$(terraform output -raw registry_repository | tr -d '\n')"

# deployment targets
gh variable set GCP_PROJECT_ID --body "your-project-id"
gh variable set GCP_REGION     --body "your-region"

# estate inputs that have no default
gh variable set GCP_BIGQUERY_LOCATION        --body "your-bq-location"
gh variable set GCP_ARTEFACT_BUCKET_NAME     --body "your-project-id-artefacts"   # globally unique
gh variable set GCP_ARTEFACT_BUCKET_LOCATION --body "your-bucket-location"
gh variable set GCP_FIRESTORE_LOCATION       --body "your-firestore-location"     # permanent

# optional — the hosting job's raw-transcript bucket. Unset, CI uses
# "<GCP_PROJECT_ID>-evidence" in GCP_ARTEFACT_BUCKET_LOCATION.
# gh variable set GCP_EVIDENCE_BUCKET_NAME     --body "your-project-id-evidence"  # globally unique
# gh variable set GCP_EVIDENCE_BUCKET_LOCATION --body "your-bucket-location"

# billing guard
gh secret   set GCP_BILLING_ACCOUNT_ID  --body "0X0X0X-0X0X0X-0X0X0X"
gh variable set GCP_BUDGET_AMOUNT       --body "50"
gh variable set GCP_CURRENCY_CODE       --body "GBP"
gh variable set GCP_GUARD_SOURCE_BUCKET --body "your-project-id-billing-guard-src"
gh variable set GCP_ENABLE_KILL_SWITCH  --body "false"

# the estate seed — determinism is a claim about two runs from the same value
gh secret set ESTATE_SEED --body "$(openssl rand -hex 16)"
```

Only the estate inputs **without defaults** are published. The ones with defaults are deliberately left unset: a value repeated in a GitHub variable and in `variables.tf` is a value that can disagree with itself, and the failure is a workflow quietly targeting a differently-named dataset.

**`infra/bootstrap/`'s own inputs are not published at all.** No workflow applies that module — see [§6](#6-grant-ci-its-authority) — so `grant_ci_state_access`, `ci_project_roles`, `enable_ci_deploy_identity` and `state_bucket_location` live only in your local `.env.local` (§4) and in the applied state. `.env.local` is one operator's working copy, not a record of what was applied: if it is missing or stale, reconstruct the module's inputs from the applied state and `terraform plan`, and treat an unexpected destroy in that plan as a wrong input rather than real drift. `GCP_CI_DEPLOY_SERVICE_ACCOUNT` is set in §6 once the identity exists; leave it unset until then rather than creating it empty.

`github_repository` and `github_owner_id` are never published either. They are what the provider's attribute condition checks — which repository on earth can mint tokens into your project — and a second copy in a settings page that no apply reads is a copy that can disagree with the trust while looking authoritative.

`GCP_ENABLE_KILL_SWITCH` is a variable rather than a workflow input because an input defaulting to empty would silently *revoke* on the next apply nobody thought to fill in.

## 6. Grant CI its authority

**Still local, still `infra/bootstrap`.** Granting is another apply of the module from step 4, not a workflow dispatch — there is no bootstrap workflow, and `infra/bootstrap/README.md` §"Why this module is not automated" is the argument. In one line: Terraform refreshes a whole module before it can plan any of it, and `workloadIdentityPools.get` lives only in `roles/iam.workloadIdentityPoolAdmin`, which no principal this module grants holds. Granting it would let an identity reachable from `main` rewrite the condition on the pool that authenticates it.

Two flags, in this order, each with its own plan you read before applying:

```sh
cd infra/bootstrap
source .env.local

# 6a — state access. Set grant_ci_state_access=true in .env.local, then:
source .env.local
terraform plan -input=false -out=tfplan   # expect: 1 to add
terraform apply -input=false tfplan

# 6b — the deploy identity. Set enable_ci_deploy_identity=true, then:
source .env.local
terraform plan -input=false -out=tfplan   # expect: the SA, its roles, two bindings
terraform apply -input=false tfplan
rm tfplan

gh variable set GCP_CI_DEPLOY_SERVICE_ACCOUNT \
  --body "$(terraform output -raw ci_deploy_service_account | tr -d '\n')"
```

**The order is load-bearing and the flags are sticky.** `ci_deploy_state` — the deploy identity's own grant on the state bucket — is gated on *both* flags, so arming 6b first produces an identity that cannot read state, which surfaces as a `403` on `terraform init` and reads as a broken trust rather than a missing role. And because the flags are variables rather than one-way switches, an apply run from a shell that has lost them plans `1 to destroy` on the binding CI is currently using. Edit `.env.local`, don't export inline.

Then the one binding Terraform cannot make, because a budget lives on the billing account and that is outside the module's provider scope:

```sh
gcloud beta billing accounts add-iam-policy-binding "0X0X0X-0X0X0X-0X0X0X" \
  --member="serviceAccount:$(terraform output -raw ci_deploy_service_account)" \
  --role="roles/billing.costsManager"
```

`costsManager` creates and reads budgets. It cannot attach or detach a billing account, so it does not overlap with the guard's own `billing.projectManager` grant.

## 7. Capture the teardown baseline

**Before the first estate apply, and it cannot be reconstructed afterwards.**

```sh
gh workflow run teardown.yml --ref main -f mode=capture-baseline
```

Download the `teardown-evidence-<run_id>` artifact and keep
`evidence/teardown/baseline.json` private. This public export ignores deployment
inventories. For local deployment, the scripts read that file from your working
tree. For teardown through Actions, use a private deployment repository with
its own baseline committed via PR; the workflow reads it from the checkout. Teardown is verified as a *return to a baseline* rather than as an empty project — which is also the only form of the claim bootstrap can satisfy, since bootstrap survives a destroy by design (TD-1).

Without a baseline available to the check, it still runs, but every resource in the project falls in scope, including bootstrap's, and the result is noise rather than a verdict.

**Recapture whenever the set of resources expected to survive a destroy changes.** Two things trigger that, and they are the same rule: an API that adds an enumerator (below), and *bootstrap gaining a resource*. A registry added to bootstrap after the baseline was taken, for example, scores as an orphan surviving its own destroy. Recapturing needs a pre-estate project, so the moment to notice is before §8, not after. TD-29.

**A baseline is only comparable to checks that read the same surfaces.** A baseline captured before `cloudasset.googleapis.com` was enabled holds nothing the asset backstop reports, so the first check that runs the backstop counts the whole project fabric — default VPC routes, subnetworks, firewall rules, Dataplex entry groups — as orphaned. The reference deployment hit exactly this (TD-9). If §5 enabled an API that adds an enumerator, the baseline has to be recaptured, and recapturing means returning the project to a pre-estate state first: run §9's destroy, confirm it, then capture.

## 8. Apply and seed the estate

```sh
gh workflow run ci.yml --ref main -f apply_estate=true
```

One dispatch runs the gates, then `infra/billing-guard/`, then `infra/estate/`, then the generator. The cap exists before the spend, and this dispatch needs no manual step. NFR-5 is still not met overall: the tool harness needs three hand-made `gcloud` grants, and any *re*seed must be followed by an `infra/estate` apply to restore BigQuery row access policies (the README's "Deploying it yourself" has both).

**The determinism check is the exit criterion.** The seed step prints two lines; take them from the run log:

```
manifest fingerprint <32 hex>
plan fingerprint     <32 hex>
```

Dispatch again without touching `ESTATE_SEED`, and compare both. They must be identical. The fingerprints are hashes of values rather than values, so they are safe to record here, in a commit message, and in the write-up.

**Do not hash the manifest object — the check will always fail.** `gcloud storage hash` covers the whole file including `generated_at`, which is wall-clock and differs between any two runs by construction. `schemas.md` §3 mandates that field and the exit criterion says byte-identical; the collision is resolved in `src/bta/generator/manifest.py`, whose `fingerprint()` hashes the manifest with `generated_at` removed and nothing else. When the manifest was written is metadata about the run, not about the estate. Hashing the object measures the clock and reports it as non-determinism.

The manifest lands in `gs://<state-bucket>/fixtures/fixture-manifest.json` and holds live flag values (`schemas.md` §3). It never reaches an artifact, a log, or the repository — and never a model context, which is what `.claudeignore` and the `deny` rules in `.claude/settings.json` are for. The fingerprints are the supported way to reason about it without opening it.

## 9. Verify teardown

```sh
gh workflow run teardown.yml --ref main -f mode=destroy-and-verify
```

Terraform destroys the estate, then the guard — that order, because removing the cap while the thing it caps is still running is the wrong order to fail in. Then a scan compares the project against the baseline and writes a log, which uploads whether the check passed or failed.

The scan exists because "Destroy complete!" is a statement about state, and the survivors of a teardown are by definition the resources state never tracked. Surfaces the scan could not read fail it rather than counting as clear.

`infra/bootstrap/` is not destroyed: `prevent_destroy` on the state bucket and `disable_on_destroy = false` on the APIs are both deliberate. That is TD-1, and it is open. The baseline handles it — bootstrap's survivors were captured before the estate existed, so they are out of scope by construction rather than by exemption, and they did not appear in the first run's orphan list.

**What did appear was the billing guard.** Deploying its Cloud Function leaves Google-managed service agents behind, and leaves a `gcf-v2-sources-*` build staging bucket that no apply owns. Objects deleted from that bucket are retained under a seven-day soft-delete policy and are still billed, so **a teardown run within seven days of a billing-guard deploy cannot verify NFR-6** and will report that bucket as an orphan. Wait out the retention, or remove the generations by hand — note that clearing the bucket's soft-delete policy does *not* purge what is already under it:

```sh
gcloud storage objects list 'gs://gcf-v2-sources-<project number>-<region>/**' \
  --format='value(name,generation)'
gcloud storage rm 'gs://gcf-v2-sources-<project number>-<region>/<name>#<generation>'
```

This step also needs `cloudasset.googleapis.com` enabled, which `infra/bootstrap/` does. The asset enumerator is the scan's backstop and a surface it cannot read fails the run, so with that API off the check reports `NFR-6 NOT verified` however clean the project is. TD-9 has both.

## 10. Rehearse the billing kill switch

Independent of everything above and runnable in parallel — but on a **scratch project you are willing to detach billing from**, never the lab. Steps are in `infra/billing-guard/README.md`.

SEC-3 says *tested*, not deployed. Testing "budget alert → Pub/Sub → Cloud Function → disable billing" means actually disabling billing, which is untestable later without destroying the lab. Arm the real guard by setting `GCP_ENABLE_KILL_SWITCH` to `true` only once the rehearsal has been done for real.

## 11. Build the image

`infra/hosting/` will not accept a tag — `var.image` must be digest-pinned, because SC-6 commits the reference run to being reproducible and a tag can be moved afterwards. So the image is built and pushed before hosting is applied.

The registry itself belongs to `infra/bootstrap/` (`registry.tf`), for two reasons: hosting cannot create the repository it needs an image to already be in, and the image must survive a `terraform destroy` of the estate and the site.

**Always pass `--ref`, and pass the ref you actually intend to deploy.** A dispatch with no `--ref` runs against the default branch and builds *that* code. It exits 0, prints a valid digest, and reports nothing wrong — so a digest built from `main` while you believed you were deploying a branch is a silent substitution, and the thing you notice later is behaviour, not an error.

```sh
gh workflow run image.yml --ref main
gh run watch
```

It prints the digest-pinned reference to the run summary. Copy it — that is `TF_VAR_image` in the next step.

A dispatch on any *other* ref authenticates and then fails at the token exchange, because impersonating `ci-deploy` is bound to `ci_privileged_ref` (TD-2). That is the control working, not a fault. To build from a branch, either merge it first — which is usually what you want, since an unmerged branch is not a thing anything should be able to deploy by accident — or grant that one ref deliberately through `ci_additional_privileged_refs`, and empty it again afterwards.

To build locally instead — no different in result, and the one path where the code built is simply whatever you have checked out:

```sh
REPO="$(terraform -chdir=infra/bootstrap output -raw registry_repository)"
gcloud auth configure-docker "${REPO%%/*}" --quiet
docker build --tag "$REPO/site:$(git rev-parse HEAD)" .
docker push "$REPO/site:$(git rev-parse HEAD)"
gcloud artifacts docker images describe "$REPO/site:$(git rev-parse HEAD)" \
  --format='value(image_summary.digest)'
```

Either way, confirm the digest belongs to the commit you meant before going on. `git rev-parse HEAD` locally, or the `github.sha` on the workflow run — the failure this catches is deploying different code from the code you reviewed.

## 12. Apply the site

**Prefer the workflow.** `ci.yml` has a `hosting` job, so the supported path is one dispatch:

```sh
gh workflow run ci.yml --ref main \
  -f apply_estate=true -f apply_hosting=true -f build_image=true \
  -f site_provider=google
gh run watch
```

**Pass `site_provider` explicitly.** The workflow's default is `anthropic`, which the reference deployment could never obtain quota for (`docs/results.md`); `google` is the provider the reference run used.

`build_image` builds from the dispatched ref and hands the digest to the apply
as a job output, so nobody transcribes it — which is where the wrong commit
gets deployed, silently, because a build from the wrong ref succeeds and prints
a valid digest. To deploy an image that already exists, turn it off and pass
the digest instead; that is how a reference run is reproduced against the exact
image it used (SC-6):

```sh
gh workflow run ci.yml --ref main \
  -f apply_hosting=true -f build_image=false \
  -f image='<digest-pinned reference>'
```

It resolves the estate outputs, derives `registry_id` from the published registry repository, reads the fixture manifest back out of the state bucket, masks every flag value before Terraform can quote one in an error, applies, and deletes the manifest from the runner. NFR-5 asks for clean-to-deployed with no manual intervention beyond bootstrap, and this is the step that had been missing from it.

The rest of this section is the **manual path**, kept because it is what to fall back to when a first apply into a new project is being debugged, and because reading it is the fastest way to understand what the job does.

`infra/hosting/` applied by hand consumes `terraform output` from both other modules, and nothing reads back the other way (POR-4).

**It takes 28 required variables, none of which has a default.** Terraform refuses to plan on the first one it cannot resolve, so a partial set fails immediately rather than part-way through. They come from five places — the estate's remote state, **bootstrap's** remote state, the GitHub Actions variables published in [§5](#5-publish-the-identifiers), the fixture manifest from [§8](#8-apply-and-seed-the-estate), and you. Collect them first, plan, then apply.

**Three of the inputs are flag values** — `marker_c1`, `marker_f6` and `marker_f4` — and a fourth, `fixture_manifest`, is every flag value at once. They come from the manifest the generator wrote in §8. The manifest stays outside the repository and stays the only file that holds them: do not put them in a `.tfvars` file, which is exactly the file the pre-commit hook exists to keep out of git.

### 12.1 Collect the inputs

Both `terraform output` reads below need this clone's `infra/bootstrap/backend.tf` in place — see [§4](#4-bootstrap-by-hand). Without it Terraform falls back to empty local state and the outputs come back blank rather than erroring, which is the same silent-empty trap §5 warns about.

```sh
export TF_STATE_BUCKET="$(terraform -chdir=infra/bootstrap output -raw state_bucket_name)"

# One comes from bootstrap itself: the Identity Platform browser key. It is
# created there, not here, because destroying an API key locks its id for 30
# days and this module is destroyed every cycle — see
# `infra/bootstrap/identity_platform_key.tf`.
export TF_VAR_identity_platform_api_key="$(terraform -chdir=infra/bootstrap \
  output -raw identity_platform_api_key)"

# Eight of the twenty-eight come from the estate's state. The prefix is
# committed in versions.tf, so only the bucket is supplied here.
terraform -chdir=infra/estate init -input=false -reconfigure \
  -backend-config="bucket=$TF_STATE_BUCKET" >/dev/null

eval "$(terraform -chdir=infra/estate output -json | jq -r '{
  estate_dataset_id:                  .dataset_id.value,
  estate_ops_dataset_id:              .ops_dataset_id.value,
  artefact_bucket_name:               .artefact_bucket_name.value,
  firestore_database_id:              .firestore_database_id.value,
  integration_secret_id:              .integration_secret_id.value,
  runtime_service_account_email:      .runtime_service_account_email.value,
  shared_service_account_email:       .shared_service_account_email.value,
  provisioning_service_account_email: .provisioning_service_account_email.value
} | to_entries[] | "export TF_VAR_\(.key)=\(.value|@sh)"')"

# Five more are already published as Actions variables (§5).
export TF_VAR_project_id="$(gh variable get GCP_PROJECT_ID)"
export TF_VAR_region="$(gh variable get GCP_REGION)"
export TF_VAR_project_number="$(gh variable get GCP_PROJECT_NUMBER)"
export TF_VAR_arena_pool_id="$(gh variable get GCP_ARENA_POOL_ID)"
export TF_VAR_arena_provider_id="$(gh variable get GCP_ARENA_PROVIDER_ID)"

# `registry_id` is the BARE repository ID — the last segment of bootstrap's
# `registry_repository` output, not the output itself. The output is
# `<region>-docker.pkg.dev/<project>/<repo>`; passing it whole makes Terraform
# build an Artifact Registry resource ID containing a hostname and slashes, and
# the apply fails on a resource that looks correctly configured.
REGISTRY_REPOSITORY="$(gh variable get GCP_REGISTRY_REPOSITORY)"
export TF_VAR_registry_id="${REGISTRY_REPOSITORY##*/}"

# From §11.
export TF_VAR_image='<the digest-pinned reference>'

# The evidence bucket for raw transcripts (VER-6) — created by this module,
# so these name a bucket that does not exist yet. Match what CI would use.
export TF_VAR_evidence_bucket_name="${TF_VAR_project_id}-evidence"   # globally unique
export TF_VAR_evidence_bucket_location='<your-bucket-location>'

# Yours. Model identifiers are inputs, not repository constants (POR-6), and
# are recorded per run (FR-22a) — set them to the exact identifiers the run is
# meant to use. Every model is a Vertex model now (FR-22, amended 2026-08-28).
#
# `vertex_location` is separate from `region` because model availability and
# Cloud Run availability are not the same map, and `anthropic_vertex_location`
# is separate again because partner models reach fewer regions than Gemini.
# europe-west2 serves neither Claude nor, at the time of writing, Mistral —
# europe-west1, europe-west4 and the `global` endpoint do. Confirm before
# pinning; OD-8 requires it.
export TF_VAR_model_anthropic='<vertex publisher model id>'
export TF_VAR_model_xai='<vertex publisher model id>'
export TF_VAR_model_google='<vertex publisher model id>'
export TF_VAR_vertex_location='europe-west1'
export TF_VAR_anthropic_vertex_location='europe-west1'   # or '' to use the above

# Yours, and secret. The access code is a password, not a word — FR-2 is right
# that it is not a security boundary, and it is still the only thing between
# the open internet and the daily token ceiling.
export TF_VAR_access_code="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
export TF_VAR_suite_bypass_token="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"

# There are no provider API keys. Every model is reached through Vertex under
# the runtime service account's own IAM (FR-22/SEC-9, amended 2026-08-28).

# The flag values, read from the manifest and never written anywhere else.
# §8's seed step uploads it to the state bucket and deletes it from the runner —
# it is never an artifact, a log line or a file in the repository. Fetch it to a
# path outside the clone:
#
#   gcloud storage cp "gs://$TF_STATE_BUCKET/fixtures/fixture-manifest.json" ~/bta-fixture-manifest.json
#
M=~/bta-fixture-manifest.json           # never inside the repository
export TF_VAR_fixture_manifest="$(cat "$M")"
export TF_VAR_marker_c1="$(jq -r '.flags[]|select(.id=="C1").value' "$M")"
export TF_VAR_marker_f6="$(jq -r '.flags[]|select(.id=="F6").value' "$M")"
export TF_VAR_marker_f4="$(jq -r '.flags[]|select(.id=="F4").value' "$M")"
```

Record the access code and the bypass token somewhere before you lose the shell — nothing reads them back out, and Secret Manager holds versions the operator cannot see through Terraform.

Two of these are worth a moment. `marker_f4` is F4's value and the generator *does* plant it, as a version of the estate's integration secret; it is supplied here anyway because at L1 and L2 the key sits in the Onboarding system prompt by design (FR-14), and the runtime service account deliberately holds no estate access (SEC-11) — so it cannot read the estate's copy. Hosting keeps its own. Without it the Onboarding agent cannot be started at L1 or L2 at all. And the values live in this shell's environment for the length of the apply, which is the same exposure a `-var` flag has, minus the shell history.

### 12.2 Plan before you apply

`terraform validate` cannot see any of this. It checks syntax and types; every failure mode above is a *value* — a missing variable, a repository ID with a hostname in it, a digest from the wrong commit. The plan is the first step that resolves them against a real project.

```sh
terraform -chdir=infra/hosting init -input=false \
  -backend-config="bucket=$TF_STATE_BUCKET"
terraform -chdir=infra/hosting plan -input=false
```

A clean plan here is the last cheap checkpoint before anything bills.

### 12.3 Apply

```sh
terraform -chdir=infra/hosting apply -input=false
```

`terraform -chdir=infra/hosting output -raw service_url` is the site. Read `infra/hosting/README.md` before sharing that URL: it puts a deliberately vulnerable agent on the public internet, and the access code is not a security boundary (FR-2).

### The 28 required inputs

| Variable | Source |
| -- | -- |
| `project_id`, `region` | you, via `GCP_PROJECT_ID` / `GCP_REGION` |
| `project_number` | bootstrap output, via `GCP_PROJECT_NUMBER` |
| `arena_pool_id`, `arena_provider_id` | bootstrap outputs, via `GCP_ARENA_POOL_ID` / `GCP_ARENA_PROVIDER_ID` |
| `registry_id` | bootstrap — the **last segment** of `registry_repository`, not the whole output |
| `identity_platform_api_key` | bootstrap output — created there because its destroy is irreversible for 30 days |
| `estate_dataset_id`, `estate_ops_dataset_id` | estate outputs `dataset_id`, `ops_dataset_id` (the names differ) |
| `artefact_bucket_name`, `firestore_database_id`, `integration_secret_id` | estate outputs |
| `runtime_service_account_email`, `shared_service_account_email`, `provisioning_service_account_email` | estate outputs |
| `image` | §11, digest-pinned |
| `evidence_bucket_name`, `evidence_bucket_location` | you — the bucket this module creates for raw transcripts; CI defaults them from `GCP_EVIDENCE_BUCKET_*` or the project ID |
| `model_anthropic`, `model_xai`, `model_google`, `vertex_location` | you |
| `access_code`, `suite_bypass_token` | you — **secret**, generate, do not choose |
| `fixture_manifest`, `marker_c1`, `marker_f6`, `marker_f4` | the §8 manifest — **flag values**, never into a file in the repository |

Fourteen more have defaults. Two of them decide what the site costs while nobody is using it:

```sh
# A verification run is not measuring NFR-1's p95, and one warm instance at
# 2 vCPU / 2 GiB bills continuously whether or not anyone visits (NFR-4).
export TF_VAR_min_instance_count=0
export TF_VAR_cpu_idle=true
```

Restore the defaults (`1` and `false`) before any run whose latency figures get published — a cold start on a container importing three provider SDKs is exactly what they exist to avoid. Seven are limits, left alone unless a run needs them lowered: `rate_limit_per_minute`, `requests_per_persona_per_day`, `daily_token_ceiling`, `gate_attempts_per_minute`, `gate_attempts_per_hour`, `gate_attempts_per_hour_global`, `max_instance_count`. The remaining five are `service_name`, `site_provider` (defaults to `anthropic` — set `google`, as in §12), `evidence_retention_days`, `anthropic_vertex_location` and `xai_vertex_location`. **For a first verification run, lower `daily_token_ceiling` well below its 2,000,000 default** — enough for the handful of turns F1 and F2 need by hand, and nowhere near a figure that matters if something loops.

---

## Variable reference

Everything the workflows read — `ci.yml`, `image.yml` and `teardown.yml`. `ci.yml`'s hosting job reads the `BTA_*` rows below; applied by hand, `infra/hosting/` takes its inputs from the shell instead, and its 28 required variables are listed in [§12](#the-28-required-inputs). Secrets are marked; the rest are Actions variables by design, because they name identities and targets rather than authenticating as one.

`infra/bootstrap/`'s inputs are absent from this table on purpose. That module is applied locally and reads `.env.local`, so a GitHub variable naming one of its inputs would be a setting nothing consults.

| Name | Source | Consumed by |
| -- | -- | -- |
| `GCP_PROJECT_ID` | you | ci, teardown |
| `GCP_REGION` | you | ci, teardown |
| `GCP_PROJECT_NUMBER` | bootstrap output | ci, teardown |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | bootstrap output | ci, teardown |
| `TF_STATE_BUCKET` | bootstrap output | ci, teardown |
| `GCP_ARENA_POOL_ID` | bootstrap output | ci, teardown |
| `GCP_ARENA_PROVIDER_ID` | bootstrap output | ci, teardown (and read by hand for hosting — §12) |
| `GCP_REGISTRY_REPOSITORY` | bootstrap output | image (and, last segment only, hosting's `registry_id` — §12) |
| `GCP_CI_DEPLOY_SERVICE_ACCOUNT` | bootstrap output, published in §6 | ci, teardown |
| `GCP_BIGQUERY_LOCATION` | you | ci, teardown |
| `GCP_ARTEFACT_BUCKET_NAME` | you | ci, teardown |
| `GCP_ARTEFACT_BUCKET_LOCATION` | you | ci, teardown |
| `GCP_FIRESTORE_LOCATION` | you | ci, teardown |
| `GCP_EVIDENCE_BUCKET_NAME`, `GCP_EVIDENCE_BUCKET_LOCATION` | you | ci (hosting) — optional; default `<project>-evidence` in the artefact bucket's location. **Not read by `teardown.yml`** — see TD-36 |
| `GCP_BUDGET_AMOUNT` | you | ci, teardown |
| `GCP_CURRENCY_CODE` | you | ci, teardown |
| `GCP_GUARD_SOURCE_BUCKET` | you | ci, teardown |
| `GCP_ENABLE_KILL_SWITCH` | you | ci, teardown |
| `GCP_BILLING_ACCOUNT_ID` | you | **secret** — ci, teardown |
| `ESTATE_SEED` | you | **secret** — ci |
| `BTA_MODEL_ANTHROPIC`, `BTA_MODEL_GOOGLE`, `BTA_MODEL_XAI` | you | ci (hosting) |
| `BTA_VERTEX_LOCATION` | you | ci (hosting) |
| `BTA_ANTHROPIC_VERTEX_LOCATION`, `BTA_XAI_VERTEX_LOCATION` | you | ci (hosting) — may be empty |
| `BTA_MIN_INSTANCE_COUNT`, `BTA_CPU_IDLE`, `BTA_DAILY_TOKEN_CEILING` | you | ci (hosting) — optional, defaults in the module |
| `BTA_ACCESS_CODE` | you | **secret** — ci (hosting) |
| `BTA_SUITE_BYPASS_TOKEN` | you | **secret** — ci (hosting) |

The access code and the bypass token are repository secrets rather than
generated per apply, because the site is redeployed more often than it is
re-shared: a code that changed on every apply would be a code nobody could
hand out. They are still deployment secrets under FR-2 and NFR-3a — held by
the operator, never in the repository, never in agent context.

A destroy still evaluates variables, which is why `teardown.yml` needs the billing-guard inputs even though nothing reads them.

## When it goes wrong

| Symptom | Cause |
| -- | -- |
| `state snapshot was created by Terraform vX` | Local binary newer than the workflow pin — [§2](#2-version-alignment) |
| `gh workflow run` reports the workflow does not exist | Not on the default branch yet. `workflow_dispatch` is registered from there only — [§3](#3-ordering-constraints) |
| `terraform output` prints nothing, and the `gh variable set` calls silently store empty values | No `backend.tf` in this clone, so Terraform fell back to empty local state. `cp backend.tf.migrate backend.tf` and re-init — [§4](#4-bootstrap-by-hand) |
| Token exchange succeeds, `terraform init` fails `403` on the state bucket | `GCP_CI_DEPLOY_SERVICE_ACCOUNT` unset, or §6 not done. The exchange working and the role being absent are different problems and this is the second one — [§6](#6-grant-ci-its-authority) |
| Token exchange fails from a branch | Working as intended — impersonation is bound to `main` (TD-2). Re-dispatch with `--ref main`, or grant the branch deliberately via `ci_additional_privileged_refs` |
| A `terraform plan` in `infra/bootstrap` proposes destroying `ci_state` or `ci_deploy` | The shell lost the flags in `.env.local`. `source .env.local` and re-plan; do not apply — [§6](#6-grant-ci-its-authority) |
| `No value for required variable` | A no-default input missing from the [reference table](#variable-reference) and from the workflow's `env:` block — or, in `infra/hosting/`, an unexported `TF_VAR_*` from [§12.1](#121-collect-the-inputs). All 28 are required. On a `teardown.yml` hosting destroy naming `evidence_bucket_*`, see TD-36 |
| Hosting apply fails building an Artifact Registry resource ID | `registry_id` was given bootstrap's whole `registry_repository` output. It wants the last segment — [§12.1](#121-collect-the-inputs) |
| The deployed site behaves like an older commit | `gh workflow run image.yml` was dispatched with no `--ref`, so it built the default branch. Nothing errors — [§11](#11-build-the-image) |
| Pool name unavailable but not listed | Soft-deleted for 30 days. `gcloud iam workload-identity-pools list --show-deleted` |
| `NOT_FOUND` on a principalSet binding | Pool propagation. `pool_propagation_delay`, 60s by default, and the error is not retryable |
| Teardown reports every resource stale | No `baseline.json` available to the check — [§7](#7-capture-the-teardown-baseline) |
| Secret scanning blocks a push | Flag payloads are credential-shaped by design (SEC-7). Narrow allowlist entries only; flag values are never allowlistable |

## Related

- `infra/bootstrap/README.md` — the two pools, the deploy identity, why the whole module is applied by hand
- `infra/estate/README.md` — what gets provisioned and where the flags land
- `infra/billing-guard/README.md` — the rehearsal
- `scripts/README.md` — what the teardown scan can and cannot see
- `docs/tech-debt.md` — TD-1, which this sequence makes live
