# 1.1 — SEC-3 rehearsal: the billing kill switch, fired for real

**Date:** 2026-08-19
**Requirement:** SEC-3 — *tested before the lab is published, not merely deployed.*
**Scratch project:** `example-sec3-rehearsal`, on the same billing account as the lab, deleted afterwards.
**Module under test:** `infra/billing-guard/`, applied from a copy with the `backend "gcs"` block removed, `enable_kill_switch = true`, `budget_amount = 1`.

Phase 1 requires billing-disable demonstrated once, end to end, with evidence. This is the evidence. It is a spike record rather than a passing test because the thing demonstrated cannot be re-run: it ends with a project whose billing is detached.

---

## Result

The kill switch works. It did not work on the first firing, and the reason it did not is the more valuable half of this record.

```
18:30:04.485  CAP REACHED: spend 5.00 of 1.00. Detaching example-sec3-rehearsal.
18:30:04.948  NOTICE  project   (billing association changed)
18:30:05.933  billing detached from example-sec3-rehearsal
```

```
$ gcloud beta billing projects describe example-sec3-rehearsal
billingAccountName: ''
billingEnabled: false
```

Path exercised end to end: **Pub/Sub topic → Eventarc trigger → Cloud Function → billing detached.** Messages were hand-published to the topic in the shape the budget service sends; see *What this does not establish*.

---

## Four defects, none of which a deploy could have found

Each was invisible to `terraform apply`, which was green throughout, with `kill_switch_armed = true`.

### 1. The guard could not fire. It died on a read it did not have permission for.

`roles/billing.projectManager` carries exactly two permissions, both writes:

```
resourcemanager.projects.createBillingAssignment
resourcemanager.projects.deleteBillingAssignment
```

`main.py` calls `GetProjectBillingInfo` *before* detaching, which needs `resourcemanager.projects.get`. The function raised `PermissionDenied: 403` and never attempted the detach it **was** authorised to perform.

The absence of the `CAP REACHED … Detaching` line is what located it: that `log.critical` sits immediately before `_detach()`, so execution had stopped at the pre-read.

**Fixed by granting the missing read.** The rehearsal used a one-permission custom role, `billingGuardReader`, and applying it is what produced the successful detach above.

**The lab does not use that form, and the difference is itself a finding.** CI run 32292027232 failed with `403 iam.roles.get`: creating a custom project role needs `roles/iam.roleAdmin`, which `ci_deploy_roles` does not grant. Shipping the custom role would have meant granting CI the authority to mint and edit any role on the project — buying least privilege for a function's service account at the cost of a large privilege increase for the deploy identity, which is the more consequential of the two and the one whose changes leave no audit trail (TD-6). The lab therefore grants `roles/browser`, which carries the same `resourcemanager.projects.get` plus five other read-only permissions and needs no new CI authority.

The rehearsal established which **permission** was missing. It could not establish which **grant** was affordable, because it ran under an operator holding `roles/owner` where every form applies equally. A rehearsal on a scratch project cannot test the deploy identity's authority unless it uses that identity.

This is the shape of failure SEC-3 exists to catch: deployed and tested were indistinguishable until the day it ran.

### 2. The module cannot be applied to a bare project.

`apis.tf` enabled ten APIs and depended on two it never named — `cloudresourcemanager.googleapis.com` and `iam.googleapis.com`. The lab gets both from `infra/bootstrap/`, so the module had never met a project without them.

Against a bare project every entry fails with a 403 naming Cloud Resource Manager, which reads as a credential problem rather than a missing dependency.

**Partly fixed.** Both are now declared, and the comment says plainly that the list cannot bootstrap them: the provider resolves the project through Cloud Resource Manager before it can read any `google_project_service` back, including that one. A genuinely bare project still needs them enabled out of band, and the rehearsal section now says so.

### 3. The documented verification query returns nothing on success.

The README's step 3 filtered `severity>=CRITICAL`. `main.py` uses `logging.basicConfig`, so `log.critical()` reaches Cloud Run as unstructured stdout with **no severity field**. The query matched nothing on the successful run — a working guard reading as a broken one.

**Fixed** in the README by dropping the severity filter. The durable alternative is structured logging via `google-cloud-logging`, which would also make severity usable in alerting; not done, and worth doing if anything ever alerts on this.

### 4. Rehearsal steps 5 and 6 are not executable, and the idempotence branch is close to unreachable.

Detaching billing makes the project read-denied:

```
Read access to project 'example-sec3-rehearsal' was denied:
please check billing account associated and retry
```

The function stops running, so nothing further can be published to it. Consequences:

- The under-cap path must be exercised *before* the firing message, or left to `tests/test_billing_guard.py`, which covers it in `decision.py`.
- The *already detached* branch needs billing **enabled** and the project **detached** simultaneously — contradictory states.

So the claim that "idempotence comes from re-reading `billing_enabled`, not from remembering" is **unverified**, and `budget.tf`'s note that the firing path runs repeatedly by design is true of the publisher and false of the consumer: the budget keeps publishing past the cap, and by then nothing is listening.

The pre-read is kept regardless, because it makes the outcome attributable — acted, or found already acted — and that distinction is one this project refuses to blur elsewhere. But it is now documented as a design preference rather than a tested mechanism.

---

## What this does not establish

**Every message was hand-published.** Delivery from the topic through Eventarc to the function is proven. That a genuine budget *threshold crossing* publishes to the topic is not, and it is the half TD-7 was written about. Establishing it needs real spend crossing a real threshold, on a budget whose notifications lag by hours.

**The lab's own guard was not tested.** It was deployed before `billingGuardReader` existed and would fail exactly as this one did. The fix reaches the lab only on the next `ci.yml` estate dispatch. `GCP_ENABLE_KILL_SWITCH` must stay `false` until then — arming it against the currently deployed guard would arm nothing.

**One firing, one project.** No claim about behaviour under concurrent notifications, retries, or a budget with multiple threshold rules crossing at once.

---

## Evidence

Log lines above, captured while the project still answered, and the `billingEnabled: false` output. The scratch project was deleted afterwards, so neither is reproducible from the cloud; the API returns nothing for a deleted project and the ID stays reserved for 30 days.
