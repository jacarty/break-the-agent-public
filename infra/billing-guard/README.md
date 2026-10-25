# infra/billing-guard

SEC-2's hard cap and SEC-3's kill switch.

```
Cloud Billing budget  →  Pub/Sub topic  →  Cloud Function  →  detach billing
```

The budget lives on the billing account and is filtered to one project, so
another project on the same account cannot push the lab over its cap.

## What it does not protect against

Budget notifications lag actual spend by hours. This stops *sustained* spend,
not a spike. The control that bounds a spike is NFR-3's daily token ceiling,
which sits an order of magnitude below the cap — the guard is the second line,
and treating it as the first is the mistake this paragraph exists to prevent.

Detaching billing does not delete anything. Resources stop serving and data
survives until the project is deleted. Teardown is `terraform destroy`, not
this.

## What `billing.projectManager` actually grants

Exactly two permissions, both writes:
`resourcemanager.projects.createBillingAssignment` and
`.deleteBillingAssignment`. It carries **no read**, which is why the guard also
holds `roles/browser` — `main.py` reads billing state before it writes, and
`GetProjectBillingInfo` needs `resourcemanager.projects.get`. Without it the
function dies on the read having never attempted the detach. The SEC-3 rehearsal
found this; see `docs/spikes/1-1-sec3-rehearsal.md`.

`roles/browser` rather than a tighter custom role because creating a custom role
needs `roles/iam.roleAdmin` on the project, which `ci_deploy_roles` does not
grant and should not: it would let CI mint and edit any role. Browser's other
five permissions are read-only. The trade is deliberate — see the comment in
`function.tf`.

Re-attaching is still beyond it in practice: creating an assignment also needs
`billing.resourceAssociations.create` on the billing account, which nothing here
grants.

## Arming

`enable_kill_switch` defaults to **false**. In that state the function receives
every notification, makes the decision, and logs `CAP REACHED … billing left
attached` instead of acting.

Deploy false. Rehearse. Then set true. SEC-3 says *tested before the lab is
published, not merely deployed*, and an untested kill switch is
indistinguishable from a tested one right up until the day it runs.

## Deploy

`ci.yml` applies this immediately before the estate, on the same dispatch — the cap has to exist before the thing it caps. There is no manual apply for the lab project; arming is the `GCP_ENABLE_KILL_SWITCH` repository variable, so the decision is an audited settings change rather than a step somebody has to remember.

Locally, backend config is partial as elsewhere:

```sh
terraform init -backend-config="bucket=${TF_VAR_state_bucket_name}"
terraform apply
```

**For the rehearsal, delete the `backend "gcs"` block from a copy of the module.**
`-backend=false` does not work and was wrong here until 2026-08-19: it suppresses
backend *initialisation*, then `apply` parses the block anyway and refuses with
"Backend initialization required". Work from a copy rather than the tree, so the
lab's real state cannot be reached from the rehearsal at all.

No extra credential setup is needed — but only because `versions.tf` sets `user_project_override` and `billing_project` on the provider. Without those, `billingbudgets.googleapis.com` refuses a request that names no quota project, Google attributes the call to its own shared client project, and the budget fails with a `403 SERVICE_DISABLED` citing a project number you have never seen while the API is plainly enabled on yours. `gcloud auth application-default set-quota-project` does **not** fix it; the provider does not read ADC's quota project. Everything up to the budget applies cleanly first, so it reads as a problem with the budget resource rather than with the credential.

Inputs, all `TF_VAR_*` (POR-6 — nothing deployment-specific is committed). Published to GitHub in `infra/bootstrap/README.md`:

| Variable | Notes |
|---|---|
| `project_id`, `region` | |
| `billing_account_id` | `XXXXXX-XXXXXX-XXXXXX`. A **secret**, per POR-6a |
| `budget_amount`, `currency_code` | Currency must match the billing account's |
| `source_bucket_name` | Holds the zipped function source |
| `alert_thresholds` | Notify-only below 1.0; the guard acts at 1.0 |
| `enable_kill_switch` | See above |
| `propagation_delay` | `90s`. One absorber — see below |

**A first apply against a clean project waits once, before deploying the function.** Deploying a gen2 function whose trigger runs as another identity makes Eventarc check that the deployer can `actAs` that identity, and it refused with a 403 on `iam.serviceAccounts.ActAs` while the project IAM policy, read directly, already carried the binding. The error is terminal rather than re-polled, so the only other fix is re-running the apply — the manual intervention NFR-5 forbids.

