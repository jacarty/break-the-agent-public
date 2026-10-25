# infra/bootstrap

One-time groundwork that has to exist before any other module can run. Five things:

1. **The CI trust** — a workload identity pool and provider that let GitHub Actions exchange its OIDC token for Google credentials, scoped to one repository. No key exists anywhere in the flow (SEC-6). Roles are granted directly to the federated principal wherever that is possible; the estate needs two it may not hold, so there is also one service account it impersonates — see [The deploy identity](#the-deploy-identity). Impersonation is still keyless, and the keyless property is one of CI's access path rather than of the project.
2. **The arena trust** — a *second* pool, trusting Identity Platform and mapping `attribute.team`, through which L3 federates personas. Separate from the CI pool by requirement (SEC-8). See [The two pools](#the-two-pools).
3. **The Terraform state bucket** — used by this module and every module after it.
4. **The CI deploy identity** — off by default, and the only service account this module creates.
5. **The image repository** — Artifact Registry, one repository, holding the site's container image. See [Where the image lives](#where-the-image-lives) for why it sits here rather than in `infra/hosting/`.

Both pools are created with **no authority whatsoever**. Every role, on both, is granted by the module that needs it.

This module does not provision the estate, the hosting layer, budgets, or anything the agents touch. **Every apply of it is a local apply, by an operator with owner on the project** — not the first one only. It is the "documented bootstrap" that NFR-5 carves out of the `terraform apply` guarantee, and the carve-out covers the whole module rather than its first run. [Why this module is not automated](#why-this-module-is-not-automated) is the argument.

---

> **Standing up a new project?** `docs/first-deploy.md` is the ordered sequence — bootstrap, grants, baseline capture, estate, teardown — including the constraints that are not recoverable by re-running. This document is the reasoning behind steps 1–3 of it.

## Before you start

You need:

- `gcloud` authenticated as a principal with `roles/owner` on the target project
- `terraform` >= 1.9
- `gh` authenticated against the repository
- A project on a **separate billing account** with a budget cap (SEC-2)

Configuration is supplied as `TF_VAR_*` environment variables. The repository ships no `terraform.tfvars.example` and you should not create one — POR-6c explains why.

Because this module is re-run by hand every time CI's authority changes, keep those exports in a file rather than retyping them. `.env.local` is gitignored; `.env.local.example` is the placeholder copy:

```sh
cp .env.local.example .env.local
$EDITOR .env.local
source .env.local
```

Nothing in it is a credential — it names a project, a repository and a bucket. Authentication is your own `gcloud auth login`.

`github_owner_id` is the numeric account ID, and it is checked in the provider's attribute condition alongside the repository name. Repository names are reusable after a rename, transfer or deletion; account IDs are not. Conditioning on the name alone means whoever claims that name next can mint tokens into your project.

---

## Step 1 — apply with local state

**This is the only apply in the repository that a workflow cannot do, and the reason is not convenience.** A workflow authenticates through the pool this creates and stores state in the bucket this creates. Neither exists yet, so there is no credential for a runner to present and nowhere for it to write. Automating it would mean putting a service-account key in GitHub, which is the one thing SEC-5 and SEC-6 exist to prevent.

Keep it minimal, then: this apply creates a trust, a bucket and nothing else. Everything afterwards — including attaching every role CI holds — is another local apply of this same module, for the reason in [Why this module is not automated](#why-this-module-is-not-automated).

The state bucket does not exist yet, so this first run keeps state on disk.

```sh
cd infra/bootstrap
terraform init
terraform apply
```

This creates the pool, the provider, and the bucket. The federated principal is granted **nothing** at this point — see [Granting access](#granting-access).

## Step 2 — migrate state into the bucket

```sh
cp backend.tf.migrate backend.tf

terraform init -migrate-state \
  -backend-config="bucket=${TF_VAR_state_bucket_name}" \
  -backend-config="prefix=bootstrap"
```

Answer `yes` when asked to copy existing state. `backend.tf` is gitignored: a committed backend block would break step 1 for the next person to deploy this, since Terraform would try to read a bucket that does not exist yet.

Once the migration succeeds, delete the local state files:

```sh
rm terraform.tfstate terraform.tfstate.backup
```

### Every run after this one

`backend.tf` is gitignored, so a fresh clone has no backend configuration and Terraform will silently fall back to local state — starting a second, empty state file that knows nothing about the pool or the bucket. From then on the two diverge and the real resources are orphaned.

So before any later command, in any clone, including your own after a `git clean`:

```sh
cp backend.tf.migrate backend.tf

terraform init \
  -backend-config="bucket=${TF_VAR_state_bucket_name}" \
  -backend-config="prefix=bootstrap"
```

No `-migrate-state` — there is nothing local to migrate. This module genuinely gets re-run (attaching roles is how), so this is the normal path, not an edge case.

## Step 3 — publish the identifiers to GitHub

These are Actions **variables**, not secrets. They name an identity and a target; they do not authenticate as one (POR-6a, POR-6b).

```sh
gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --body "$(terraform output -raw workload_identity_provider)"
gh variable set GCP_PROJECT_ID                 --body "${TF_VAR_project_id}"
gh variable set GCP_REGION                     --body "${TF_VAR_region}"
gh variable set TF_STATE_BUCKET                --body "$(terraform output -raw state_bucket_name)"
```

A workflow that runs Terraform against `infra/estate/` needs that module's inputs too, since the repository ships no tfvars (POR-6c). Set them once here rather than repeating literals in each workflow file:

```sh
gh variable set GCP_PROJECT_NUMBER             --body "$(terraform output -raw project_number)"
gh variable set GCP_ARENA_POOL_ID              --body "$(terraform output -raw arena_pool_id)"
gh variable set GCP_BIGQUERY_LOCATION          --body "${TF_VAR_bigquery_location}"
gh variable set GCP_ARTEFACT_BUCKET_NAME       --body "${TF_VAR_artefact_bucket_name}"   # globally unique
gh variable set GCP_ARTEFACT_BUCKET_LOCATION   --body "${TF_VAR_artefact_bucket_location}"
gh variable set GCP_FIRESTORE_LOCATION         --body "${TF_VAR_firestore_location}"
```

These are the estate variables with no default. The ones that have defaults are deliberately left unset: a value repeated in a GitHub variable and in `variables.tf` is a value that can disagree with itself, and the failure would be a workflow quietly targeting a differently-named dataset.

`infra/hosting/` and the `image` workflow need two more, both outputs of this module rather than operator-supplied values:

```sh
gh variable set GCP_ARENA_PROVIDER_ID   --body "$(terraform output -raw arena_provider_id)"
gh variable set GCP_REGISTRY_REPOSITORY --body "$(terraform output -raw registry_repository)"
```

**This module's own inputs are not published anywhere durable.** `grant_ci_state_access`, `ci_project_roles`, `enable_ci_deploy_identity` and `state_bucket_location` are passed at apply time and recorded nowhere a later session can trust, because nothing in CI applies this module. **`.env.local` is not that record.** It is gitignored, local to whichever laptop last edited it (§"Before you start" above shows it being created and edited for exactly these variables), and holds only the current operator's working values — not a history of what was actually applied, and not something a different operator or a later session can assume exists or is current. A stale or missing `.env.local` proves nothing either way about what was applied. Reconstruct the inputs from applied state and verify with `terraform plan` before applying — never from whether `.env.local` happens to exist. A repository variable that no workflow reads is a setting that appears to control something and does not.

One variable does come from here, and it is set later rather than now: `GCP_CI_DEPLOY_SERVICE_ACCOUNT`, once the identity exists — see [The deploy identity](#the-deploy-identity). Leave it unset until then. `vars.GCP_CI_DEPLOY_SERVICE_ACCOUNT` renders empty when the variable does not exist, and an empty `service_account:` input is the same as omitting it, so `ci.yml` and `teardown.yml` would authenticate directly. In practice neither can do anything useful until the identity is published, so this is a property that keeps the failure legible rather than one to rely on.

`infra/billing-guard/` is applied by `ci.yml` ahead of the estate, so its inputs are published too. `billing_account_id` is the one **secret** here (POR-6a); the rest name deployment values:

```sh
gh secret   set GCP_BILLING_ACCOUNT_ID     --body "0X0X0X-0X0X0X-0X0X0X"
gh variable set GCP_BUDGET_AMOUNT          --body "50"
gh variable set GCP_CURRENCY_CODE          --body "GBP"
gh variable set GCP_GUARD_SOURCE_BUCKET    --body "${TF_VAR_project_id}-billing-guard-src"
gh variable set GCP_ENABLE_KILL_SWITCH     --body "false"
```

`GCP_ENABLE_KILL_SWITCH` is how the guard is armed, and it stays `false` until the rehearsal in `infra/billing-guard/README.md` has been done for real. Arming is a decision, but a decision is data — a repository variable is an audited settings change, where a manual apply is a step somebody has to remember on a day nobody is thinking about budgets.

`github_repository` and `github_owner_id` are not published either. They are what the provider's attribute condition checks — the two values that decide *which repository on earth* can mint tokens into this project — so they are set once, locally, in the same file as the rest of this module's inputs, and read back from the applied state thereafter. A copy of them living in a settings page that no apply consults is a copy that can disagree with the trust while looking authoritative.

`ci.yml` and `teardown.yml` then authenticate like this:

```yaml
permissions:
  id-token: write   # required, or the OIDC token is never issued
  contents: read

steps:
  - uses: google-github-actions/auth@v3
    with:
      workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}
      project_id: ${{ vars.GCP_PROJECT_ID }}
      service_account: ${{ vars.GCP_CI_DEPLOY_SERVICE_ACCOUNT }}   # empty until TD-5's identity exists
```

---

## Where the image lives

`registry.tf` creates one Artifact Registry repository, `var.registry_id`, and
it lives here rather than in `infra/hosting/` for an ordering reason, not a
tidiness one. `infra/hosting/variables.tf` requires `var.image` to be
digest-pinned, so an image has to exist and be pushed *before* hosting can be
applied at all — a repository hosting itself created could not be pushed to
until after the apply that needs it, which is backwards. And the repository
has to survive a `terraform destroy` of the estate and the site: the lab is
torn down and rebuilt repeatedly (NFR-6), and rebuilding an unchanged image on
every teardown/redeploy cycle would make teardown destructive to something it
has no business touching. It is the same reasoning that puts the state bucket
here rather than in a module that gets destroyed.

The CI deploy identity is granted `roles/artifactregistry.writer` on it —
writer, not admin, since pushing a tag is the whole job and deleting the
repository is not something a workflow should do by accident. `infra/hosting/`
reads it back through the `registry_repository` output and grants its own
runtime service account `roles/artifactregistry.reader`, so the running
revision can pull the image it was built from.

`arena_provider_id` is published alongside `arena_pool_id` for the same
reason: `infra/hosting` derives the STS audience from the project number, the
pool and the provider rather than being handed the assembled string, so an
operator cannot supply an audience that disagrees with the three values it is
built from.

## Where the browser API key lives

`identity_platform_key.tf` creates the public Identity Platform API key that
`signInWithCustomToken` requires (SEC-10, FR-3), and it lives here for the same
class of reason as the registry — except that here the cost of getting it wrong
is measured in weeks.

**Destroying a Google API key soft-deletes it and locks the key id for 30
days.** The id is derived from the service name, so it is the same string on
every deploy. `teardown.yml` destroys `infra/hosting` on every cycle, as NFR-6
requires. A key created there is therefore a key that cannot be recreated for a
month after the first teardown, and no input an operator can change fixes it
without changing the deployment's identity.

So the key belongs to the module that is not destroyed. `infra/hosting/` takes
it as `var.identity_platform_api_key`; `ci.yml` reads it out of this module's
state rather than from a GitHub variable, so it stays out of the CI platform's
configuration. `ADR 0012` carries the full argument, including why
`prevent_destroy` in hosting was the wrong answer.

The key is public by design — it is served to every browser that loads the
persona picker. What makes that safe is SEC-10, configured in
`infra/hosting/identity_platform.tf`: no self-service sign-in provider exists,
so the key alone cannot register anything. The `identitytoolkit`-only
restriction on the key is the second layer and moved with it.

CI holds no API-key role. `roles/serviceusage.apiKeysAdmin` was removed from
`ci_deploy_roles` when the resource moved, since nothing CI applies touches a
key any more.

### Moving it on an existing deployment

**Only needed once, on a deployment that applied the key from `infra/hosting`.**
A new project gets it from a plain bootstrap apply and can skip this.

Order matters, and one wrong step burns the name for 30 days. `state rm`
forgets a resource without touching the cloud; `destroy` is what must never
happen here.

```sh
# 0. Both modules need this clone's backend.tf in place (see Step 2 above).
#    Confirm what exists, and get the key id.
gcloud services api-keys list --format='value(name,displayName)'

# 1. Hosting forgets the key. This deletes NOTHING in the project.
terraform -chdir=../hosting state rm google_apikeys_key.identity_platform

# 2. Bootstrap adopts it. The id is the full resource name from step 0.
terraform -chdir=. import google_apikeys_key.identity_platform \
  "projects/<project-number>/locations/global/keys/<service-name>-identity-platform"

# 3. **Collapse the unknown before planning anything.** Not optional, and the
#    reason is not obvious from the plan it prevents — see below.
terraform -chdir=. apply -target=google_project_service.bootstrap

# 4. Confirm the adoption is clean. This must plan NO changes to the key.
terraform -chdir=. plan
```

**Step 3 is the one that stops this being a 30-day mistake.** `data.google_project.this` carries `depends_on = [google_project_service.bootstrap]`, and this migration adds `apikeys.googleapis.com` to that set. Any change to it defers the data source to apply time, so `.number` is unknown at plan — and `project` on the key is `ForceNew`, so an unknown there plans a **replacement**, which destroys the key and locks the id for 30 days.

The replacement has nothing to do with the id-versus-number bug the resource comment describes. Both produce the same plan line from different causes, and only one of them is fixed by having written the config correctly. An earlier version of this runbook told you to read step 4's plan and attribute any replacement to `project` disagreeing; that advice would have sent you looking at a resource that was already correct.

This is TD-9's mechanism, which already documented that *"any future addition to the bootstrap service map will do the same thing"* — for the two `ci-deploy@` bindings, where a rewritten binding is recoverable. Here it lands on the one resource in the module whose replacement is not. The same targeted apply collapses all three at once, so a non-targeted plan showing the `ci_state` and `ci_impersonation` bindings as replaced is the same cause and the same fix, not a second problem.

Only after step 4 plans no change to the key: apply bootstrap (it also drops
the `apiKeysAdmin` role), then apply hosting with the new input. Hosting's plan
should show the key as removed from *its* state only — if it proposes to
destroy one, stop, because step 1 did not happen.

## Why this module is not automated

There is no `bootstrap.yml`, and its absence is a finding rather than an omission. One existed; it could not run.

Terraform refreshes every resource in a module before it can plan any of it. Planning this module therefore needs read access to all of it: four enabled services, the state bucket, both pools, both providers, the project. No principal this module is able to grant covers that set.

| Resource | Permission needed to refresh | Federated principal | `ci-deploy` |
| -- | -- | -- | -- |
| `google_project_service` ×4 | `serviceusage.services.get` | ✗ | ✓ `serviceUsageAdmin` |
| `google_storage_bucket.state` | `storage.buckets.get` | ✗ — `objectUser` is object-scoped | ✓ `storage.admin` |
| `data.google_project` | `resourcemanager.projects.get` | ✗ | ✓ |
| both pools, both providers | `workloadIdentityPools.get` | ✗ | ✓ `workloadIdentityPoolViewer` |

The federated principal's column is empty by design — `ci_project_roles` is `[]`, and the roles that would fill it are the ones its validation refuses. That is the tripwire working, not a gap.

The last row was `✗` in both columns when this section was first written, which made the argument shorter than it should have been. `workloadIdentityPoolViewer` was then added to `ci_deploy_roles` for an unrelated reason — `scripts/verify-teardown.sh` enumerates pools, and a baseline capture that cannot see them fails rather than reporting a clean project. So `ci-deploy` *can* now refresh this module.

**Refresh was never the binding constraint; apply is.** Creating or updating a pool or a provider needs `workloadIdentityPools.update` and `workloadIdentityPoolProviders.create`, which live only in `roles/iam.workloadIdentityPoolAdmin`. Granting that to `ci-deploy` would let an identity impersonable from `main` rewrite the attribute condition on the provider that authenticates it — widening the trust to a repository someone else controls. That is precisely the escalation TD-2's ref-scoping was built to stop, arriving through a different door. `variables.tf` now refuses the role outright rather than relying on this paragraph.

A plan-only workflow would therefore run. It is still not worth having: it could report drift on a trust it has no authority to correct, and the fix would be a local apply anyway. The module is applied by hand, in full.

`-refresh=false` was considered for the original problem and not taken; a plan generated against unverified state is the wrong artefact to hand an apply that edits a trust.

**What this costs.** The run history of a workflow was the only audited record of *when* CI's authority changed. Local applies leave none. What remains is the versioned state in the bucket — object versions are timestamped and retained twenty deep — and the git history of `variables.tf`. Neither records who ran what, and if that becomes load-bearing it needs a real answer rather than this paragraph. Tracked as TD-6.

---

## Granting access

`ci_project_roles` is empty by default and `grant_ci_state_access` is `false`. The trust is created with no authority on purpose: a failed token exchange and a missing IAM role produce completely different failures, and debugging both at once is the usual reason this takes an afternoon rather than ten minutes.

Verify the exchange works first — a job that authenticates and calls nothing still proves the provider, the condition and the mapping are right. Any workflow reaching the `google-github-actions/auth` step successfully has proved it; a `403` on a later API call is the *next* problem, not evidence against the trust.

**Granting is a local apply**, in the clone where you did step 1. Set `grant_ci_state_access=true` in `.env.local`, then:

```sh
source .env.local

terraform plan -input=false -out=tfplan
terraform apply -input=false tfplan
rm tfplan
```

Plan first and read it, every time. This is the module that decides what CI can reach, and it is the one place in the repository where an unreviewed apply is expensive.

Editing `.env.local` rather than exporting inline is the point: the flags are sticky state, and a later apply that inherits a shell missing `grant_ci_state_access=true` plans `1 to destroy` on the binding CI is currently using. Terraform would be correct to do it — the variable said so.

**This is where the keyless property ends.** `ci_project_roles` is validated against a short list — `roles/owner`, `roles/editor`, the IAM and service-account admin roles — because any of them lets the federated principal create a service account and issue it a key, which is exactly what the trust claims not to allow. The validation is a tripwire against the `roles/owner` a deployer reaches for when an apply fails, not a boundary against anyone determined; it lives in `variables.tf` and can be edited. Removing it should be a decision with a reason attached.

Roles beyond state access belong with the module that needs them, granted to the `ci_principal_set` output rather than accumulated here. A bootstrap module that slowly acquires every role the project will ever need is how a keyless CI identity ends up more privileged than the service account it replaced.

---

## The deploy identity

`infra/estate/` creates service accounts and project IAM bindings, so applying it needs `roles/iam.serviceAccountAdmin` and `roles/resourcemanager.projectIamAdmin` — two of the roles the validation above refuses. NFR-5 needs CI to apply the estate anyway. That was TD-5, and the resolution is `deploy.tf`: the roles go on a named service account, and the federated principal holds only `roles/iam.serviceAccountTokenCreator` on that one account. `ci_project_roles` stays empty and its tripwire stays armed.

Arm it the same way, locally, and only after state access is already granted. Set `enable_ci_deploy_identity=true` in `.env.local`, then:

```sh
source .env.local

terraform plan -input=false -out=tfplan
terraform apply -input=false tfplan
rm tfplan

gh variable set GCP_CI_DEPLOY_SERVICE_ACCOUNT \
  --body "$(terraform output -raw ci_deploy_service_account | tr -d '\n')"
```

Expect the service account, one project role binding per entry in `ci_deploy_roles`, the ref-scoped token-creator binding, and `ci_deploy_state`. That last resource is gated on `grant_ci_state_access` as well, so arming this while state access is still off produces a deploy identity that cannot read state — a `403` on `terraform init` that reads as a broken trust rather than a missing role. Hence the order.

`GCP_CI_DEPLOY_SERVICE_ACCOUNT` is the one value this module publishes to GitHub, and the only one `ci.yml` and `teardown.yml` need from it beyond the pool provider and bucket name.

**What this buys and what it does not.** A principal that can mint a token for an account holding project IAM admin has project IAM admin, one hop away. The privilege is not reduced. Two things do change: no key is ever issued anywhere in the flow, so SEC-5 and SEC-6's keyless claim survives literally; and the authority is attached to a subject with its own declared role list rather than to the trust, so widening it is an edit to `ci_deploy_roles` rather than a widening of what every workflow can do by default. Revoking is deleting one binding.

`ci_deploy_roles` has its own refusal list — `roles/owner`, `roles/editor`, `roles/iam.serviceAccountKeyAdmin`. The last is the one that matters: the entire argument for impersonating rather than granting directly is that no key exists, and an identity that can create keys makes that false. It is a tripwire, not a boundary, since `projectIamAdmin` can grant `keyAdmin` to itself.

**One binding this module cannot make.** A `google_billing_budget` lives on the billing account, which is outside the project and outside this module's provider scope. Grant it once, by hand, as an operator with billing admin:

```sh
gcloud beta billing accounts add-iam-policy-binding "${TF_VAR_billing_account_id}" \
  --member="serviceAccount:$(terraform output -raw ci_deploy_service_account)" \
  --role="roles/billing.costsManager"
```

`costsManager` creates and reads budgets. It cannot attach or detach a billing account, so it is not the guard's own `billing.projectManager` grant and does not overlap with it.

The `roles/iam.serviceAccountTokenCreator` binding is on the account, not the project. At project level it would let CI impersonate every service account in the project — including the SEC-11 runtime identity, which the entire L3 column depends on being unreachable.

---

## Testing a workflow without merging it

The token-creator binding is scoped to `ci_privileged_ref`, so a dispatch from a branch authenticates and then fails at the exchange. That is deliberate (TD-2), and it has a cost: a change to `ci.yml`, `teardown.yml` or any script they run can only be exercised by merging it. A one-line fix to a script that decides NFR-6 becomes commit, PR, merge, dispatch, find the next problem, repeat.

`ci_additional_privileged_refs` names further refs that may impersonate the deploy identity:

```sh
export TF_VAR_ci_additional_privileged_refs='["refs/heads/your-branch"]'

terraform plan -input=false -out=tfplan     # expect exactly 1 to add
terraform apply -input=false tfplan
rm tfplan
```

Then dispatch against that branch:

```sh
gh workflow run teardown.yml --ref your-branch -f mode=destroy-and-verify
```

**Read the plan.** Adding a ref should be `1 to add, 0 to change, 0 to destroy` and nothing else. A destroy or replace on `ci_impersonation["refs/heads/main"]` means a sticky flag came through unset, and applying it would revoke the binding CI is using.

**What this does and does not open.** Nothing on a listed ref fires by itself. The jobs that authenticate as the deploy identity are gated on `workflow_dispatch` in both workflows, and a `pull_request` run carries `refs/pull/N/merge`, which matches no entry. The grant is reachable only when someone dispatches deliberately.

What changes is that the code being run has not been through review — which is the whole point, since reviewing it is what you are trying to do. The residual worth knowing: a branch defines its own triggers, so a listed branch could give itself a push trigger. That needs the same write access as merging, so it moves no trust boundary; it just means the list is not a substitute for caring what is on the branch.

**Clearing it is the same apply with an empty list.** Keep it empty when you are not actively testing a workflow, and empty it before publication — a standing grant to a stale branch is authority nobody is thinking about.

Wildcards are refused by validation. `attribute.ref` is compared for equality, so `refs/heads/*` would match a branch literally named `*`: access for nobody, in a form that reads like access for everybody. That failure would be discovered as a token-exchange error during whatever the branch was meant to test.

**The impersonation binding is ref-scoped, and that is what resolves TD-2.** The pool's attribute condition is repository-scoped, so every ref can authenticate; token-creator on this account is granted to `attribute.ref/refs/heads/main` instead. A dispatch from a branch authenticates fine and then fails at the token exchange, which is the intended behaviour.

`attribute.ref` was mapped on the provider from the beginning so this could be written later — an attribute added after the fact does not retroactively apply to bindings written without it.

This is deliberately not a GitHub Environment protection rule. The workflows' environments (`lab`, `hosting`, `image`) exist as deployment records and the control does not depend on them carrying reviewers — it is enforced by IAM at the point of exchange. Adding reviewers to them is a reasonable second layer; it is not the control.

---

## The two pools

SEC-8 requires the identity CI federates through and the identity personas federate through to be separate pools. They are separate for a concrete reason rather than tidiness: a pool is an attribute namespace, and one pool would mean a condition or binding written for one population silently governs the other.

| | CI pool (`pool_id`) | Arena pool (`arena_pool_id`) |
| -- | -- | -- |
| Trusts | `token.actions.githubusercontent.com` | `securetoken.google.com/<project>` |
| Audience | provider resource name (default) | the project ID |
| Maps | `attribute.repository`, `ref`, `environment` | `attribute.team` |
| Condition | repository **and** owner account ID | `assertion.team != ''` |
| Population | one repository's workflows | untrusted personas, including the attacker |
| Bindings written by | this module (`ci_project_roles`) and `infra/hosting/` | `infra/estate/` |

Both live here because SEC-8 asks for two pools, not two modules, and this module already carries the propagation absorber and the naming discipline that a pool needs. Estate receives the `arena_pool_id` and `project_number` outputs and assembles its own `principalSet://…/attribute.team/<team>` strings, so it depends on two strings rather than on this module's state.

**The arena condition is not an allowlist, deliberately.** Spike 0.1 finding 3 established that the grant attaches to the attribute, not the person: a subject never seen before, presenting `team: team-a`, receives exactly team-a's access. Listing the valid team names on the provider would narrow nothing — those are the only values ever minted — while implying IAM checks *who may claim a team*. It does not, and cannot. The persona picker and the signing service are the root of trust (FR-3). If persona selection ever accepts a caller-supplied team, L3 collapses and no binding in this repository would notice.

**The arena provider is created before Identity Platform exists.** It stores an issuer URI; it does not resolve it. The provider therefore applies cleanly against a project with no Identity Platform tenant, and the first *exchange* is what will fail if the tenant is missing. `identitytoolkit` and `iamcredentials` are enabled by the module that mints persona tokens, not here — see the comment in `apis.tf`.

---

## Things worth knowing

**Direct resource access has a boundary, and SEC-11 sharpens it.** Granting roles to a `principalSet` works for ordinary API access, but a federated principal cannot *be* a service account. Cloud Run's runtime identity is a real service account — and per SEC-11 it must be a dedicated one holding no estate access, never the default compute account. Spike 0.4 measured the default reading the entire estate through the metadata server, which would void the whole L3 column silently. `infra/estate/` creates that identity and `infra/hosting/` deploys with it explicitly; CI's `roles/iam.serviceAccountUser` on it is granted from `infra/hosting/`, not here.

**The scope is the repository, not a branch.** The attribute condition and the principalSet both key on `attribute.repository` alone, so any workflow on any ref — branch, tag, `workflow_dispatch`, or a PR branch pushed by anyone with write access — can exchange a token. `ref` and `environment` are mapped on the provider but not conditioned on. The privileged binding — impersonating the deploy identity — *is* narrowed to `main` (TD-2, below); the state-bucket grant is not, and that bucket also holds the fixture manifest (TD-43).

Forked `pull_request` runs cannot reach this, because GitHub withholds `id-token: write` from them. That is not a general property of forks: `pull_request_target`, `workflow_run` and `issue_comment` workflows run in base-repository context with token issuance available, and are the standard route by which fork-authored content reaches privileged credentials. Do not add one here without conditioning on `attribute.environment` first.

**Pool IDs are effectively permanent.** Deleting a pool soft-deletes it for 30 days and the ID cannot be reused (spike 0.1 finding 6, which tabulates the IDs the spikes burned and when they free up). Soft-deleted pools are hidden from `gcloud iam workload-identity-pools list` unless you pass `--show-deleted`, so a name can be unavailable while appearing free. A failed first apply that you want to retry under a clean name costs you that name for a month.

**IAM bindings wait on pool propagation.** `pool_propagation_delay` (60s by default) pauses before any binding naming the principalSet is written, because spike 0.1 saw pool reads return `NOT_FOUND` immediately after creation. The resulting error is a bad request rather than a retryable one, so Terraform will not recover from it on its own.

**This module does not satisfy NFR-6, and that is unresolved.** `terraform destroy` here will fail until the `prevent_destroy` block on the state bucket is removed by hand, and `disable_on_destroy = false` on the enabled APIs means destroy is not a full revert either. Both are deliberate: deleting the state bucket orphans every resource in every other module, and disabling APIs breaks resources other modules still depend on.

NFR-6 as written says `terraform destroy` removes every provisioned resource, with no carve-out for bootstrap. Either the requirement needs amending or this module needs to change; asserting the carve-out in this README would be a PRD change made in the wrong document (`process.md` §What gets written down). Tracked in `docs/tech-debt.md`.
