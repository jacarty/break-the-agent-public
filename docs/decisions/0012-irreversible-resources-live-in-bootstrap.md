# 0012 — Resources whose destroy is irreversible live in bootstrap

**Date:** 2026-08-28
**Phase:** 4 (before the first teardown of a deployed site)
**Status:** accepted
**Requirements:** NFR-5, NFR-6, POR-4, POR-6, SEC-10, FR-3
**Related:** TD-30, ADR 0011, `infra/bootstrap/registry.tf`

## Decision

`google_apikeys_key.identity_platform` moves from `infra/hosting/` to
`infra/bootstrap/`. Hosting consumes it as `var.identity_platform_api_key`;
bootstrap publishes it as an output. `roles/serviceusage.apiKeysAdmin` is
removed from `ci_deploy_roles`, because CI no longer creates, reads or destroys
a key.

The general rule this instance establishes: **a resource whose destroy cannot
be undone within a deploy cycle belongs to the module that is not destroyed.**

## Why

`teardown.yml` destroys `infra/hosting` on every cycle, and NFR-6 requires that
it do so. Destroying a Google API key soft-deletes it and **locks the key id
for 30 days**. The id was `"${var.service_name}-identity-platform"` — derived
from a variable with a default, so identical on every deploy.

The consequence is not a slow teardown. It is that the first teardown of the
site makes the site undeployable for a month: the next apply asks for a name
the previous destroy just locked, gets a collision, and there is no input an
operator can change that does not also change the deployment's identity.

It was found by reading, not by hitting it. `infra/hosting/identity_platform.tf`
already carried the 30-day lock in a comment — it had been discovered on
2026-08-28 as a *replacement* loop, when `project` was set to the project id
rather than the number and every plan proposed a destroy-and-create that could
not succeed. That fix (commit `56c4ec9`) stopped Terraform proposing the
replacement; it left the same trap sitting behind the teardown path, one
dispatch away, unremarked.

## Why bootstrap specifically, and not "hosting but with prevent_destroy"

`prevent_destroy` would make `teardown.yml` fail rather than silently burn the
name, which is better than nothing and still wrong: NFR-6 is a claim that
`terraform destroy` removes every provisioned resource, and a module that
refuses to be destroyed makes that claim false in a second way. The honest
statement is that this resource is not part of what a cycle provisions.

Bootstrap is already the module that holds exactly this class of thing, for
exactly this reason. The state bucket has to exist before anything writes state
and must survive everything. The image registry has to exist before hosting can
be applied at all, because `var.image` must be digest-pinned, and must survive a
teardown so that redeploying an unchanged commit does not require a rebuild
(`registry.tf`). The browser key is the third: needed before the site can
authenticate anyone, and unrecreatable for 30 days after a destroy.

TD-1 already records that bootstrap surviving a teardown puts it outside NFR-6's
verified scope, and `scripts/verify-teardown.sh` treats bootstrap's resources as
baseline rather than orphans. This adds one resource to that baseline. It widens
what teardown does not verify, which is a real cost and is why it is written
down here rather than absorbed.

## What this does not change

**POR-4 holds.** Hosting takes the key as an input, the way it already takes
`registry_id` and the estate's outputs. Nothing reads back the other way, and
the two modules gain no shared value — `arena_service_name` in bootstrap and
`service_name` in hosting are separate variables that default to the same
string, deliberately not one variable passed between them.

**SEC-10 is unaffected.** The key is public by design: it is served to every
browser that loads the persona picker and must be assumed known. What makes it
worthless alone is that the tenant enables no self-service sign-in provider, and
that is configured in `infra/hosting/identity_platform.tf`, which did not move.
The `identitytoolkit`-only restriction moved with the resource and is unchanged.

**The tenant configuration stays in hosting.** `google_identity_platform_config`
is destroyed and recreated freely and belongs to the cycle. Only the key moved.
After this change `infra/hosting/` holds no resource whose destroy is
irreversible, which is the property worth having rather than the file layout.

## Consequences

- One more required input to `infra/hosting` — 25 becomes 26. `ci.yml` resolves
  it from bootstrap's state rather than from a GitHub variable, so it stays out
  of the CI platform's configuration entirely; `teardown.yml` passes a
  placeholder, as it does for every other hosting variable it must evaluate but
  never reads.
- CI's authority is reduced by one project role. Unusually, a change that adds
  a step also removes a grant.
- Existing deployments need a state move, not an apply. It is a `state rm` plus
  an `import` and it is destructive if done in the wrong order — the runbook is
  in `infra/bootstrap/README.md` under "Where the browser API key lives".
- A later change (`feature/p5-suite-cases`) added `lifecycle { prevent_destroy = true }`
  to the key, now that it sits in bootstrap. This does not reopen the "hosting
  but with `prevent_destroy`" rejection above: that argument turned on NFR-6
  requiring the *destroyed* module to be destroyable, and bootstrap is already
  outside NFR-6's verified scope by the general rule this ADR establishes. The
  block converts an accidental replacement plan (either cause described here)
  into a refusal at plan time rather than a silent 30-day lock.

## Rejected

**Leave it and rename the key each cycle.** Makes the key id a deployment input
that must change on every deploy, which is a hand-maintained value that
correlates with nothing — and SC-6 asks a deployer to reproduce a run, not to
invent a name for it.

**Recreate it as the Firebase auto-provisioned key.** That key exists in every
project and carries no restriction, so the `identitytoolkit`-only scoping is
lost. The restriction is the reason the resource is declared rather than
adopted.
