# infra/hosting

Cloud Run, Identity Platform, the evidence bucket, and the site's secrets. This
is where TD-13's marker delivery lands. There are no provider-key secrets: every
model is reached through Vertex under the runtime's own IAM (FR-22, amended
2026-08-28), which retired TD-22.

`infra/estate/` has no coupling to this module (POR-4). The dependency runs one
way: hosting consumes `terraform output` from estate and bootstrap, and nothing
there reads anything here. That is what lets the estate be reseeded without
touching the site.

---

## Applying

**Follow `docs/first-deploy.md` §12, not this section.** Twenty-eight of this
module's variables are required and none has a default, they come from five
different places, and Terraform refuses to plan on the first one it cannot
resolve — so a bare `terraform apply` here fails immediately on a wall of
missing variables. §12 collects them, plans, and then applies.

```sh
# The shape only. The inputs come from docs/first-deploy.md §12.1.
terraform -chdir=infra/hosting init -backend-config="bucket=$TF_STATE_BUCKET"
terraform -chdir=infra/hosting plan -input=false
```

Every input is a variable and none has a deployment-specific default (POR-6).
The values come from `terraform output` in the two modules that ran before, and
from the operator for the secrets, the evidence bucket and the model
identifiers.

**Applied against a live estate.** This module served the phase 4 manual
captures and the 2026-09-15 reference run (`docs/results.md`), and was
destroyed with the rest of the lab afterwards. The preferred path is `ci.yml`'s
hosting job; the manual path in §12 is the fallback.

---

## What this costs (NFR-4)

**Measured for the reference run** — the README's *Cost* section has the billed
figures (≈ £7 for the full run plus a day of uptime). What follows is the
arithmetic behind them, so you can redo it for your own region and uptime.

### Idle

`cloudrun.tf` sets `min_instance_count = 1` with `cpu_idle = false`, which is
*CPU always allocated*: one instance at 2 vCPU and 2 GiB bills continuously,
whether or not the site is ever visited. That is the whole of the idle cost and
it is not small.

```
2 vCPU × 2,628,000 s/month × <vCPU-second rate>
+ 2 GiB × 2,628,000 s/month × <GiB-second rate>
```

At instance-based rates in the region of $0.000018 per vCPU-second and
$0.0000020 per GiB-second — **check current Cloud Run pricing for your region
rather than trusting these; they are here to show the arithmetic, not to be
quoted** — that is roughly **$105/month, $3.50/day, $0.14/hour**, before a
single request.

Two consequences worth stating plainly:

- **It can exceed a small budget cap on its own.** `docs/first-deploy.md` uses
  $50 as its example `GCP_BUDGET_AMOUNT`. Idle alone passes that inside three
  weeks, so the SEC-3 kill switch could detach billing before an attacker ever
  reaches the site — a teardown triggered by the site doing nothing.
- **For a short verification run it buys nothing.** `min_instance_count = 1`
  and `cpu_idle = false` exist to keep NFR-1's p95 honest by removing cold
  starts. A run whose purpose is capturing F1 and F2 by hand is not measuring
  p95. Setting `min_instance_count = 0` and `cpu_idle = true` for that run
  costs a cold start on the first request and takes idle cost to approximately
  zero; set them back before any run whose latency figures get published.

### Per suite run