The wait is precautionary rather than proven: the apply that added it also ran against a service account created a run earlier, so the wait and the extra elapsed time are confounded. It costs 90 seconds on create and nothing afterwards, which is cheaper than another failed deploy to settle it.

**There is no Pub/Sub publisher binding, and its absence is deliberate** — see the comment in `budget.tf` and TD-7. A binding granting `roles/pubsub.publisher` to `billing-budgets-pubsub@system.gserviceaccount.com` was removed after IAM rejected the principal as non-existent, identically for CI and for an operator with `roles/owner`. Publish permission comes from linking the budget to the topic, which `all_updates_rule` does. A green apply does not demonstrate delivery; the rehearsal below is what does.

## Rehearsal (SEC-3)

Run this against a **scratch project on the same billing account**, not the lab
project. It ends with a project whose billing is detached, which is a state
worth reaching deliberately once and never by surprise.

Nothing in this module's state outlives the scratch project, so work from a copy
with the `backend "gcs"` block deleted (see Deploy above).

Run once, first: the provider cannot read a project whose Cloud Resource Manager
API has never been used, and the module cannot bootstrap that for itself.

```sh
gcloud services enable cloudresourcemanager.googleapis.com iam.googleapis.com \
  --project "$SCRATCH_PROJECT"
```

1. Apply with `enable_kill_switch = true` and `budget_amount = 1`.

2. Publish a notification as the budget service would. This is the whole
   contract — the function reads two numbers out of the message body:

   ```sh
   topic=$(terraform output -raw budget_topic_name)
   gcloud pubsub topics publish "$topic" --project "$SCRATCH_PROJECT" \
     --message '{"budgetDisplayName":"rehearsal","costAmount":5,"budgetAmount":1,"currencyCode":"GBP"}'
   ```

3. Confirm it fired. **Do not filter on severity.** `main.py` uses
   `logging.basicConfig`, so `log.critical()` reaches Cloud Run as unstructured
   stdout with no severity field, and a `severity>=CRITICAL` filter returns
   nothing *on a successful run*. That filter was in these instructions until
   2026-08-19 and made a working guard look like a broken one:

   ```sh
   gcloud logging read 'resource.type="cloud_run_revision"' \
     --project "$SCRATCH_PROJECT" --limit 10 --freshness 10m \
     --format="value(timestamp,textPayload)"
   ```

   Expect `CAP REACHED: … Detaching <project>` followed by
   `billing detached from <project>`.

4. Confirm the effect, which is the only part that actually proves anything:

   ```sh
   gcloud beta billing projects describe "$SCRATCH_PROJECT"   # billingEnabled: false
   ```

5. **Stop here — steps beyond this point cannot be run, and that is a finding
   rather than an omission.** Detaching billing makes the project read-denied,
   so the function no longer executes and nothing further can be published to
   it. Two consequences:

   - The under-cap path (`costAmount: 0.1` logs and does nothing) has to be
     exercised *before* step 2, or left to `tests/test_billing_guard.py`, which
     covers it in `decision.py`.
   - The *already detached* branch in `main.py` needs billing enabled and the
     project detached at the same time, which are contradictory states. It is
     effectively unreachable, so the claim that idempotence comes from
     re-reading `billing_enabled` is **unverified**, and the budget's habit of
     publishing past the cap does not exercise it — the consumer is dead by
     then. See `docs/spikes/1-1-sec3-rehearsal.md`.

6. Record the outcome in `docs/spikes/`, then delete the scratch project. The
   project ID stays reserved for 30 days.

## Re-enabling billing

Re-attaching needs `roles/billing.admin` on the billing account — see
"What `billing.projectManager` actually grants" above for why the guard's own
roles cannot do it. A deliberate human act:

```sh
gcloud beta billing projects link "$PROJECT_ID" --billing-account "$BILLING_ACCOUNT_ID"
```

## Tests

`tests/test_billing_guard.py` covers `function/decision.py` — the inclusive cap
and the malformed-notification cases. `decision.py` imports nothing so it runs
in the repo's own venv; `main.py` holds the clients and is exercised by the
rehearsal above, not by pytest.
