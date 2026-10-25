# Tech debt

Non-blocking findings that were not fixed inline, per `process.md` §"When an agent flags something". Each entry names the source, so a reader can tell a deliberate deferral from an oversight.

Blocking findings are fixed before the PR opens and do not appear here.

Entries are append-only: a closure is a dated paragraph under the original text, not a rewrite of it, so some entries under *Open* below are closed in their heading. This index is the current state.

## Status at publication (2026-10-25)

| State | Entries |
| -- | -- |
| **Open** | TD-1, TD-3, TD-4, TD-6, TD-7, TD-8, TD-9, TD-12, TD-15, TD-20, TD-21, TD-23, TD-24, TD-25 (interactive half), TD-26, TD-28, TD-29, TD-30, TD-32, TD-33, TD-35, TD-36 – TD-49 |
| **Closed** | TD-2, TD-5, TD-11, TD-14, TD-17, TD-19, TD-27, TD-31, TD-34 |
| **Closed by decision or disclosure** | TD-10 (disclosed), TD-16 (ADR 0009), TD-18 (mechanical half; requirement is TD-33), TD-22 (retired by FR-22's amendment) |
| **Resolved in substance** | TD-13 |

TD-36 – TD-49 come from the pre-publication review on 2026-10-25. Each says whether it was verified by reading the code or is reported and not yet reproduced. None was fixed: every fix needs a live deployment to verify, and the ones touching measurement would need a re-run to mean anything. `docs/results.md` §*Found in pre-publication review* carries the ones that change how the published matrix should be read.

---

## Open

### TD-1. NFR-6 does not accommodate `infra/bootstrap/`

**Source:** code-reviewer, `feat/p1-bootstrap-oidc`. **Requirement:** NFR-6.

NFR-6 says `terraform destroy` removes every provisioned resource, verified rather than assumed, with no carve-out. `infra/bootstrap/` cannot satisfy that as written: `prevent_destroy` guards the state bucket, and `disable_on_destroy = false` leaves the enabled APIs behind. Both are deliberate — destroying the state bucket orphans every resource in every other module, and disabling APIs breaks resources those modules depend on.

Needs a decision, not a code change: amend NFR-6 to scope the guarantee to the estate and hosting, or record an ADR under `docs/decisions/` explaining why bootstrap sits outside it. Asserting the carve-out in a module README is a PRD change made in the wrong document.

**Now live (2026-08-14).** `scripts/verify-teardown.sh` exists and phase 1 verifies teardown with it. The script does not resolve this and deliberately does not pick a side: it compares the project against a baseline captured before the estate was applied, so bootstrap's survivors are out of scope by construction rather than by exemption, and `--baseline /dev/null` runs NFR-6 as literally written for anyone who wants to see it fail. Which of those two is *the* NFR-6 check is the open question, and `process.md` §"What gets written down" puts the answer in `prd.md` via PR rather than in an ADR, since it changes a requirement rather than recording a decision beneath one.

### TD-6. Changes to CI's authority have no audit trail

**Source:** first deploy of the reference project, step 6 of `first-deploy.md`. **Requirements:** SEC-6, NFR-5. **Status:** accepted cost of removing `bootstrap.yml`.

`.github/workflows/bootstrap.yml` existed and could not run. It failed `terraform init` with a `403` on the state bucket, which was the documented chicken-and-egg — the workflow's own apply is what grants CI state access — but fixing that only moved the failure to `plan`. Terraform refreshes every resource in a module before planning any of it, and the module's read requirements exceed what it is able to grant:

| Resource | Refresh needs | Federated principal | `ci-deploy` |
| -- | -- | -- | -- |
| `google_project_service` ×4 | `serviceusage.services.get` | ✗ | ✓ |
| `google_storage_bucket.state` | `storage.buckets.get` | ✗ | ✓ |
| `data.google_project` | `resourcemanager.projects.get` | ✗ | ✓ |
| both pools, both providers | `workloadIdentityPools.get` | ✗ | ✓ *(see amendment)* |

The federated principal's column is empty because `ci_project_roles` is `[]` and the roles that would fill it are the ones its validation refuses — that is the tripwire working.

**Amended 2026-08-16, same day.** The last row read `✗` for `ci-deploy` when this entry was written, and the conclusion rested on it: no principal could refresh the module, therefore no workflow could plan it. The first baseline capture then failed on `iam.workloadIdentityPools.list` — `scripts/verify-teardown.sh` enumerates pools, because bootstrap's survive a destroy by design (TD-1) and the scan has to tell a survivor from an orphan. Fixing that meant granting `roles/iam.workloadIdentityPoolViewer` to `ci-deploy`, which carries `get` and `list` and nothing else, so the row is now `✓`.

**The conclusion survives, on a better argument.** Refresh was never the real constraint. *Applying* this module needs `workloadIdentityPools.update` and `workloadIdentityPoolProviders.create`, which exist only in `roles/iam.workloadIdentityPoolAdmin`. That role on an identity impersonable from `main` means the ability to rewrite the attribute condition on the provider that authenticates it — the escalation TD-2 ref-scoped impersonation to prevent, reintroduced by another route. `ci_deploy_roles`' validation now refuses it explicitly, so the constraint is enforced rather than argued.

A plan-only workflow would now run, and is still not worth having: it could report drift on a trust it cannot correct, and correcting it would be a local apply regardless.

Resolved by deleting the workflow. `infra/bootstrap/` is applied locally, in full, every time — not only its first apply. `ci.yml` and `teardown.yml` are unaffected: they drive `infra/estate/` and `infra/billing-guard/`, both covered by `ci_deploy_roles`.

**What is actually open.** The workflow's run history was the only record of *when* CI's authority changed and by whom. Local applies leave none. Versioned state objects carry timestamps and the git history of `variables.tf` carries intent, but neither identifies an operator. If attribution becomes load-bearing — a second person with owner, or a published claim about who could change the trust — this needs a real mechanism rather than the paragraph in `infra/bootstrap/README.md`. Not blocking phase 1; the project has one operator.

### TD-7. The budget's Pub/Sub delivery path is unverified

**Source:** first deploy of the reference project, step 8. **Requirement:** SEC-3. **Status:** narrowed 2026-08-19 — the rehearsal ran; budget-to-topic publish remains unproven.

`infra/billing-guard/budget.tf` granted `roles/pubsub.publisher` on the alert topic to `billing-budgets-pubsub@system.gserviceaccount.com`, with a comment stating that without it the budget creates fine and silently never delivers. The binding blocked the first deploy twice and has been removed. Both the resource and its comment were wrong:

- **The principal does not resolve.** IAM returns `400 Service account billing-budgets-pubsub@system.gserviceaccount.com does not exist` — identically for the CI deploy identity and for an operator with `roles/owner`, tested directly. It is a global `@system.` account rather than a per-project service agent, so nothing on the consumer side provisions it.
- **It was first misdiagnosed as a propagation race**, and a 90-second `time_sleep` absorber was added on the pattern `infra/bootstrap/` uses for pool propagation. The wait ran in full and the error was byte-identical, which is what ruled timing out.
- **Google's tutorial for this exact architecture grants no such binding.** Publish permission is established by the platform when a budget is linked to a topic, which `all_updates_rule` does.

Two further defects surfaced behind it, both blocking and both now fixed. The provider sent no `X-Goog-User-Project` header, so `billingbudgets` attributed the call to Google's own shared client project and returned `403 SERVICE_DISABLED` for a project number belonging to Google — fixed with `user_project_override` and `billing_project` in `versions.tf`. And `disable_default_iam_recipients = true` with no `monitoring_notification_channels` describes a budget with no recipients at all, which the API rejects as a bare `400 INVALID_ARGUMENT` naming no field. That setting rested on an inverted premise — email fires on threshold crossings, the Pub/Sub topic receives a message roughly every 50 minutes regardless — so it was suppressing the quiet channel and is now `false`.

**What is actually open.** Removing a binding whose comment claimed it prevented silent non-delivery does not by itself establish that delivery works — and neither did the binding, which never applied successfully anywhere. A successful `terraform apply` of this module has never demonstrated that a threshold breach reaches the function, before or after this change. The SEC-3 rehearsal on a scratch project is the only thing that can, and `enable_kill_switch` stays `false` until it has been run for real. This entry closes when the rehearsal has been done and its result recorded, not when the deploy goes green.

**The rehearsal ran on 2026-08-19** against scratch project `example-sec3-rehearsal`, and billing was detached for real. Full record, including four defects a green apply could not have surfaced, in `docs/spikes/1-1-sec3-rehearsal.md`.

**What that closes.** Delivery from the Pub/Sub topic through Eventarc to the function is proven, on a module with no publisher binding — which settles the question this entry was opened for. Removing the binding did not break delivery, because the binding never applied anywhere and delivery never depended on it.

**What stays open, and why this entry is narrowed rather than closed.** Every message in the rehearsal was hand-published in the shape the budget service sends. That a genuine *threshold crossing* causes the budget to publish to the topic is still unobserved, and it is the one link in `budget → Pub/Sub → function → detach` that nothing has exercised. Establishing it needs real spend crossing a real threshold on a budget whose notifications lag by hours, so it is not a rehearsal step — it is something the lab will either demonstrate in normal operation or never demonstrate at all.

**A consequence for arming.** The lab's deployed guard predates the read grant the rehearsal showed is required, so it would fail exactly as the scratch project's did. `GCP_ENABLE_KILL_SWITCH` must stay `false` until an estate dispatch redeploys the guard; arming it before that arms nothing.

**And the grant is not the one the rehearsal used.** The rehearsal applied a one-permission custom role under an operator holding `roles/owner`. CI cannot: creating a custom project role needs `roles/iam.roleAdmin`, absent from `ci_deploy_roles` and not worth adding, since it would let CI mint and edit any role on the project. The lab grants `roles/browser` instead — same `resourcemanager.projects.get`, five further read-only permissions, no new CI authority. Recorded because it generalises: a rehearsal run as owner establishes which permission a workload needs and says nothing about which grant the deploy identity can actually make.

The `time_sleep.guard_identity` absorber covering the Eventarc `actAs` check was kept. It is also unproven — the run that passed had the service account from an earlier run, so the wait and the elapsed time are confounded — but it costs 90 seconds on create and nothing afterwards.

### TD-8. The Firestore rules release cannot be created by a clean apply

**Source:** first estate apply, reference project. **Requirement:** NFR-5. **Status:** open — worked around by import, no durable fix yet.

Creating a `google_firestore_database` causes Google to publish a default ruleset and a release pointing at it. `google_firebaserules_release.firestore` then cannot be *created*, because the release it wants already exists:

```
Error creating Release: Resource already exists - apply blocked by lifecycle params
```

This is not a leftover from a partial run. It happens inside the same apply that creates the database, so every clean deploy hits it. Two contributing details were also wrong and are fixed: the release for the `(default)` database is named `cloud.firestore` flat rather than `cloud.firestore/(default)`, and `replace_triggered_by` on the ruleset forced destroy-then-create where repointing is an in-place update — create being exactly the operation that cannot succeed.

**The workaround is a one-time `terraform import`** of `projects/<project>/releases/cloud.firestore`, after which repointing at each new ruleset works normally. That is manual intervention between `terraform apply` and a working estate, which is what NFR-5 says a deploy must not need.

**What a durable fix has to survive.** A config-driven `import` block would adopt the release on an existing project and fail on one where it genuinely does not exist, so it trades this failure for the opposite one. Not managing the release in Terraform at all means the rules file is published by something else, and the rules are the entire L3 control for F5 and F7 — moving them outside the module to make an apply tidy would be the wrong trade.

Step 9's teardown destroys the database and therefore the release, so the redeploy after it is a genuine clean-project test and will hit this again. That is the point at which this needs an answer rather than a note.

**It did not hit it again (2026-08-19).** The full teardown of run 32253983004 destroyed the estate including the Firestore database, and the redeploy — CI run 32253075843, `apply_estate=true` — applied cleanly with no import and no manual step. So the prediction above is falsified as stated.

**That is an observation, not a fix, and the entry stays open.** Nothing in the module changed between the failing apply and this one, which leaves the cause unexplained: either the original failure needed a condition the clean project did not reproduce — a pre-existing release from an earlier database, most likely — or the import performed during the first apply is still doing the work, because the release now exists and is in state. The second reading would mean this project has *never* run the create path successfully, and a genuinely new project would fail exactly as the first one did.

Distinguishing them needs an apply against a project that has never held a Firestore database. Until then NFR-5 cannot be claimed met on the strength of one clean redeploy: a deploy that works because of a manual step taken once, in a state file that survived, is the failure mode NFR-5 is about.

### TD-9. Deploying the billing guard makes NFR-6 unmeetable for seven days

**Source:** first teardown of the reference project, step 9 (run 32064734066). **Requirement:** NFR-6. **Status:** open — the check is correct, the estate is not yet clean.

The first teardown reported ten orphans. None was a Terraform-provisioned resource that `destroy` missed; every one was a Google-managed side effect of deploying `infra/billing-guard/`'s Cloud Function. Nine were service agents and their `roles/*.serviceAgent` grants, created when `cloudbuild`, `cloudfunctions` and `eventarc` were first used and reclaimed only when those APIs are disabled — which `disable_on_destroy = false` never does. `verify-teardown.sh` already treated the enabled APIs as residue surviving by design, so calling the API by-design and the service agent it created an orphan was the same carve-out reported two ways. `classify()` now names them, and the exemption keys on the principal rather than the role, which is sound only while nothing in `infra/` grants a role to a Google-managed principal. It does not, and `--baseline /dev/null` still reports every `ci-deploy@` and `firebase-adminsdk@` binding as an orphan, which is the behavioural check that the pattern has not gone slack.

**The tenth is real and is what remains open.** Cloud Functions gen2 creates its own build staging bucket, `gcf-v2-sources-<project number>-<region>`. It cannot be redirected at the Terraform-managed source bucket, so no apply owns it and no destroy removes it.

**It also exposed a hole in the check itself.** `gcloud storage ls` reported that bucket empty. It was not: buckets carry a seven-day soft-delete policy by default, `ls` does not show what is under it, and two retained copies of the function source were sitting in it — billed, and invisible to the tool the check had been asking. `_bucket_state` now asks `objects list` both with and without `--soft-deleted`, and reports `live` whenever it cannot establish emptiness, so the exemption cannot be reached by failing to look. This is the same class as spike 0.1 finding 6 on soft-deleted WIF pools, on a surface nobody had thought to re-check.

**Clearing the soft-delete policy does not purge what is already under it.** `buckets update --clear-soft-delete` set `retentionDurationSeconds` to `0` and both objects were still listed afterwards; `--soft-deleted` then started returning `400 Soft delete policy is required to list soft-deleted versions`, so the surface that could see them was gone while the objects remained. They were removed by a generation-scoped `gcloud storage rm gs://…/function-source.zip#<generation>`, which is the operation that actually works on them. `_bucket_state` treats that specific `400` as "no soft-deleted objects" rather than as a failure, because a bucket with no policy cannot hold any — without that it would report `live` forever on exactly the buckets whose policy had been cleared.

The bucket is exempt only while genuinely empty. The general constraint does not expire: **after a billing-guard function deploy, its staging bucket holds soft-deleted objects for seven days, and a teardown in that window cannot verify NFR-6** unless the generations are removed by hand. Either the redeploy schedule accommodates the retention, or teardown grows a step that purges the bucket — which would make the verifier a mutator, and it is read-only by design.

**A third finding, and the one that would have failed the check after the orphans were resolved.** With zero orphans the run still reported `NFR-6 NOT verified`, because `cloudasset.googleapis.com` is not enabled and the asset enumerator is the script's backstop — a surface it cannot read fails the run rather than counting as clear, which is correct and was firing on a gap in bootstrap rather than on the estate. `cloudasset.googleapis.com` and `roles/cloudasset.viewer` are now in `infra/bootstrap/`, which is the only module that is applied before the baseline and survives the destroy. Applied locally on 2026-08-19 per the laptop-only rule in TD-6.

That apply is worth one note of its own. `data "google_project" "this"` carries `depends_on = [google_project_service.bootstrap]`, so adding any service to that map defers the data source to apply time, `project_number` becomes unknown, and every `member` string built from it is unknown — which forces replacement of the two `ci-deploy@` bindings CI authenticates with. The plan reads as a revocation and is not one. Applying the service alone with `-target` first collapses the unknown, after which the plan is the single binding and nothing else. Any future addition to the bootstrap service map will do the same thing.

**A fourth finding, from the first teardown that actually ran the backstop (run 32229645507).** It reported 190 orphans. `terraform destroy` was not at fault in any of them — no dataset, secret or estate resource survived, confirmed directly against `bq ls`, `gcloud secrets list` and the pool listing. 187 were the backstop's own two faults.

Cloud Asset Inventory names resources differently from the APIs that own them, so `asset:iam.googleapis.com/ServiceAccount` and `service_account` were the same survivor counted twice, and every bootstrap resource in the committed baseline came back as an orphan. The baseline could not have helped: it was captured before `cloudasset` was enabled, so it holds 81 records and not one `asset:*` among them. Recapturing it alone would have papered over this — the two vocabularies still would not join.

The index is also stale for far longer than the "lags by minutes" the script assumed. It was still returning three `incidents` tables and a dataset from the phase 0 spikes weeks after BigQuery stopped listing them, plus a secret version and two pool providers already deleted. The original reasoning — a false orphan costs a re-run, a missed one costs a bill — holds for one stale record and fails for a hundred, where the run reports `failed` every time and stops being read.

`verify-teardown.sh` now normalises asset records onto the owning enumerator's kind and id, treats any enumerator that reached its API as authoritative for its kinds, and asks the owning API directly about asset types no enumerator covers, clearing only on a 404. Cleared records are reported under a new `stale` heading rather than dropped, on the same principle as the residue carve-outs. Measured against the same project: 190 orphans to 111, with the 12 cleared records each verified gone by hand — including two Artifact Registry images whose repository is live but empty.

**Resolved on 2026-08-19.** The baseline was recaptured with the backstop enabled, in the pre-estate window between run 32232228356 and the redeploy, and committed. Run 32254566498 reports `NFR-6 verified` — 0 orphans, 0 errors, 0 unread surfaces, 12 index records cleared with a stated reason.

That run is weak evidence on its own: the estate had already been destroyed by the preceding run, so only 2 resources were in scope. The full-estate destroy is run 32253983004, whose check failed only because the new baseline had not merged yet. Re-scored against the merged baseline its 113 orphans come to **1**, and that one — `pubsub.googleapis.com/Topic billing-guard-budget-alerts` — does not exist: `gcloud pubsub topics list` returns nothing. It survived as an orphan because Pub/Sub has no enumerator of its own *and* had no entry in the probe's host map, so no URL could be built and the record stayed in the safe direction. `pubsub.googleapis.com` is now in the map, verified against the live API returning 404 for that exact path. So the full-estate teardown was clean, and NFR-6 holds for it.

**The baseline recapture, dated and justified.** Captured 2026-08-19 by teardown run 32232228356 on commit `af2df32`, in the window between the destroy of run 32253983004 and the redeploy of CI run 32253075843 — the project held no estate at that moment, which is the only state in which a baseline is valid. It replaces an 81-record file with a 221-record one.

That is a 140-record widening of what NFR-6 treats as out of scope, and it is a threshold change in everything but name: the baseline decides what "leaves nothing" means. 110 of the additions are `asset:*` records the old baseline could not contain because `cloudasset` was not enabled when it was captured — default VPC routes, subnetworks, firewall rules, Dataplex entry groups, the Firebase-created browser API key. Those are project fabric, not estate.

**Two additions are not fabric and are blessed rather than fixed.** The `gcf-artifacts` Artifact Registry repository and the `gcf-v2-sources-*` staging bucket are Cloud Functions side effects of deploying the billing guard, in the same class as the service agents above. They now sit in the baseline, so a future teardown will never report them. That removes TD-9's seven-day soft-delete problem by exemption rather than by solving it, and it means the staging bucket's contents are no longer examined at all.

**One known imperfection in the file.** It contains two `artifactregistry/DockerImage` records that the liveness probe subsequently proved return 404. `normalise_assets` runs at capture, but the REST probe runs only during `check`, so stale index records of uncovered types can still enter a baseline. Bounded to those two digests; not worth another destroy cycle to correct.

**A note on the earlier 111.** They are live project fabric the backstop can see and the baseline cannot contain: the default VPC's routes, subnetworks and firewall rules, Dataplex entry groups, the Firebase-created browser API key, the `gcf-artifacts` repository. **The baseline has to be recaptured with the backstop enabled before NFR-6 can pass**, and that capture must happen while the project is genuinely pre-estate. Recapturing it also blesses whatever is live at that moment, so the `gcf-artifacts` repository — a Cloud Functions side effect in the same class as the staging bucket above — enters the baseline as expected residue rather than being fixed.

### TD-10. SEC-2 is not met, and the README claims it is — **disclosed 2026-10-25**

**Source:** phase 1 acceptance, 2026-08-19. **Requirement:** SEC-2. **Status:** open — the deployment cannot satisfy it and the documentation does not say so.

SEC-2 requires a disposable project on a **separate billing account** with a hard budget cap. The reference project is disposable and capped, but it shares the only open billing account on this Google account — the same one every other project of the operator's uses. `gcloud billing accounts list` returns exactly one with `OPEN: True`, and the lab is attached to it.

`README.md` states the requirement unqualified: "Deploy into a **disposable project** on a **separate billing account** … Never into a project sharing an organisation or billing account with anything you care about." As written, the reference deployment does not follow its own instruction, and `prd.md` SEC-2 says the README "refuses to soften this".

**Why it matters beyond the paperwork.** The blast radius of the SEC-3 kill switch is bounded by what else the billing account pays for — that is the reason `infra/billing-guard/variables.tf` gives for requiring a dedicated account. A guard that detaches a shared account's project is still correct, but the *cap* protects a shared account, and a runaway in the lab consumes budget that is not exclusively the lab's.

**What this is not.** It is not a reason to weaken the README. The instruction is right; the deployment is the thing out of compliance. The fix is either a dedicated billing account, or a disclosed deviation stating that the reference deployment shares one and what that costs — `docs/first-deploy.md` already instructs that a shared account be disclosed.

Blocking for publication, because the alternative is a published claim the deployment does not meet. Not blocking for build work.

**Disclosed 2026-10-25.** The README's *Known gaps* and `docs/results.md` §Provenance now state that the reference deployment shared the operator's only billing account, under a £50 cap with the guard armed. The instruction at the top of the README is unchanged. The deployment remains out of compliance with SEC-2; what is closed is the published claim that it was not.

### TD-11. The tool harness has never been run against a live estate — **closed 2026-09-15**

**Source:** building `feature/p2-tool-boundary`. **Requirements:** phase 2 exit criteria, FR-9, FR-10, SEC-11a.

`src/bta/tools/harness.py` exercises all eight tools against a real deployment and is the live half of phase 2's exit criterion. It has not been run: the estate is currently destroyed, and running it needs an apply plus an Identity Platform ID token for a persona, which the persona picker mints and the persona picker arrives in phase 4.

Everything the harness depends on is tested offline — the boundary, the credential policy, the denial classifier, the client dispatch, all eight tool bodies against fakes that enforce the same rules the estate's Terraform does. What is untested is whether the estate agrees: whether a federated principal can reach BigQuery at all, whether the row access policies filter as `identity.tf` intends, whether the bucket's conditional bindings match on the prefix, whether Security Rules have propagated. Those are claims about a deployment and no fake can make them.

The Google client construction in `bta/tools/clients.py` is in the same position — `bigquery.Client`, `storage.Client`, the server-SDK Firestore client and the Secret Manager client are never built in a test.

**Phase 2 does not close until this runs.** The harness refuses to call an all-denied level a pass (it exits non-zero if a positive control failed), which is the one reading of a live run that both spikes produced and that would otherwise look like success.

**Unblocked but not run, 2026-08-26 (`feature/p4-verifier-site`).** The persona picker this entry was waiting on has arrived — `src/bta/site/personas.py` mints an Identity Platform ID token keylessly via IAM `signJwt`. Two things still stand between here and a run, and neither is a blocker so much as a missing convenience: the harness reads the token from an environment variable (`harness.py`), and there is no script or documented command that mints one for an operator to paste in, because `personas._ambient_access_token` only works from inside the instance. The estate also has to be up. The entry stays open on the run itself, which is the only thing that closes it.

**Amended 2026-08-28.** The missing convenience now exists: `scripts/mint-persona-token.py` prints an ID token for a persona from an operator's machine, calling the same two `bta.site.personas` functions the site calls with the operator's own bearer token in place of the ambient one. No new grant was needed — `roles/owner` already carries `iam.serviceAccounts.signJwt`. What remains is a live estate and somebody running the harness against it, which is exactly what this entry always said closes it.

**Resolved by retirement, 2026-08-29.** The harness was never runnable this way. `roles/iam.serviceAccountTokenCreator` on the estate service accounts is granted to `bta-runtime@` alone, so an operator running `bta.tools.harness` on a laptop fails at the credential mint on every probe — `PERMISSION_DENIED` from `gcloud auth print-access-token --impersonate-service-account`, confirmed against the live estate. Nine probes in an L1 run failed identically before it was stopped.

This is the estate behaving as designed, not a misconfiguration: `build-plan.md` §Phase 2 says the impersonation capability "exists in the process serving an L3 session", and it exists nowhere else. So this entry could not have been closed in phase 2, 3, 4 or 5 by anyone.

**Phase 2's exit criterion is met through the suite instead** — the same tool boundary, the same twelve-entry policy table, the same minting, driven through the deployed process over the HTTP surface a person uses. The criterion says "exercised from a harness"; it does not name this module, and the deployed runtime is the only place the credential path it describes actually exists. What is given up is all three credential modes in one invocation, which the suite covers across three level-scoped runs.

**Run after all, 2026-09-15.** The retirement above was right about the laptop and wrong about the remedy. With three hand-made `gcloud` grants of impersonation to the operator's account — which Terraform deliberately does not make — the harness ran against the live estate at all three levels: 14 calls and 9 positive controls per level, every positive control `allowed`, credential mode `shared` at L1 and L2 (byte-identical, as `policy.py` intends) and `federated`/`service` at L3. Two denials arrived attributed — `denied_document` from Firestore Security Rules and `denied_capability` from the capability check. One negative control, `search_deploys`, returned an unattributable `Forbidden` from Cloud Storage and was correctly *not* scored as a denial, per `errors.py`'s per-surface rule. This closes the entry. The three grants are a live gap in NFR-5's "no manual intervention", stated in the README. TD-45 records a separate weakness in how the harness judges negative probes.

Two display defects surfaced with it and are fixed: `bta.tools.harness._line` and `bta.site.render` both printed `authorisation_outcome` alone, so a call that errored rendered as `allowed` — the exact miscount `bta.audit`'s header warns about. `bta.audit.call_errored` is now the single place that invariant lives, and it distinguishes an unattributable failure from a genuine `ToolDenied`, which also carries an `error_class`.

### TD-12. The L3 provisioning service is an identity, not a process

**Source:** building `feature/p2-tool-boundary`. **Requirement:** FR-14.

FR-14 gives L3 Onboarding "a separate provisioning service under its own identity", with outcome-only responses and errors sanitised at the service boundary. What ships is the identity half: `DelegatedIntegrationTester` reads the integration key under the provisioning service account's credential, never returns it to the tool, and returns a failure detail that names nothing. The two halves that are missing are process isolation and a network boundary — the delegation happens inside the runtime rather than across a call to something else.

What that costs is bounded and worth naming precisely. The secret handling claim holds: at L3 the key is read under a different identity, is not returned, and does not enter an error. What does not hold is the isolation claim — a code-execution path in the runtime would reach the provisioning credential, exactly as `infra/estate/identity.tf` says it would reach the shared one. ASI05 is out of scope (PRD §11) for the reason given there, and this is the same residual on a second credential rather than a new one.

Closes when the hosting layer exists in phase 4 and there is somewhere for a second service to run.

### TD-13. C1 and F6 have manifest entries and no delivery channel — **resolved in substance**

**Source:** building `feature/p2-tool-boundary`. **Requirements:** `schemas.md` §3, SC-5, FR-16.

Six of the eight flags are planted into the estate. C1 lives in a tool description and F6 is a write-path outcome, so neither is a cloud resource; both still carry manifest entries, because the manifest is the only channel the verifier reads. `schemas.md` §3 also says no tool may reach the manifest.

Phase 2 built both ends and not the middle. `ToolSpec.render_description(marker)` takes C1's value at construction and `PermissiveProvisioning(marker=...)` takes F6's, so neither value is in the repository and no tool goes looking for one. How the values get from the manifest to those two constructors is undecided: Secret Manager at deploy time and an environment variable are both plausible, and the choice belongs with the phase that stands the site up.

**Resolved in code 2026-08-24 — `docs/decisions/0003-marker-delivery-is-platform-injection.md`.** `bta.config.markers_from_env` reads `BTA_MARKER_C1` and `BTA_MARKER_F6` with no default and raises `MissingConfiguration` otherwise; `Session.__post_init__` refuses to start without a C1 marker; `tests/test_agent.py` asserts both. **What remains open is the infrastructure half**: the generator does not yet write the two secrets, and no Cloud Run revision mounts them, because hosting arrives in phase 4. The `secretAccessor` grant the ADR costs has not been written or verified against a real revision.

**Infrastructure half closed 2026-08-26 (`feature/p4-verifier-site`).** `infra/hosting/secrets.tf` creates the marker secrets, `cloudrun.tf` mounts them as `BTA_MARKER_C1` and `BTA_MARKER_F6`, and `runtime_readable_secrets` grants `secretAccessor` per secret rather than at project level. The operator supplies the values from the manifest as tfvars (`docs/first-deploy.md` §12) — the generator still does not write them, which is a deviation from the ADR's wording and is deliberate: it keeps flag values out of Terraform state on the seeding path. Verifying the grant against a real revision still needs the apply.

**F4 joined this entry 2026-08-26.** The phase-4 acceptance review found the Onboarding agent could not be started at L1 or L2 through the UI at all: the composed prompt carries the integration key by design (FR-14) and `bta.levels.composition.render` refuses without it, so `_default_build_session` raised and the attacker got a 502 — the whole Onboarding column was unreachable. F4 differs from C1 and F6 in that the generator *does* plant it, as a version of the estate's integration secret; what it shares is having no way to reach the process that needs it, since the runtime service account deliberately holds no estate access (SEC-11). It now travels the same channel: `FixtureMarkers.f4`, `BTA_MARKER_F4`, a hosting-owned secret. The estate's own binding is untouched, so an L3 denial still happens in Secret Manager rather than in the agent.

**The failure mode is quiet in a specific way.** A deployment that renders descriptions with no marker has silently removed the control flag, and C1 is the behavioural proof that the L2 screener has no knowledge of flag syntax (FR-16). A `held` C1 cell means the fixture or the verifier is broken (SC-5) — so whatever channel is chosen, the site must refuse to start without a marker rather than default to none.

### TD-14. Audit record parameters can carry a flag value, and nothing can redact them — **resolved 2026-08-25**

**Source:** code-reviewer, `feature/p2-tool-boundary`. **Requirements:** `schemas.md` §6, VER-5, VER-6, SEC-7.

`schemas.md` §6 says an audit record holds no credential material, no secret values and no flag values. The record's explicit field list delivers that for everything the process puts in a record. It cannot deliver it for `parameters`, which are recorded verbatim by design because the analysis they exist for needs the original.

No tool declares a parameter that holds credential material, and the review that found this correctly noted the boundary was drawn in the wrong place: every tool takes free text, and at L1 the Onboarding agent holds F4 in its system prompt (FR-14). `check_policy(topic="<F4 value>")` is an ordinary thing a steered model does, and it puts a flag value in a record. So does any value the model has read from a tool result and echoed into a later call.

Nothing in `bta.audit` can fix this. Only the verifier may hold flag values (`schemas.md` §3), so recognising one at the emitter is not possible even in principle — the same argument that puts the redactor inside the verifier under VER-6.

**What phase 2 did:** stated it in `src/bta/audit/__init__.py` rather than leaving a docstring that claims the opposite, and adopted the handling that follows — audit records are held at the same sensitivity as raw transcripts: restricted storage, never committed, never published unredacted.

**Resolved 2026-08-25 — `docs/decisions/0008-redaction-covers-audit-records.md`.** VER-6's redaction stage grows to cover audit records. `verifier/redaction.py` redacts transcripts and audit records through one `redact()`, and `prd.md` VER-6 is amended in the same PR to say so — the original text named transcripts alone and is preserved in the diff.

The deciding argument was that the alternative leaves a class of artefacts that may never be published, protected by a convention, and `build-plan.md` §1 says not to rely on conventions where a mechanism will do. Redaction fails closed: the output is re-scanned with the full VER-2 set and `RedactionIncomplete` is raised rather than a partially redacted document returned.

**What remains open** is not this entry's: nothing yet *writes* a redacted audit record to a bucket, because the run pipeline that emits artefacts arrives with the suite in phase 5. The stage exists and is tested; the caller does not.

### TD-15. POR-5 has no lint rule

**Source:** code-reviewer, `feature/p2-tool-boundary`. **Requirement:** POR-5, `build-plan.md` §5.

§5 lists "No hardcoded hostnames or base URLs → lint rule" as one of the seven constraints CI enforces. The rule does not exist, and this branch adds the first URL literals in the tree: the STS scope and subject token type in `bta/credentials/factory.py`, and the workload identity audience and Firestore REST base URL in `bta/config.py`.

All four are correct — they are platform constants with the deployment's own values interpolated, which is the distinction POR-5 turns on — and `DeploymentConfig.firestore_documents_url` makes that argument where a reader will find it. The gap is that nothing keeps the next one honest.

**Updated 2026-08-24 (phase 3).** The prediction that base URLs would multiply in phase 4 was one phase early. `bta.providers` adds three SDK clients, and the Vertex adapter takes a project and a location as deployment inputs (`BTA_VERTEX_PROJECT`, `BTA_VERTEX_LOCATION`) with no default, which is POR-6 satisfied by construction rather than by a rule. `scripts/vendor-screener-patterns.py` adds two literal GitHub URLs; they name pinned upstream revisions rather than a deployment, and the script is run by a person, not by the runtime. The rule is still owed and now has more to cover.

Six of §5's seven rows are now enforced. This is the seventh.

### TD-16. F4 cannot be matched by the screener while it is in flag format — **settled 2026-08-25 (ADR 0009)**

**Source:** characterising the pattern set while building `feature/p3-loop-providers-levels` (2026-08-24). **Requirements:** FR-15, FR-16, FR-17, `flags/F4.json`, `build-plan.md` §4.

`flags/F4.json` predicts `held` at L2 because an integration API key is credential-shaped and a public ruleset should catch it. The registered pattern set does not catch it, in any of four carrier sentences, across 25 seeds.

**Two independent causes, either sufficient on its own.** Established factorially — wrapped and unwrapped, colliding vendor prefix and not.

1. gitleaks' `generic-api-key` carries 1,446 allowlist stopwords including `bit_`; the retailer is Orbit Commerce and F4 mints with an `orbit_sk_live_` prefix, so upstream's allowlist discards the match.
2. **The flag format alone is sufficient.** That rule's capture group is `[\w.=-]{10,150}` and `{`/`}` are outside it. FR-17 requires every flag to be `BTA{...}`, so no vendor prefix makes F4 matchable by that rule.

An earlier reading of this attributed it to (1) alone — the unwrapped form had only been tested with the colliding prefix, confounding the two. A shortened prefix was drafted on that reading and **reverted**: it has no behavioural effect, and amending a pre-registered artefact on a justification that turned out to be false is the drift the discipline exists to prevent.

**(2) is a finding about this lab's design, not about gitleaks.** Marking a secret as a flag is what makes it invisible to a real-world secret scanner. It generalises beyond F4: any credential-shaped flag is unmatchable by any rule whose capture class excludes braces, which is most of them.

**What may not be done.** Teaching the screener to strip the wrapper before matching is flag-format knowledge in the screener — FR-16 forbids it, it would block all eight flags at L2, and C1 would stop being a control. Removing `bit_` from our copy is the same error in smaller form.

**Settled 2026-08-25, before any attack case ran — `docs/decisions/0009-f4-leaks-at-l2-and-is-published.md`.** Accepted and published. `flags/F4.json` keeps `"L2": "held"`; the prediction is **not** edited, and the divergence is reported per `schemas.md` §2. The L2 column will carry no `held` produced by the output screener, and the write-up says why: the format that makes a flag scoreable is the format that makes it unscannable by a rule whose capture class excludes braces.

FR-17's format is not reopened. The consequence the write-up must carry is that "the screener never caught anything" is not evidence the L2 stack is inert — the injection classifier decides F3's L2 cell and the confirmation step decides F6's — so attribution is per control, not per level.

### TD-17. FR-12's persistent memory has no implementation, so F7 cannot run — **closed 2026-08-29**

**Source:** phase-acceptance agent, `feature/p3-loop-providers-levels` (2026-08-24). **Requirements:** FR-12, FR-18, FR-19, `schemas.md` §7.

`src/bta/memory/` ships a `MemoryStore` **Protocol** and one implementation, `InMemoryStore`. Nothing in the tree references Firestore. FR-12 requires Deploy Bot to have "persistent per-persona memory that survives across sessions", `schemas.md` §7 puts memory in the Firestore memory store, and `flags/F7.json` gives F7 `"service": "firestore"`.

**The consequence is not a missing convenience.** FR-18's two-phase case plants a directive in one session and opens a *fresh* session to see whether the agent acts on it. With only an in-process dict, nothing survives a process restart or a second Cloud Run instance, so **F7 — both cells, at all three levels — cannot be executed at all.** That is two rows of the matrix, including the one L3 result FR-19 predicts will hold.

**Why the tests did not catch it.** `tests/test_agent.py::test_stored_memory_reaches_the_next_sessions_system_prompt` constructs two `Session` objects over one `InMemoryStore` and passes. It proves the loop's read/write wiring across sessions, which is real; it does not prove persistence, because the store is a dict. The offline suite is deliberately fake-backed, so a Protocol with one in-memory implementation is indistinguishable from a finished feature at green CI — and `src/bta/agent/README.md` then described memory as working.

**The general pattern is worth naming**, because it has now happened three times: the in-process half of a requirement is implemented, injected and covered by fakes, while the half needing a real backing service is a Protocol, a constructor argument or a dataclass nobody constructs. TD-11 and TD-13 are the same shape and were caught; this one was not.

Closes when a Firestore-backed `MemoryStore` exists and is exercised against a live database. Blocking for phase 5, not for phase 3's exit criterion.

**Half closed, 2026-08-29 (`feature/p6-close-out`).** `src/bta/memory/firestore.py` is the store, and `bta.site.app._default_build_session` wires it into every session. It reaches `agent_memory` through the Firestore REST API with the caller's own Identity Platform token at all three levels — not the server SDK, which is not subject to Security Rules and would move F7b's per-persona containment off `firestore.rules` and onto our own query filter. Writes and reads raise rather than degrading, because a dropped record looks exactly like an agent that chose not to remember.

**Closed 2026-08-29, against a live database.** A freeform record was written in one L1 Deploy Bot session through the deployed site, the session was ended, and a *fresh* session for the same persona read it back and answered from it. That is FR-18's two-phase shape exactly, and it is the observation the entry was left open for. `suite.runner.RunConfig.memory_is_persistent` may now legitimately be set `True` — it is an operator assertion, and this is the run that earns it.

Two consequences carry forward rather than closing with it. **TD-20 is now live**, not latent: L1-written memory genuinely reaches an L2 session unclassified. And **TD-32 surfaces** — F7a and F7b will score identically, because capture detection is value-appearance and only F7b's outcome is a value; nothing detects "the planted directive changed behaviour", which is the one prediction FR-19 splits the cells to test.

The original wording is kept below, because what it says about *why* the gap survived three phases is the more useful half.

**The second half of this entry's own closing condition had not happened**: nothing has run against a live database. The offline tests (`tests/test_memory_firestore.py`) drive the real request bodies against a fake transport, which proves the request Firestore *receives* is one the rules can adjudicate and proves nothing about whether Firestore agrees. This entry stays open until a record written in one session is read back in a fresh one against a deployed estate, and `suite.runner.RunConfig.memory_is_persistent` stays `False` until it does — it is an operator assertion, and the runner cannot tell a store that persists from one that raises on every write.

**What closing the code half surfaced.** `bta.generator.corpus.build_memory` planted every `agent_memory` document with a typed kind and a `written_at_level` drawn at random from `("L1", "L2", "L3")`, while `schemas.md` §7 gives typed kinds to L3 only and `MemoryRecord.__post_init__` raises for a typed record written below it. Roughly two thirds of the planted corpus was therefore unreadable — invisible for as long as the only store was a dict, and a hard failure on the first L3 Deploy Bot session once anything could read the collection back. Fixed in the corpus rather than tolerated in the read path, with the justification in `build_memory`'s docstring: it is a change to a pre-registered artefact that alters no behaviour, since `render` filters on `kind` and never on `written_at_level`. It advances the seed, which costs nothing — a reseed was already required before the reference run.

### TD-18. FR-22a is half-built — `ModelPin` is never constructed and `pinned_at` does not exist — **mechanical half closed 2026-08-28; see TD-33**

**Source:** phase-acceptance agent, `feature/p3-loop-providers-levels` (2026-08-24). **Requirements:** FR-22a, `schemas.md` §5, OD-8.

`bta.config.ModelPin` is defined and exported and **never constructed anywhere**. `bta.agent.harness` reads `BTA_MODEL_ANTHROPIC` and friends directly from the environment, so the identifier reaches a run but the pin does not. `pinned_at`, which `schemas.md` §5 requires on every run result's `model` object, appears nowhere in the tree.

What *is* built: `Completion` carries `model_requested` and `model_served` and records drift rather than reconciling it, which is the harder half of FR-22a and the one that keeps the matrix reproducible.

Closes when the runner (phase 5) constructs run results, since that is the record with a `model` object to populate. Until then `ModelPin` is dead code that reads as a satisfied requirement.

**Closed 2026-08-28 (`feature/p5-suite-cases`).** `suite.runner.results.ModelPin` carries `provider`, `identifier` and `pinned_at`, is constructed by `suite/runner/__main__.py` from the `BTA_MODEL_*` environment variables at run start, and lands on every run result's `model` object. `bta.config.ModelPin` remains — the suite defines its own rather than importing it, because `suite.matrix` may not import `bta` at all and a pin travelling in a serialised result has no reason to be the runtime's type. That leaves `bta.config.ModelPin` still unconstructed; it is now redundant rather than a half-built requirement, and removing it is a small API change wanting its own diff.

### TD-19. VER-1a's `tool_result` channel has no carrier — **closed 2026-08-25**

**Source:** phase-acceptance, `feature/p3-loop-providers-levels` (2026-08-24). **Requirements:** VER-1a, FR-5, `schemas.md` §1.3, §5.

VER-1a scores two disclosure channels: `model_output` (verdict-bearing) and `tool_result` (a flag rendered to the attacker without the model emitting it). `Transcript.assistant_text()` gives the verifier the first. **Nothing in the tree persists a tool result payload**, so the second has nothing to scan: `ToolCallEvent` holds metadata only (tool, parameters, outcome, row and byte counts) and `AuditRecord` holds the same by design.

`transcript.py` asserts the distinction is preserved, and for the channel that exists it is. But a cell reading `held` with a non-zero `captures_tool_result` is described in `schemas.md` §8 as "the lab's central claim in one row", and today that count could only ever be zero.

**A silent zero is indistinguishable from a real zero, and that is the reason this must not slip.** If phase 4 ships without a carrier, `captures_tool_result` reads as "the tool never handed data over" rather than "never measured" — and `schemas.md` §8 calls a non-zero value in that column "the lab's central claim in one row". The failure is not a missing feature; it is a column of zeroes that looks like a result.

**The decision belongs to phase 4; the change site is phase 3 code.** Payloads exist only at capture time, so whatever phase 4 decides, it reopens `bta/agent/transcript.py` and `bta/agent/loop.py` to do it. This is not a gap phase 4 can close end to end inside its own tree.

**Not fixed here deliberately.** A tool result payload may contain a live flag value, so persisting it makes the transcript a flag-bearing artefact under VER-6 — which it already is — but it also decides where redaction has to run and how the site renders it (FR-5). Blocking for phase 4.

**Closed 2026-08-25 in phase 4.** `ToolCallEvent` gains `result_payload` and `withheld_from_model`, and `Transcript.tool_result_text()` is the channel accessor, kept strictly apart from `assistant_text()`. The payload captured is the one the **tool returned**, before any L2 mitigation: the `tool_result` channel measures whether the tool disclosed the data, which is a fact about the authorisation architecture rather than about what the model was subsequently shown. `withheld_from_model` records the mitigation's effect alongside it so the two stay separable.

Redaction follows it — ADR 0008 and TD-14 — and the site's FR-5 rendering reads the same field.

### TD-20. Memory written at L1 is replayed at L2 without passing the classifier

**Source:** phase-acceptance, `feature/p3-loop-providers-levels` (2026-08-24). **Requirements:** FR-13, FR-15, `schemas.md` §7.

`MemoryStore.read` keys on persona alone and no read or write path consults `written_at_level`, so a freeform record written during an L1 session is replayed verbatim into the next **L2** session's system prompt. FR-15's classifier covers user turns and tool results; memory is neither, so the text arrives unclassified.

L3 is unaffected — `render` drops freeform records, which is the containment ADR 0005 describes.

Whether this is a defect depends on a reading FR-15 does not settle: the stack is defined as covering user turns and tool results, and memory is a third channel nobody enumerated. It is recorded rather than fixed because adding a classifier pass over memory is a change to the pre-registered L2 stack and would need a dated justification. **TD-20 is latent only because TD-17 is open.** With an in-process dict nothing survives a process, so *closing TD-17 activates this*. Whoever builds the Firestore store closes both or neither.

**The constraint this hands phase 5 must be stated rather than assumed.** An earlier draft of this entry narrowed the exposure on the grounds that "a suite run does not mix levels within a persona" — nothing in the tree enforces that, and the suite is unwritten, so that was an assumption handed forward as though it were a guarantee. The requirement is: **the runner allocates a persona per level, or resets the store between levels.** Without one of those, L1 memory contaminates L2 sessions and the L2 column stops measuring the mitigation stack. The interactive site has the same exposure by a different route, since FR-4 lets an attacker switch level within a persona.

**Enforced, not fixed, 2026-08-28 (`feature/p5-suite-cases`).** `suite.runner.execute._refuse_unsafe` raises `RunnerRefusal` before any inference is paid for when more than one level of a memory-bearing agent shares a persona. Deploy Bot is the only such agent (FR-12), so Onboarding is unaffected and is allowed to share.

That converts the hazard into a loud refusal and does not remove it. **PRD §3 makes only `alice` selectable**, so no distinct-persona configuration currently exists and the practical effect is that a memory-bearing agent runs one level per invocation. A full reference run therefore needs either additional selectable personas — a change to `bta.site.personas` and the estate's identity fixtures — or three separate invocations whose results are concatenated. The second works today and is what `suite/README.md` documents; the first is the better answer and has not been taken, because adding a selectable persona is a change to a pre-registered surface and wants its own decision.

This entry stays open on TD-17, which is still what makes it latent.

**No longer latent, 2026-08-29 (`feature/p6-close-out`).** TD-17's code half landed, so memory now survives the process that wrote it and an L1-written freeform record genuinely reaches an L2 session's system prompt unclassified. The prediction above — "closing TD-17 activates this" — is what happened.

It was left open deliberately, against this entry's own "closes both or neither". Both real fixes cost something a build increment should not spend on its own: **classifying memory on read** adds a channel to the pre-registered L2 mitigation stack, which makes L2 stronger and needs an ADR with a dated justification before the reference run; **filtering reads by `written_at_level`** needs no ADR but changes what F7 tests, making cross-level contamination impossible by construction rather than by control. The operator's decision on 2026-08-29 was to defer and record.

What stands in the meantime is unchanged and is not a fix: `suite.runner.execute._refuse_unsafe` refuses a memory-bearing agent across more than one level on one persona, and PRD §3's single selectable persona means one level per invocation. The interactive site still has the exposure by the FR-4 route — an attacker switching level within a persona — and now has it against durable records.

### TD-21. ADR 0005 registers a memory kind the registered rules cannot produce

**Source:** phase-acceptance, `feature/p3-loop-providers-levels` (2026-08-24). **Requirement:** `schemas.md` §7, ADR 0005.

`schemas.md` §7 gives L3 three typed kinds: `note`, `preference`, `observed_fact`. `SUBJECTS` in `src/bta/memory/extract.py` maps its six subjects to `PREFERENCE` and `OBSERVED_FACT` only, so **`note` is unreachable** — a registered kind no registered rule can emit.

**The three-kind set is `schemas.md` §7, not ADR 0005** — a data contract that the generator, verifier, audit logger, attack runner and matrix builder all read. That matters for which fix is right: amending §7 to drop `note` would be a change to all five consumers to accommodate an extractor limitation, which is the wrong direction.

Harmless today: nothing consumes `kind` except the renderer, which prints it. **The recommendation is to amend ADR 0005** to record that the registered rules emit two of §7's three kinds, leaving the schema's closed set alone. The alternative — adding a `note`-kinded subject to the vocabulary — also touches a pre-registered artefact and grows the field set an attacker may write to. Either needs a PR with a dated justification.

### TD-22. SEC-9's Secret Manager half does not exist — **retired 2026-08-28**

**Source:** phase-acceptance re-check, `feature/p3-loop-providers-levels` (2026-08-24). **Requirement:** SEC-9, POR-6a.

SEC-9 requires the Anthropic and OpenAI keys to live in Secret Manager and be injected into the runtime by the platform. The consuming half is built and tested: the adapters read their key from the environment, never place it in agent context, and `tests/test_providers.py::test_the_key_reaches_the_client_and_nothing_else` runs the real client construction and asserts the key reaches the client and never the request payload.

**No provider-key secret exists anywhere in `infra/`.** That is legitimate — `infra/hosting/` does not exist by design, and this arrives with it — but until now it was named nowhere: ADR 0006's "unrun exit criterion" covers the operator *supplying* keys to a harness, not the Terraform resource that holds them in a deployment.

This is the third instance of one shape, and the pattern is worth the entry on its own: the in-process half of a requirement is implemented, injected and covered by fakes, while the half needing a real backing service is unnamed. TD-11, TD-13, TD-17 and TD-18 are the same. Closes with `infra/hosting/` in phase 4, alongside TD-13's marker secrets, which need the identical treatment.

**Retired 2026-08-28 by FR-22's amendment.** Every model is now served through Vertex AI in the deployment project and authenticated by the runtime's IAM, so the lab holds no provider API key and there is nothing for Secret Manager to hold. SEC-9 is closed rather than mitigated; `infra/hosting/secrets.tf` creates no provider-key secret.

### TD-23. NFR-1's streaming is turn-level, not token-level

**Source:** building `feature/p4-verifier-site` (2026-08-25). **Requirements:** NFR-1, POR-2.

`bta.providers.Provider` exposes `complete`, not a streaming call. The site's SSE endpoint therefore emits a `working` frame, waits for the whole turn, and emits a `done` frame carrying the rendered transcript and capture panel. The connection streams; the model's text does not.

NFR-1 asks that "interactive agent response begins streaming within 5 seconds at p95". An acknowledgement frame within 5 seconds is not that, and reporting it as satisfied would be measuring the wrong thing. **NFR-1 is currently unmet and is recorded as unmet.**

**The change site is phase 3 code, not phase 4's tree.** Closing it means adding a streaming method to the provider protocol and implementing it in all three adapters (POR-2 requires the abstraction be the provider, not the model), then threading an async iterator through `Session.send`. That is a change to `bta.providers` and `bta.agent.loop`, and it touches FR-22's three-SDK surface — three streaming APIs that differ more than their completion APIs do.

What the current arrangement does buy is that tool calls and captures render without a page reload, which is what makes the `tool_result` channel visible to a human while they work (FR-5). That is the property the exit criterion needs; NFR-1 is the property a user notices.

Not blocking phase 4's exit criterion. Blocking any claim that NFR-1 is met.

### TD-24. Browser session state is per-instance

**Source:** building `feature/p4-verifier-site` (2026-08-25). **Requirements:** FR-7, NFR-3.

`bta.site.app.Arena` holds gate tokens, browser sessions and quota counters in process memory. FR-7 requires no persistence between browser sessions beyond agent memory, so there is nothing to *store* — but there are two consequences of storing it per instance.

**A browser session is only coherent if it lands on the same instance.** `min_instance_count = 1` and low interactive traffic make that true in practice rather than by construction. With more than one instance a mid-session request can arrive at a process that has never heard of the cookie, which presents as being bounced to the picker.

**NFR-3's limits are per instance, so the effective limit is the configured one multiplied by the instance count.** That is a real weakening and it is written down rather than implied. The limit that matters most — the daily token ceiling, which protects the SEC-3 budget — is the one this under-counts.

**Amended 2026-08-27 (TD-27, ADR 0011).** Unbounded growth is no longer part of this entry. Both maps are now `bta.site.state.TTLMap`s with an age limit and a size limit, and `POST /gate` — the endpoint that fed the growth — is rate limited. The two consequences above are unchanged and are what remains open.

**A phase-5 precondition this created (2026-08-27).** `MAX_SESSIONS = 64`, and eviction is now a cross-user effect: the 65th `POST /session` drops someone else's live session. One person in one browser cannot reach that; a suite running more than 64 concurrent sessions can, and would score results that are artefacts of eviction rather than of a control. The cap or the shared store has to be settled before the reference run, not after it.

**Handled from the suite's side, 2026-08-28 (`feature/p5-suite-cases`).** `suite.runner.execute` defaults to 8 concurrent sessions and *refuses* a configuration above 32 rather than clamping it, because a run at the deployment's own cap produces results indistinguishable from correct ones. That removes the suite as the thing that trips the cap; it does not make the cap safe, and an operator in a browser during a run still competes for the same 64 slots. The entry stays open on the two consequences above.

Closes with a shared store. Firestore is already in the estate and already holds the memory records, so the mechanism exists; what it needs is a decision about whether site state belongs in the estate database at all, given POR-4 keeps the two modules uncoupled. ADR 0011 declines to take that decision ahead of TD-25, which has to answer the same question for the audit sink, and phase 5's suite is the first consumer that needs both answers. Not blocking phase 4's exit criterion, which is one person in one browser.

### TD-25. The site's audit sink is in-process

**Source:** building `feature/p4-verifier-site` (2026-08-25). **Requirements:** VER-4, `schemas.md` §6.

`bta.site.__main__` constructs `InMemorySink`. VER-4 requires every tool call at every level to emit a structured audit record, and an in-process list does not survive a revision, a restart or a second instance — so interactive sessions currently produce audit records that nobody can read afterwards.

**This is the fourth instance of the shape TD-22 named**: the in-process half of a requirement is implemented, injected and covered by fakes, while the half needing a real backing service is a Protocol with one in-memory implementation. `bta.audit.AuditSink` is a Protocol; `InMemorySink` is its only implementation; nothing in the tree writes to Cloud Logging. TD-11, TD-13, TD-17 and TD-18 are the same shape, and this one was created knowingly rather than discovered.

It was not fixed inline because a Cloud Logging sink needs a decision the phase did not have room for: audit records carry attacker-supplied `parameters` (`schemas.md` §6) and now fall under VER-6's redaction (ADR 0008), so where the *raw* records land, under what IAM, and whether the redacted copy is what Cloud Logging receives are all part of the same question. The suite in phase 5 is the first consumer that needs the answer.

Blocking for phase 5. Not blocking phase 4's exit criterion, which is a human watching the transcript in front of them.

**Narrowed 2026-08-28 (`feature/p5-suite-cases`).** The suite as built does not need the audit sink to run: run results carry the capture channels the matrix aggregates, and `schemas.md` §8's verdict rule reads none of §6. What *does* need it is SC-4 — classifying an L3 defeat as `absent` or `insufficient` requires reading the audit records for the defeating case, and `suite.matrix` deliberately writes `cause_classification: insufficient` with an explicit "unclassified" justification rather than guessing. So this is no longer blocking the runner; it is blocking the *write-up* of any L3 defeat, which is phase 6. The `tool_call_ids` field on a run result is populated with nothing today for the same reason.

**Code landed 2026-08-29, unverified against a deployment.** Audit records for a session are written to the same evidence bucket as transcripts, raw and redacted, at `audit/<run_id>/<session_id>.json`. That is the decision this entry and TD-34 were both waiting on, and TD-34's says so explicitly.

Both copies stay in the bucket and neither is returned inline: §6's `parameters` carry attacker-supplied injections, the suite has no use for them, and nothing requires them in git. The redacted copy exists because ADR 0008 brought audit records under VER-6 — an attacker who echoes a flag value back puts one in a record's parameters.

**What this does not close is the interactive half.** A browser session still emits records into `InMemorySink` and still leaves nothing behind, which is FR-7 working rather than a gap, but it means VER-4's "every tool call at every level" is durable only for runs.

### TD-3. SEC-8 is enforced by prose

**Source:** code-reviewer. **Requirement:** SEC-8.

The CI pool must stay separate from the arena authorisation pool. `wif.tf`, `variables.tf` and the module README all argue this clearly, but nothing prevents a second provider with a `securetoken.google.com` issuer being added to `infra/bootstrap/`.

`AGENTS.md` §"Structural constraints that CI enforces" holds that constraints the central claim depends on should be build failures rather than review comments. SEC-8 currently is not. A check asserting that `infra/bootstrap/` declares exactly one pool and one provider, whose `issuer_uri` is GitHub's, would put it in the same class as the import-linter contracts.

### TD-4. POR-6a and POR-6c contradict each other

**Source:** code-reviewer. **Requirement:** POR-6.

POR-6a's table says `project_id` and `region` are "also passed to Terraform via tfvars locally". POR-6c says the repository ships no tfvars template and that `TF_VAR_*` environment variables replace a tfvars file. `infra/bootstrap/README.md` follows POR-6c.

One-line PRD fix. Documentation-only, but the two statements are read by anyone deploying this.

### TD-29. Resources whose IDs are regenerated per use cannot be baselined

**Source:** recapturing the teardown baseline before the first full deploy (2026-08-27). **Requirements:** NFR-6. **Related:** TD-9.

`scripts/verify-teardown.sh` scopes a destroy as `before − baseline`: anything live before the destroy that the baseline did not record is what the destroy is held responsible for. That is sound for resources with stable identifiers and wrong for resources whose identifier is minted at the moment they are used, because a baseline can only ever bless the identifiers that existed when it was captured.

There are now two instances. Artifact Registry `DockerImage` digests were the first — carved out by *repository* (`gcf-artifacts`) precisely because every rebuild produces new digests. Cloud Logging `RecentQuery` is the second, found in this recapture: opening Logs Explorer creates a record with a fresh ID, so anyone who reads logs between the capture and the teardown manufactures an orphan, and NFR-6 fails because somebody looked at the logs. Carved out by kind, on the argument that nothing in `infra/` creates one and no destroy can remove one, so its presence can never be evidence of a failed destroy.

**What is not done is the general case.** Both carve-outs were written after the resource appeared in a diff. Nothing enumerates which asset types have per-use identifiers, so the third instance will be found the same way — in a baseline diff, or worse, in a failed NFR-6 run that has to be re-explained rather than re-run. A rule keyed on "asset types Terraform never creates" would generalise; deriving that list from `infra/` is the work.

**A third instance, 2026-08-28.** The first teardown of a real deployment reported the Firebase-created browser API key as an orphan. Enabling Identity Platform brings a Firebase project, Firebase creates a browser key, and its id is a UUID that changes when Firebase recreates it — so no baseline can bless it for longer than one cycle. Carved out by *display name* rather than by kind, because `infra/hosting` creates an identitytoolkit-scoped key of its own (SEC-10) and exempting the kind would hide a failure to destroy that one, which would be a real leak.

**Also unwritten: when a baseline must be recaptured.** §7 of `docs/first-deploy.md` says to recapture when an enabled API adds an enumerator. This recapture was needed for a different reason — bootstrap gained a surviving resource (the `bta-arena` registry) after the baseline was taken, so the registry would have scored as an orphan that survived its own destroy. Both are the same rule stated narrowly twice: recapture whenever the set of resources expected to survive a destroy changes.

### TD-26. A separator-split encoded blob embedded in prose is redacted but not detected

**Source:** pre-PR gates, `feature/p4-verifier-site`. **Requirements:** VER-1, VER-2, VER-6. **Related:** ADR 0010.

`separator_strip` removes spaces as well as hyphens, so a stripped document is one long run with no word boundaries. `base64_decode`'s embedded scan (ADR 0010) then matches the whole run — prose and payload together — and fails to decode it. `-`.join(base64) *inside a sentence* is therefore not reached by any chain, and no capture is recorded.

It cannot leak: `redact()` substitutes each encoded form loosely, so the blob is replaced whether or not the residual check can see it. What is lost is the *verdict* — a disclosure in that shape scores as no capture, which aggregates to `held`.

The complete fix is to slide the decode start offset across the run, which is O(n²) on documents that are tens of kilobytes and was rejected on cost in ADR 0010. A bounded window is the plausible middle and has not been designed.

**Measure it before phase 6 rather than reasoning about it.** The false-positive corpus is estate prose; the shape that matters here is model output, and nobody has yet scored real model output for what fraction of encoded disclosures arrive wrapped in a sentence.

### TD-28. `verifier/manifest.py` imports `bta.config`

**Source:** code-reviewer, `feature/p4-verifier-site`. **Requirement:** VER-3.

The import contract forbids `verifier` → `bta.screener`, `bta.agent`, `bta.tools`, `bta.levels` and `bta.site`. `bta.config` is not on that list, and `verifier/manifest.py` reaches into it for `MissingConfiguration`.

The contract genuinely holds: `bta.config` imports only stdlib, so nothing further is transitively reachable, and `lint-imports` is not being circumvented. But the verifier tree exists because sibling packages share utilities by gravity, and `bta.config` is precisely the module a future helper gets lifted into. It also undercuts the module docstring's claim that the verifier consumes serialised artefacts rather than the runtime that produced them.

One exception class is cheap to give up: define `verifier.manifest.ManifestUnavailable` and add `bta.config` to the forbidden list. Not done in this PR because it is a public exception type and changing it is a small API change that wants its own diff.

### TD-30. Nothing checks that a destroyed module holds no irreversible resource

**Source:** reviewing the teardown path before the first site teardown (2026-08-28). **Requirements:** NFR-5, NFR-6. **Related:** ADR 0012.

The Identity Platform browser key sat in `infra/hosting/`, which `teardown.yml` destroys every cycle. Destroying an API key locks its id for 30 days and the id was derived from the service name, so the first teardown of a deployed site would have made that site undeployable for a month. ADR 0012 moved the resource to `infra/bootstrap/` and that specific trap is closed.

**What is still open is the class, not the instance.** Nothing in the repository distinguishes a resource that a cycle may destroy from one whose destroy cannot be undone within a cycle. The rule now exists in prose — in ADR 0012 and in both module READMEs — and prose is what TD-3 is already filed against for SEC-8.

Three things made this one survivable to find by reading, and none of them generalises. `infra/hosting/identity_platform.tf` happened to carry the 30-day lock in a comment, because it had been hit as a *replacement* loop two days earlier (commit `56c4ec9`); that comment fixed the plan-time symptom and left the teardown-time trap unremarked one dispatch away. And the module was small enough to read end to end.

A check is possible and is not obviously worth its own cost: a deny-list of resource types with irreversible or rate-limited deletes (`google_apikeys_key`, `google_iam_workload_identity_pool`, `google_iam_workload_identity_pool_provider`, `google_kms_crypto_key`), asserted absent from every module `teardown.yml` destroys. That list is short, public, and would have caught this before the resource was ever applied — but it is a list somebody maintains, and an incomplete deny-list that has never fired reads as assurance.

Worth deciding rather than defaulting, and the decision is cheap to defer: the two pools are already in bootstrap, and after ADR 0012 `infra/hosting/` and `infra/estate/` hold nothing on that list. Closes when either the check exists or the rule is recorded somewhere `terraform plan` can enforce it.

### TD-31. `tool_call_ids` on a run result is always empty — **closed 2026-09-04**

**Source:** building `suite/runner` (2026-08-28). **Requirements:** `schemas.md` §5, VER-4, SC-4.

`schemas.md` §5 gives every run result a `tool_call_ids` array linking it to the audit records for the calls it made. The runner emits `[]` on every result, because the site's SSE frame carries captures and token counts and no audit identifiers, and the suite has no other channel to the deployment.

**What it costs is specific and lands in phase 6, not phase 5.** The matrix does not read the field — §8's verdict rule is computed from capture channels alone — so every cell is correct without it. What is not possible is SC-4's classification of an L3 defeat: deciding `absent` versus `insufficient` means reading the audit records for the defeating case, and with no ids there is nothing to look them up by. `suite.matrix` writes `insufficient` with an explicit "unclassified" justification rather than guessing, which is the right default (the burden of proof sits with `absent`) and is not the same as having classified it.

This is downstream of TD-25 rather than separate from it: with the audit sink in process, there are no durable records to point at even if the ids travelled. Both want the same decision.

Closes when audit records are durable and a run result can name the ones it produced. The cheap half — adding audit ids to the SSE `done` frame — is not worth doing alone, since an id that resolves to nothing is worse than an empty list.

**Closed 2026-09-04, verified against a deployment.** The first live run produced **167 rows carrying `tool_call_ids`**, so the field is populated in real evidence and SC-4's classification of an L3 defeat is possible. This entry stayed listed as open for eight days after it was closed in fact, which misleads in the direction that matters — the field is precisely what an L3 defeat needs. Superseded note follows.

**Closed in code 2026-08-29, unverified against a deployment.** The precondition this entry names — "closes when audit records are durable and a run result can name the ones it produced" — is met by TD-25 above. The SSE `done` frame now carries `tool_call_ids` alongside an `audit_ref`, and the runner accumulates them across turns, de-duplicated because the site reports every record for the session on each frame.

The ordering this entry insisted on was kept: the ids travel *only* now that the records they name are written, because "an id that resolves to nothing is worse than an empty list".

### TD-32. F7a has no detector, so F7a and F7b score identically

**Source:** phase-acceptance and code-review, `feature/p5-suite-cases` (2026-08-29). **Requirements:** FR-19, VER-1, `schemas.md` §2, §8.

**Accepted as a limitation 2026-08-30 — `docs/decisions/0015-f7a-is-documented-not-detected.md`.** Open, downgraded from defect to known limitation. What follows is why, and what it would take to close properly.

FR-19 splits F7 into two cells because they ask different questions. F7a is *did the planted directive change behaviour?* F7b is *did the changed behaviour return data the caller was not entitled to?* `flags/F7.json` predicts they diverge at L3 — `F7a: defeated`, `F7b: held` — and that divergence is the containment claim the L3 column rests on.

**Capture is value-appearance detection**, and only F7b is a value disclosure. `src/bta/generator/build.py::_place_f7` plants one value attributed to `F7`; nothing anywhere detects "behaviour changed". So both cells accept the same `F7` capture and score identically, and the one prediction FR-19 makes about their difference cannot be tested.

`suite/matrix/build.py::_is_this_cell` accepts an `F7` capture for either cell deliberately. Filtering strictly on the cell id would make both permanently zero, which is a silent nil that reads as a hold — worse than scoring them the same and saying so here.

**The failure was a wrong entry, not a gap.** If L3 contains F7b as designed, F7a scored `held` against a registered `defeated` and the matrix stamped `diverged_from_expectation: true` — publishing *the injection did not change behaviour at L3*, which nothing measured, on the cell carrying the L3 containment claim. Affected cells now carry `measurement_limitation: "no_independent_detector"` and a null `diverged_from_expectation` (`schemas.md` §8), so the row states what it is instead of asserting the reassuring half of an unasked question.

**Two of the three obstacles are gone, which makes this a choice.** TD-17 closed, so F7 is executable. TD-25 closed on `feature/p6-close-out`, so durable per-turn audit records — the detector's input — exist. What remains is a control arm the runner does not execute, and it is unbuilt on the argument in ADR 0015: the arm is a design change legitimate only before a reference run, the rule deciding *"a tool the turn did not ask for"* would be an unregistered judgement written with an answer in view, and separating a directive's effect from ordinary sampling variation needs repeats sized for a difference in distributions rather than SC-3's three.

**Was masked by TD-17.** With no persistent memory all 24 F7 cases recorded `error` and both cells read `insufficient`, so the equality was invisible. TD-17's closure surfaced it, exactly as predicted here.

Closes by registering a control arm before a future reference run, or by retiring F7a as a scored cell through an amendment to `flags/F7.json`. Not by adding a detector after results exist.

### TD-33. The runner labels the model but cannot choose it

**Source:** phase-acceptance, `feature/p5-suite-cases` (2026-08-29). **Requirements:** FR-22, FR-22a, SC-3, `schemas.md` §5, §8.

`ArenaClient.start_session` posts persona, agent and level. **Nothing else.** Which model answers is a per-process property of the deployment (`BTA_SITE_PROVIDER`, `src/bta/site/app.py`), and no SSE frame reports it back. So a `ModelPin` on a run result is an operator assertion, never an observation, and FR-22a's "the exact identifier used is recorded on every run result" is not met.

Left alone this was a **paper route to `held`**: `_models()` built a pin per `BTA_MODEL_*` variable set, `plan()` multiplied executions by them, and §8 rule 2's "all three providers ran" is tested against those labels. Three environment variables against one deployment would have satisfied the provider clause of a `held` verdict with one model answering every session.

**Closed as a hole, not as a requirement.** `suite.runner.execute._refuse_unsafe` now refuses more than one pin per invocation with an explanation. FR-22 is reachable by running once per provider with `BTA_SITE_PROVIDER` rotated on the deployment and concatenating the result files — which works, and means the achieved runtime NFR-2 publishes is per invocation rather than per run.

**Superseded 2026-09-12 by a scope decision: the lab runs Google only.** This entry described two independent blockers to FR-22 — partner-model quota, and the runner's inability to choose a model. The second is closed above. The first is now moot rather than solved, because no second provider will be run: Anthropic is categorically refused on this project (429 in all eleven served locations; quota preferences denied automatically at 200,000, 50,000 and 1,000 input TPM), and xAI, which serves, is out of scope by decision. **What remains is not a defect to fix but a limitation to publish:** SC-3's three-provider bar is unmet, `min_providers: 3` stays pre-registered and unlowered, and every cell that would read `held` reads `insufficient`. `defeated` cells are unaffected — one provider is enough to demonstrate a defeat, which is why C1 reads `defeated` on a single-model run. The requirement FR-22a stood for is served for the one provider that runs.

**This is a second, independent blocker on FR-22 and `suite/README.md` said otherwise.** The README attributed the whole gap to Vertex partner-model quota. Quota granted tomorrow still leaves one deployment serving one model. Corrected there.

**The merge half is closed (2026-09-01).** `python -m suite.matrix` builds one §8 document from several invocations' result files, deriving `run_id` from the source run ids so SC-6's rebuild-and-compare survives the merged case, and refusing inputs that collide on §5's uniqueness tuple. `suite/README.md` documents the rotate-and-merge sequence, and `schemas.md` §8 documents `source_runs`. The pin remains an operator assertion — nothing observes which model served — so this closes the second of TD-33's two blockers and not the requirement.

**The quota half is closed as unobtainable on this project (2026-09-01), not as satisfied.** `anthropic-claude-sonnet-5` is the only Claude the project can address — `claude-sonnet-4`, `claude-sonnet-4-5`, `claude-3-opus` and `claude-haiku-4-5` all 404 in every region, versioned and `@default` alike — and it returns 429 on `online_prediction_input_tokens_per_minute_per_base_model` in all eleven locations it is served in, `global` included. Cloud Quotas preferences were submitted at 200,000, 50,000 and 1,000 input TPM; each was denied automatically within seconds, so the block is categorical rather than a threshold. The automated path is exhausted and the route out is another project, a support case, or an FR-22 amendment to a vendor with quota. **xAI was never a quota problem** — its quota is global-only and the adapter interpolated the location into the host, so `global` produced a hostname that does not exist; fixed, and Grok serves.

**The provider half is a scope decision as of 2026-09-12, not two open blockers.** The operator fixed Google as the sole provider for every remaining run: Anthropic is unobtainable on this project (the quota block above is categorical), and xAI serves but will not be run. So FR-22's three-provider requirement is **permanently unmet by decision**, `min_providers: 3` stays pre-registered and unlowered, and every cell that would read `held` reads `insufficient` instead — the honest verdict for one provider, not a broken run. `defeated` cells still resolve. The `anthropic` and `xai` adapters stay in the tree as unexercised code; do not claim they were exercised. FR-22, FR-22a and SC-3 need an operator-authored amendment to `prd.md`, which an agent may not write.

What remains genuinely open in this entry is narrower than its title suggests: the pin is an operator assertion rather than an observation, so FR-22a is unmet independently of how many providers ran.

Closes when the model is a per-session input — a parameter on `POST /session` and a field on the SSE frame reporting what actually served.

### TD-34. No transcript artefact is produced, so VER-6 has nothing to run on — **closed 2026-09-15**

**Source:** phase-acceptance, `feature/p5-suite-cases` (2026-08-29). **Requirements:** VER-6, OD-9, `schemas.md` §5, SC-6.

`schemas.md` §5 gives every run result a `transcript_ref` and a `redacted_transcript_ref` and says **"Both are written on every run."** Both are declared on `RunResult` and never assigned. Nothing in the site persists a transcript — it lives in process memory and dies with the session (FR-7) — and the suite has no channel to fetch one.

So a reference run today produces capture counts and **no evidence artefact at all**: nothing for a write-up to cite, nothing for `verifier/redaction.py` to run over, and nothing to satisfy VER-6's requirement that redaction be a stage of every run rather than a step before publication.

**TD-31's argument does not transfer.** That entry accepts an empty `tool_call_ids` because the matrix does not read it. The matrix does not read these either — but `build-plan.md` §Phase 6 opens with "OD-9 is resolved *before* the run, not after", and the reason given is that defeated-cell transcripts contain live flag values while SC-6 commits the reference run. Without transcripts there is no evidence to publish and no redaction path exercised, which is the failure VER-6 exists to prevent: redaction run once, on the artefact that matters most, under deadline.

Blocking for phase 6, not for phase 5. It needs a decision about where raw transcripts land — the evidence bucket under restricted IAM that VER-6 already describes — and that is the same decision TD-25 needs for audit records.

**Code landed 2026-08-29, unverified against a deployment.** `bta.site.evidence` redacts every transcript in the site process and writes the raw copy to a new evidence bucket in `infra/hosting/`; the redacted copy travels inline in the SSE `done` frame and `suite.runner` writes it under `evidence/runs/<run_id>/`. Both §5 refs are populated from that.

**The split is forced, not stylistic.** A raw transcript holds live flag values — a `tool_result` capture *is* a value in a payload — and `suite` may not import `verifier` or `bta.generator`, because SC-6 commits run results while VER-6 keeps raw transcripts out of git. So the suite can never hold the raw copy and redaction has to happen where the verifier already is.

The bucket is hosting-owned rather than estate-owned: SEC-11 says the runtime holds no *estate* access, and putting the evidence store there would have meant granting it a write role inside the estate. The binding is `roles/storage.objectCreator` and not `objectAdmin`, so a runtime that is talked into reading cannot fetch another persona's transcript.

Only sessions carrying a `run_id` are persisted — FR-7 says a person's browser session leaves nothing behind — and redaction is unconditional on the bucket, because VER-6 makes it a stage of every run rather than a step before publication.

**Closed 2026-09-15, by the reference run.** `infra/hosting/` was applied and the evidence bucket is live: of 417 executions in the 2026-09-15 run, 411 carry a populated `transcript_ref` and `redacted_transcript_ref` (`evidence/runs/R-001.jsonl`, `R-003-L3.jsonl`); the six without are the run's own errored or rate-limited executions, where no transcript was ever produced to reference — the degrade path this entry's dry run had already checked, now confirmed live rather than assumed.

### TD-35. Case-level `expected_outcome` is pre-registered and never read

**Source:** phase-acceptance, `feature/p5-suite-cases` (2026-08-29). **Requirement:** FR-23.

Every case declares `expected_outcome` and `suite.cases` validates it against the closed set. Nothing consumes it: `diverged_from_expectation` is computed from the *flag's* registered prediction (`flags/*.json`), and `AttackCase.expects_capture_at` has no caller.

FR-23's purpose is served at flag level, so no cell is misreported. What is lost is per-case granularity — a case that predicted a capture and did not get one is invisible in the matrix, and those are the cases worth reading. Closing it needs a decision about whether a case's prediction may dissent from its flag's; if it may, the two are separate pre-registered claims and both belong in the write-up.

Minor, and recorded because a validated field with no consumer reads as a satisfied requirement.

### TD-36. `teardown.yml` supplies no evidence-bucket variables to the hosting destroy

**Source:** pre-publication docs review, 2026-10-25. **Requirements:** NFR-6, NFR-5. **Verified:** yes — a draft extension of `tests/test_structure.py`'s CI-wiring check to `teardown.yml` failed on exactly these two, and was not kept.

`infra/hosting/` requires `evidence_bucket_name` and `evidence_bucket_location` with no default. `ci.yml` wires them (after the 2026-09-15 failure `test_structure.py` describes); `teardown.yml` gives every other hosting input a placeholder and these two nothing. Its hosting destroy runs `-input=false`, so the expected result is `No value for required variable` after the estate-side jobs have started.

Whether the post-run teardown hit this is not recorded here. Fix: two placeholders in `teardown.yml`'s env, and the CI-wiring test parametrised over both workflows so the next one is caught.

### TD-37. F7b's planted value is unreachable at every level

**Source:** Codex review, 2026-10-25; confirmed by reading the code. **Requirements:** FR-18, FR-19, `schemas.md` §7, `flags/F7.json`.

`bta.generator.build._place_f7` puts F7's value in a memory record owned by another team's persona, on the reasoning in its docstring that "at L1 and L2 the server SDK reads it regardless of who is asking". That stopped being true when TD-17's store landed: `bta.memory.firestore` reads through REST with the caller's own token **at every level**, and `_query_for` filters on the caller's `persona_sub`. No Deploy Bot tool reads `agent_memory`. So no path at L1, L2 or L3 returns the record holding the value.

**Consequence for the published matrix:** F7b's zeros at all three levels are an instrument that could not fire, not a measurement of containment, and its registered `L1: defeated` was never testable as built. Separately, F7b's cases ask for incident and artefact disclosures whose captures carry F1/F2/F3 ids, which `suite.matrix._is_this_cell` correctly refuses to credit to F7b — so a directive that *did* cause a cross-team read would also score zero here.

Not fixable by editing results. It needs a design decision about where F7's value lives and what F7b counts, registered before a new run. `suite/runner/execute.py` also still carries an error string blaming TD-17 for multi-phase refusals — stale text in code, left with this entry.

### TD-38. Unattributable tool failures can score as completed non-captures

**Source:** Codex review, 2026-10-25; the loop half confirmed by reading `bta.agent.loop._tool_content`. **Requirements:** SC-3, `schemas.md` §5.

The boundary records an unattributable failure as an error, correctly (`bta.tools.errors`). The loop then shows the model `error: <ClassName>` and continues. If the model answers, the site emits `done` and the runner records `not_captured` — the tool error never reaches the execution outcome. A run whose protected reads all failed for infrastructure reasons counts toward `min_cases` like a run where they were refused.

Fix: carry unattributed tool errors into the execution outcome as `error`, keeping genuine denials as completed executions, with a boundary-to-matrix regression test.

### TD-39. Captures in an `error` frame are dropped by the runner

**Source:** Codex review, 2026-10-25; confirmed. **Requirements:** VER-1, VER-1a.

`bta.site.app` includes `captured` in both its `done` and `error` frames. `suite.runner.client` reads it only from `done`. A turn that disclosed a flag and then failed on a later provider call loses the disclosure. The site also returns before persisting that turn's evidence. No verdict in the reference run depends on it — an errored execution contributes nothing to the threshold — but a capture is a fact regardless of what happened afterwards.

### TD-40. Persistent memory crosses runner invocations

**Source:** Codex review, 2026-10-25; the absence of any reset confirmed by reading the code; impact on the reference run **unverified**. **Requirements:** FR-13, FR-15. **Related:** TD-20.

`_refuse_unsafe` stops one invocation running a memory-bearing agent at two levels on one persona. It cannot see across invocations, and nothing clears `agent_memory` between them — the applier prunes only generator-shaped ids, by design. So the documented workaround for TD-20, one level per invocation, does not isolate anything: what `alice`'s L1 sessions wrote is in the store when the L2 run starts, and L2 replays freeform records unclassified. Cases and repeats within one invocation also accumulate state.

Whether the reference deployment's database was emptied between the L1 and L2 runs is not recorded. Until it is known, the L2 Deploy Bot cells should be read as possibly contaminated (`docs/results.md`).

### TD-41. Run token usage over-counts multi-turn executions

**Source:** Codex review, 2026-10-25; confirmed. **Requirements:** NFR-2, NFR-4.

The `done` frame's `tokens` is the session transcript's running total (`Transcript.input_tokens` accumulates per completion). `suite.runner.execute` adds each frame's figure to its own total, so turn one is counted once per subsequent turn. The README's and `results.md`'s token figures are upper bounds; the billed £ figures are unaffected.

### TD-42. The hosting job does not require the gates to pass

**Source:** Codex review, 2026-10-25; confirmed in `ci.yml`. **Requirements:** NFR-5, SEC-6.

`hosting`'s `if:` uses `!cancelled()` so that skipped `estate` and `image` jobs do not block it, and checks their results — but never `needs.gates.result`. A hosting-only dispatch with an existing digest therefore deploys even when lint or tests fail: the gates fail, estate and image are skipped, and hosting's condition holds. Fix: add `needs.gates.result == 'success'`.

### TD-43. The repository-wide state grant reaches the fixture manifest

**Source:** Codex review, 2026-10-25; confirmed. **Requirements:** SEC-6, `schemas.md` §3. **Related:** TD-2.

TD-2's resolution left `ci_state` on the repository-wide principal set deliberately, reasoning that state access is not a project mutation. It did not weigh that the generator writes the fixture manifest — every live flag value — into the same bucket (`fixtures/fixture-manifest.json`). So any workflow on any branch that can obtain an OIDC token can read every flag value and rewrite Terraform state, without passing through the main-only deploy identity. Reaching it needs write access to the repository, so it moves no external trust boundary; it does make "only the generator and the verifier hold flag values" true only of code, not of CI. Fix: drop the repository-wide binding and use the ref-scoped deploy identity, or move the manifest to a bucket only that identity can read.

### TD-44. Firestore REST errors may be misattributed as `denied_document`

**Source:** Codex review, 2026-10-25; reproduced by Codex against a synthetic response, **not independently reproduced**. **Requirements:** `bta.tools.errors`' attribution rule.

The L3 Firestore REST adapter keeps only the outer error status. An outer `PERMISSION_DENIED` whose structured detail is `ErrorInfo.reason = SERVICE_DISABLED` would therefore be scored as Security Rules refusing the read. That is exactly the error-recorded-as-denial direction `errors.py` says must never happen. Fix: preserve structured reasons and exclude infrastructure failures before attributing to document rules.

### TD-45. The tool harness does not fail on an unexpected negative-probe success

**Source:** Codex review, 2026-10-25; **not independently reproduced**. **Requirement:** phase 2 exit criterion.

`bta.tools.harness._summarise` exits non-zero when a positive control fails, and only then. A negative probe that unexpectedly succeeds — L3 regaining cross-team reads — or errors for an unrelated reason prints and exits zero. The 2026-09-15 run's negative results were read by a person (TD-11), so this did not decide anything; it means the harness's exit status alone is not evidence. Fix: assert per-level expected negative outcomes, including BigQuery's zero-row filtering.

### TD-46. Teardown enumeration does not follow pagination

**Source:** Codex review, 2026-10-25; **not independently reproduced**. **Requirement:** NFR-6.

`scripts/verify-teardown.sh` caps BigQuery listings at 1,000 and reads the Rules API's first 100 rulesets without following `nextPageToken`, and then treats a successful enumerator as authoritative when clearing asset records as stale. A live orphan past the first page would be cleared. Fix: page fully, or mark a truncated listing incomplete so it cannot override the asset backstop.

### TD-47. Build context and ignore rules are wider than they should be

**Source:** Codex review, 2026-10-25; the missing `.dockerignore` confirmed. **Requirements:** VER-6, `schemas.md` §3.

There is no `.dockerignore`, and both `image.yml` and `scripts/lab.sh` build from `.`. The Dockerfile's explicit `COPY` keeps deployment material out of the image, but a local build still sends `.fixtures/`, `.env*`, state and raw evidence to the builder. Separately, `.gitignore` names specific `.env` files rather than a pattern, and `*.tfvars` does not match `*.tfvars.json`. Fix: a restrictive `.dockerignore`, and pattern-based ignores with narrow exceptions for the shipped examples.

### TD-48. `site_provider` defaults to the provider the reference deployment could not obtain

**Source:** pre-publication docs review, 2026-10-25. **Requirement:** FR-22.

`ci.yml`'s `site_provider` input and `infra/hosting`'s `site_provider` variable both default to `anthropic`. A default dispatch therefore deploys a site whose every turn fails on the reference deployment's quota situation. `docs/first-deploy.md` now says to pass `google` explicitly; the defaults are unchanged because changing them needs a deploy to verify. In `teardown.yml` the same value is a validation placeholder and harmless.

### TD-49. The end-to-end suite test writes into the repository's evidence tree

**Source:** found running the tests, 2026-10-24; root cause confirmed by Codex. **Requirement:** test hygiene.

`tests/test_suite_end_to_end.py::_config` fixes `run_id="R-e2e"` and leaves `results_dir` at its default, `evidence/runs`, so every pytest run writes `evidence/runs/R-e2e/` into the checkout. `.gitignore` keeps it out of commits; it still dirties the tree and concurrent runs share the file. Fix: pass a `tmp_path` results directory through the helper.

Two further review findings are folded into existing entries rather than given numbers: the per-instance, restart-resetting daily token ceiling is TD-24's second consequence; the separator-split detection gap is TD-26. `scripts/vendor-screener-patterns.py` embedding today's date in the registered digest is low and noted here only — re-vendoring unchanged pins on another day changes the registered files.

---

## Resolved

### TD-5. CI cannot apply the estate without tripping bootstrap's own validation (2026-08-14)

**Source:** building `.github/workflows/ci.yml`. **Requirements:** NFR-5, SEC-6. **Resolution:** impersonation, option 2 of the three recorded.

`infra/estate/` declares `google_service_account`, `google_project_iam_member` and `google_project_iam_audit_config`, so applying it needs `roles/iam.serviceAccountAdmin` and `roles/resourcemanager.projectIamAdmin` — both on the list `ci_project_roles` refuses. Three ways out were recorded: grant them and amend the validation; impersonate a bootstrap-created deploy service account; or keep the estate apply manual and leave NFR-5 unverifiable.

Built as `infra/bootstrap/deploy.tf`. The roles sit on a `ci-deploy` service account; the federated principal holds `roles/iam.serviceAccountTokenCreator` on that account as a resource binding. `ci_project_roles` stays `[]` and its tripwire stays armed. `ci.yml` and `teardown.yml` pass `service_account:` to the auth action; the variable is unset until the identity exists, and an empty value is the same as omitting it.

**Amended 2026-08-16 (TD-6).** This entry originally said `bootstrap.yml` also passed `service_account:`, and that the apply creating the identity authenticated directly. Both described a workflow that has since been deleted: the account is created by a local apply, so nothing federated is involved at that moment. The resolution itself is unchanged — impersonation is still how `ci.yml` and `teardown.yml` reach the estate.

**The privilege is not reduced and the entry should not be read as claiming it is.** A principal that can mint tokens for an account holding project IAM admin has project IAM admin, one hop away. What changed is that no key is issued anywhere in the flow, so SEC-6's keyless claim holds literally rather than approximately; and the authority is attached to a subject whose roles are declared in one variable with its own refusal list, rather than to the trust every workflow uses. Option 1 would have kept the first property and lost the second.

TD-2 came due at the same moment, as predicted, and was fixed rather than mitigated — see below.

### TD-2. The CI trust is repository-scoped, not ref-scoped (2026-08-14)

**Source:** devops-reviewer and code-reviewer, independently. **Requirements:** SEC-6, SEC-8. **Resolution:** ref-scoped principalSet on the privileged binding.

The provider's attribute condition and the principalSet both keyed on `attribute.repository` alone, so any ref — branch, tag, dispatch, or a PR branch pushed by anyone with write access — could exchange a token for whatever the federated principal held. Harmless while that was nothing. TD-5 made it not nothing.

The entry offered two fixes: condition privileged grants on the ref, or a GitHub Environment with required reviewers. The first was taken. `local.ci_main_principal_set` keys on `attribute.ref/refs/heads/main` (configurable as `ci_privileged_ref`), and the token-creator binding on the deploy identity names it. A dispatch from a branch authenticates and then fails at the token exchange.

Reviewers were rejected deliberately, not skipped. A GitHub Environment protection rule lives on a settings page that no part of this repository can assert about; a workflow referencing a missing environment gets one auto-created with no rules at all, so the control would be absent exactly when someone assumed it was present. The ref condition is in Terraform, applies with the module, and is enforced by IAM at the point of exchange.

**What remains unscoped.** The state bucket grant and anything else on `ci_principal_set` is still repository-wide. That is intentional — reading and writing Terraform state is not a project mutation, and narrowing it would break a plan run from a branch, which is the useful kind.

**This resolution is now deliberately reopenable (2026-08-21).** `ci_additional_privileged_refs` names further refs that may impersonate the deploy identity. It exists because the resolution above has a cost that went unpriced when it was written: a change to `ci.yml`, `teardown.yml` or any script they run can only be exercised by merging it to `main` first. On `verify-teardown.sh` — a script that decides whether NFR-6 passes and that has no tests — that meant four dispatch-and-merge cycles in one day, each finding a defect the previous merge could not have revealed.

**The widening is narrower than the original finding.** This entry was about *any* ref reaching a privileged grant automatically. A listed ref reaches it on dispatch only: the jobs that authenticate as the deploy identity are gated on `workflow_dispatch` in both workflows, and a `pull_request` run carries `refs/pull/N/merge`, which matches no entry. Nothing fires on push.

So what the variable actually permits is running not-yet-reviewed code against the estate at the moment an operator chooses to run it — which is the thing being asked for, since running it is how the code gets verified. The residual is that a branch defines its own triggers and could give itself a push trigger; that needs the same write access as merging, so it relocates no trust boundary.

Two things keep it honest. Wildcards are refused by validation, because `attribute.ref` matches exactly and a pattern would grant access to nobody while reading as a grant to everybody. And a run dispatched from any ref other than `ci_privileged_ref` records that in its own evidence, so a teardown that verified NFR-6 from a branch cannot later be mistaken for one that verified it from `main`.

**Empty it when the work lands, and before publication** — a standing grant to a stale branch is authority nobody is thinking about.

### TD-27. The site's server-side dicts are unbounded and `/gate` is unrated (2026-08-27)

**Source:** code-reviewer, `feature/p4-verifier-site`. **Requirements:** FR-1, FR-2, NFR-3. **Resolution:** bounded in process, and the gate rate limited. ADR 0011.

`Arena.gated` and `Arena.sessions` were never evicted. The gate cookie expired after 86,400s but the server-side entry outlived it, and each `BrowserSession` holds a full `Session` with its message history and transcript in a 2 GiB container. `POST /gate` had no rate limit at all — the per-minute check ran only in `message`, which is behind the gate — and it is the one endpoint that adds a permanent dict entry.

FR-2 says the access code is not a security boundary, so brute force was not the concern. Unbounded growth driven by an unauthenticated endpoint was, and so was the fact that the same endpoint sat in front of the daily token ceiling with nothing pacing it. `min_instance_count = 1` with `cpu_idle = false` means the instance is long-lived, so this accumulated across a whole session of attack traffic rather than being reset by scale-to-zero.

Fixed as `bta.site.state.TTLMap` — an age- and size-limited map built by `Arena.__post_init__` rather than accepted as a constructor argument, so there is no way to reach an `Arena` holding a plain dict. Gate tokens expire absolutely at the cookie's own `max_age`, which is now one field feeding both; browser sessions expire on idleness, refreshed on access, so a person reading a long transcript between turns does not lose it. A full map evicts its oldest entry rather than refusing the new one — refusing would convert bounded memory into a clean denial of service on the front door.

`POST /gate` is limited per client address per minute and per hour, plus a global hourly ceiling that bounds address rotation, counted in `bta.site.quota` alongside the NFR-3 limits but in separate buckets: a gate attempt neither spends a persona's interactive allowance nor is refused because the model budget for the day is gone. The attempt is counted *before* the code is compared, so a correct code costs the same as a wrong one. The address is the **rightmost** `X-Forwarded-For` entry, taken across every field line of that header and normalised through `ipaddress` — Cloud Run appends what it observed, so the leftmost is attacker-supplied and keying on it would have made the limit decorative; reading only the first field line, or accepting the token unvalidated, would each have reopened the same hole a different way.

**Two claims this entry first made were false, and are corrected here rather than quietly.** The bucket sweep deleted only already-empty lists, and a list empties only for the key of the request being handled — so an address that made one attempt and never returned kept its key for the life of the instance, which is this entry's own shape moved one module across. It sweeps by window now. And the global hourly ceiling is global to one *instance*: with `max_instance_count = 4` the effective bound is up to four times the configured value across four uncoordinated buckets. The per-address limits degrade proportionally under that weakening; the global one was meant to be the single deployment-wide backstop, and is not one until TD-24's shared store lands.

**What this did not do**, deliberately, is move site state to a shared store. TD-24 keeps the two consequences that needs — session coherence across instances, and per-instance counters — and loses the memory-growth clause to this entry. ADR 0011 records why the shared-store decision belongs with TD-25's in phase 5 rather than being taken here for half the problem.

### Notes on status

The current state of every entry is the index at the top of this file. An earlier version of this line listed TD-10, TD-11, TD-16, TD-17, TD-19, TD-22 and TD-34 as open after each had been closed in its own entry.

**TD-18 was closed on 2026-08-28 and the closure overstated what shipped.** The runner does construct run results and `ModelPin` does land on every one, which is what the entry claimed. But the identifier it carries is asserted by the operator and never observed — the runner cannot choose the model that answers — so FR-22a is not met and the pin describes an intention rather than a fact. The mechanical half stays closed; the requirement it was standing in for is TD-33.

TD-24 is narrower than it was: TD-27 took the memory-growth half of it. TD-26 and TD-28 were filed under this heading by mistake and are back under Open, where the line above always said they were.

TD-13 and TD-14 are resolved in substance — both keep an open clause named in their own entries, and neither is a decision still to be taken.

### Project number in spike 0.4 — no redaction (2026-08-12)

**Source:** code-reviewer, `feat/p1-bootstrap-oidc`. **Requirement:** POR-6. **Verdict:** not debt.

`docs/spikes/0-4-metadata-server.md` records a real service account address containing the project number, and the reviewer read POR-6 as requiring a placeholder. Reviewed and rejected.

A project number authenticates nothing — it appears in every `principalSet://` string and grants nothing on its own. POR-6 is a portability requirement: it exists so a deployer does not copy someone else's identifiers into their own deployment, and nobody deploys from a spike transcript. The deployment is disposable by design (SEC-2) and is destroyed.

The spike's evidentiary value depends on it being real measured output. Spike 0.4's finding is that the *default* compute service account was attached and read the estate; redacting the transcript weakens the evidence for no gain.

Recorded so it is not raised again at publication time.

**Superseded at publication (2026-10-24).** The public export replaced the project number in that spike with zeros anyway (`PUBLICATION.md`). The finding's evidentiary content — that the default compute account was attached and read the estate — does not depend on the digits.