Inference dominates and infrastructure is a rounding error against it: the same
instance serves the suite, and 90 minutes of it (NFR-2's target) is about $0.20
of Cloud Run at the rates above.

The model half is `sessions × tokens per session × rate`, per provider, and
SC-3's repeats set the first factor: 108 cases × 3 levels × 3 repeats = 972
executions per provider. The reference run measured the second: **about 12,700
tokens per execution**, ~12.4M for the full matrix on `gemini-3.7-flash`. A
three-provider run is roughly three times the inference, and partner models are
not priced like Flash.

The control that actually bounds spend is still not this estimate. It is
`daily_token_ceiling` (NFR-3), which applies to bypassed suite requests too,
and the SEC-3 budget cap underneath it. **Set the ceiling from what you are
willing to spend, not from what you predict the suite will use.**

---

## APIs

`apis.tf` enables three services this module needs and none of the other two
modules do: `run`, `identitytoolkit` and `iamcredentials`. `apikeys` was a
fourth until the browser key moved to `infra/bootstrap/` (see below); this
module no longer touches a key, so it no longer enables the API.
`artifactregistry` joins them — the repository itself is `infra/bootstrap`'s
(see [Where the image lives](#where-the-image-lives)), but this module still
needs the API enabled to grant a read binding on it. `secretmanager` is
declared again even though `infra/estate/` already enables it, because relying
on estate having been applied first would be an apply-order dependency between
two modules POR-4 requires to stay independent — enabling an already-enabled
API is a no-op, so the duplication costs nothing.

## Where the image lives

`infra/bootstrap/registry.tf` creates the Artifact Registry repository, not
this module. Two reasons, both about ordering rather than taste: `var.image`
below must be digest-pinned, so an image has to exist and be pushed *before*
this module can be applied at all — a repository this module created could not
receive that push until after the apply that needs it — and the repository has
to survive a `terraform destroy` of the estate and the site, since rebuilding
an unchanged image on every teardown/redeploy cycle (NFR-6) would make
teardown destructive to something it has no business touching.

This module only binds `roles/artifactregistry.reader` on the repository
(`var.registry_id`) to the runtime service account, so the running revision can
pull its own image.

**`var.registry_id` is the bare repository ID, not bootstrap's
`registry_repository` output.** That output is the full
`<region>-docker.pkg.dev/<project>/<repo>` push target, and bootstrap publishes
no output for the short form — it is bootstrap's own `registry_id` *input*, so
the value to use is the last segment of the published one. Passing the whole
thing makes Terraform assemble a resource ID containing a hostname and slashes,
and the apply fails on a resource that reads as correctly configured. This
sentence said the wrong thing until the first apply was attempted.

## Where the browser API key lives

`infra/bootstrap/identity_platform_key.tf` creates it, not this module, and the
reason is sharper than the registry's. **Destroying a Google API key
soft-deletes it and locks the key id for 30 days**, the id is derived from the
service name, and `teardown.yml` destroys this module on every cycle as NFR-6
requires. A key created here would make the site undeployable for a month after
its first teardown.

So it arrives as `var.identity_platform_api_key` — `ci.yml` reads it out of
bootstrap's state, and an operator applying by hand gets it from
`terraform -chdir=infra/bootstrap output -raw identity_platform_api_key`.
`docs/decisions/0012-irreversible-resources-live-in-bootstrap.md` carries the
argument, including why `prevent_destroy` here was the wrong answer.

What stayed is the part that makes the key safe to publish: the tenant
configuration in `identity_platform.tf` enables no self-service sign-in
provider (SEC-10), so a known key cannot register an identity. That resource is
destroyed and recreated freely and belongs to the cycle.

**After this move, no resource in this module has an irreversible destroy.**
That is the property the file layout is protecting, and it is worth checking
against any resource added here later.

## What the runtime reads from Secret Manager

Six secrets, all mounted `version = "latest"`: the access code and the suite
bypass token, C1's and F6's markers, F4's own hosting-owned copy (a copy, not a grant on the estate's
secret — the estate's binding stays restricted to the owning team's
principalSet, so an L3 denial still happens in Secret Manager rather than in
the agent), and the fixture manifest as a mounted file rather than an
environment variable — the site scores its own captures in-process (FR-6), and
`schemas.md` §3 keeps the set of components that may read it to two.

**`version = "latest"` is a deliberate choice for the access code and the
bypass token — they rotate by operator action and a revision should pick up
whatever is current — and it is worth naming the operational consequence
rather than assuming it is obvious: rotating a value in Secret Manager does
not, by itself, produce a new Cloud Run revision. A running container that
already resolved a mounted secret at instance start keeps that value until it
is replaced; only a new revision (or a new instance start) reads the version
Secret Manager now calls latest. Rotate-and-forget is not rotate-and-take-effect
here.

The fixture manifest and the two markers are different in kind — they are
pinned to *what the manifest says now*, not to an operator's rotation
schedule, so `"latest"` there tracks the current seed rather than a rotation
event. A reseed that writes a new manifest secret version needs a new
revision to be picked up, exactly as a rotated access code does.

---

## The four decisions worth knowing

**The image must be digest-pinned**, enforced by a variable validation. A
mutable tag would let two applies of the same commit deploy different code,
which SC-6's promise of a materially similar matrix depends on not happening.

**`secretAccessor` is granted per secret, never at project level.** A
project-level binding would hand the runtime identity the estate's integration
secret — which is F4, the value L3's Onboarding defence exists to keep away
from it. SEC-11 says the runtime holds no estate access, and a convenient
project-wide grant here is exactly how that gets violated without anyone
editing `infra/estate/`.

**SEC-10 disables each self-service provider explicitly** rather than by
omission. An absent block is a default and a default is not a decision.
Anonymous sign-in is named because it is one console toggle away, commonly
enabled while debugging, and silently permanent once forgotten. The public API
key — created in `infra/bootstrap/`, see above — has `identitytoolkit` as its
only target, so a leaked key cannot be turned on any other enabled API in the
project.

**The runtime signs its own custom tokens** via `serviceAccountTokenCreator` on
*itself*, so no service account key exists anywhere (SEC-5). The grant lets it
assert `sub: alice`, which is worth what alice is worth. It is deliberately not
granted on the shared or provisioning accounts — those two do hold estate
access, and `infra/estate/identity.tf` already grants impersonation on them.

---

## The line that makes this an internet-reachable vulnerable service

`google_cloud_run_v2_service_iam_member.public` binds `roles/run.invoker` to
`allUsers`. That is required — an attacker has to reach the site — and it is
the single most consequential resource in the module. The FR-1 access code is
the only thing in front of it, and FR-2 insists it is not a security boundary.

DIS-4 requires the README's deployment warnings above the quickstart, not below
it, for exactly this.

---

## Teardown

`scripts/verify-teardown.sh` compares the project against a baseline captured
before anything was applied. This module's resources are all inside that
comparison, unlike `infra/bootstrap/`'s (TD-1). One caveat is worth stating
because it will surface: **Secret Manager keeps versions**, and the secrets
here hold live flag values. A destroy removes the secrets; a *reseed*
without a destroy leaves the previous seed's marker versions accessible, which
is the same failure `src/bta/generator/README.md` describes for the estate and
the reason its applier prunes rather than inserts.
