# Every value here is an input (POR-6). No defaults are given for anything
# deployment-specific: a default project ID or bucket name is exactly how a
# repository becomes single-tenant without anyone deciding that it should.

variable "project_id" {
  description = "GCP project the lab is deployed into. Disposable, on a separate billing account (SEC-2)."
  type        = string
}

variable "region" {
  description = "Default region for regional resources created by later modules."
  type        = string
}

variable "github_repository" {
  description = "The repository permitted to exchange an OIDC token, as owner/name."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be in owner/name form, e.g. octocat/example."
  }
}

variable "github_owner_id" {
  description = <<-EOT
    Numeric GitHub account ID of the repository owner, as a string. Checked
    alongside the repository name so that a token cannot be minted by whoever
    claims the owner/name string after a rename, transfer or deletion — the
    name is reusable, the account ID is not.

    Obtain with: gh api users/OWNER --jq .id
  EOT
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "github_owner_id must be the numeric account ID, not the login name."
  }
}

variable "pool_id" {
  description = <<-EOT
    ID of the CI workload identity pool. Deliberately distinct from the pool
    used for arena authorisation (SEC-8) — the arena pool federates untrusted
    personas into the lab, this one federates CI into the project, and a shared
    pool would put both behind one attribute namespace.

    Deleting a pool soft-deletes it for 30 days and the ID cannot be reused in
    that window (spike 0.1 finding 6), so this value is effectively one-way.
    That finding tabulates the IDs the spikes burned and when they free up.
  EOT
  type        = string
  default     = "github-ci"
}

variable "arena_pool_id" {
  description = <<-EOT
    ID of the arena workload identity pool, which federates Identity Platform
    personas into the lab for L3. Separate from the CI pool by requirement
    (SEC-8), not by convenience: they trust different issuers and key on
    different attributes, and one pool would put CI and untrusted personas in a
    single attribute namespace.

    Subject to the same one-way naming as `pool_id` — see the note there.
  EOT
  type        = string
  default     = "arena-personas"
}

variable "arena_provider_id" {
  description = "ID of the Identity Platform OIDC provider on the arena pool."
  type        = string
  default     = "identity-platform"
}

variable "pool_propagation_delay" {
  description = <<-EOT
    How long to wait after creating the pool provider before writing IAM
    bindings that name its principalSet. Spike 0.1 finding 5 observed pool
    resources reading NOT_FOUND immediately after creation and IAM changes
    lagging behind. Lower it if applies are reliably fast; raise it if a first
    apply fails on an unknown principal.

    Applies to both pools. The arena pool's bindings are written by a later
    module, so its wait is absorbed here rather than there.
  EOT
  type        = string
  default     = "60s"
}

variable "provider_id" {
  description = "ID of the OIDC provider on the CI pool."
  type        = string
  default     = "github-oidc"
}

variable "registry_id" {
  type        = string
  description = "Artifact Registry repository for the site's container image. Lives in bootstrap because hosting requires a digest-pinned image to exist before it can be applied, and because it must survive a teardown of the estate and the site."
}

variable "arena_service_name" {
  type        = string
  default     = "bta-arena"
  description = <<-EOT
    Cloud Run service name of the arena site. Bootstrap does not create that
    service — `infra/hosting` does, from its own `service_name`, and the two
    default to the same value. It is needed here only to name the Identity
    Platform browser key, which lives in this module because its destroy is
    irreversible for 30 days (identity_platform_key.tf).

    If an operator overrides `service_name` in hosting, this moves with it.
    They are separate variables rather than one because POR-4 keeps the modules
    uncoupled, and a shared value passed between them would be exactly the
    coupling that requirement forbids.
  EOT
}

variable "state_bucket_name" {
  description = <<-EOT
    Name of the GCS bucket holding Terraform state for every module. Globally
    unique across GCS, so it cannot have a sensible default.
  EOT
  type        = string
}

variable "state_bucket_location" {
  description = "Location of the state bucket. Multi-region (EU, US) or a region."
  type        = string
}

variable "grant_ci_state_access" {
  description = <<-EOT
    Grant the federated CI principal objectAdmin on the state bucket. Off by
    default so that the OIDC exchange can be verified in isolation — a failing
    exchange and a missing role produce very different errors, and conflating
    them is the usual reason WIF setup takes an afternoon.
  EOT
  type        = bool
  default     = false
}

variable "ci_project_roles" {
  description = <<-EOT
    Project-level roles granted directly to the federated CI principalSet.
    Empty by default: the trust is created with no authority, and roles are
    attached by the module that needs them rather than accumulated here.
  EOT
  type        = list(string)
  default     = []

  validation {
    # A tripwire, not a security boundary — anyone editing this file can remove
    # it. It exists for the realistic failure: an apply fails, and roles/owner
    # is what a deployer reaches for at that moment. Each role below lets the
    # federated principal create a service account and issue it a key, which is
    # precisely the property SEC-6 and this module's comments claim it has not
    # got. Granting one should be a decision, not a debugging step.
    condition = length(setintersection(toset(var.ci_project_roles), toset([
      "roles/owner",
      "roles/editor",
      "roles/iam.securityAdmin",
      "roles/iam.serviceAccountAdmin",
      "roles/iam.serviceAccountKeyAdmin",
      "roles/resourcemanager.projectIamAdmin",
    ]))) == 0
    error_message = "These roles let the federated principal mint itself a keyed identity, voiding the SEC-6 property this module rests on. Grant something narrower, or amend this validation deliberately and say why in the commit."
  }
}

variable "ci_privileged_ref" {
  description = <<-EOT
    The only git ref that may reach anything able to change the project (TD-2).

    The pool's attribute condition is repository-scoped, which means every ref
    in the repository can authenticate — including a branch in a fork's PR
    where that is enabled. Impersonating the deploy identity is conditioned on
    this instead. Dispatch privileged workflows from this ref or they fail at
    the token exchange, which is the intended behaviour rather than a snag.
  EOT
  type        = string
  default     = "refs/heads/main"

  validation {
    condition     = startswith(var.ci_privileged_ref, "refs/")
    error_message = "Must be a full ref such as refs/heads/main. A bare branch name matches nothing and silently grants access to no one, which looks identical to a broken trust."
  }
}

variable "ci_additional_privileged_refs" {
  description = <<-EOT
    Extra refs that may impersonate the deploy identity, beyond
    `ci_privileged_ref`. Empty by default, and meant to be empty most of the
    time.

    The reason this exists: verifying a change to `ci.yml`, `teardown.yml` or
    anything they run means dispatching them, dispatch runs the ref you name,
    and only `ci_privileged_ref` can mint a deploy token. Without this the loop
    for a one-line fix to a workflow is commit, PR, merge, dispatch, discover
    the next problem — which is how a morning goes on a script nobody can test
    before merging it.

    Reachable on dispatch only. The jobs that authenticate as the deploy
    identity are gated on `workflow_dispatch` in both workflows, and a
    `pull_request` run carries `refs/pull/N/merge`, which matches no entry here.
    Nothing on a listed ref fires on push.

    What it does change: code that has not been through review can be run
    against the estate when someone dispatches it. That is the feature. The
    residual is that a branch also defines its own triggers, so a branch on this
    list could add a push trigger to itself — which needs the same write access
    as merging, and is worth knowing rather than worrying about.

    Name one working branch, not a pattern, and clear it when the work lands.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for r in var.ci_additional_privileged_refs : startswith(r, "refs/")])
    error_message = "Each must be a full ref such as refs/heads/feature-x. A bare branch name matches nothing and grants access to no one, which looks identical to a broken trust."
  }

  validation {
    # `attribute.ref` is compared for equality, so a wildcard would not widen
    # the grant — it would match a branch literally named `*` and silently
    # grant nothing. Refused because it reads as working.
    condition     = alltrue([for r in var.ci_additional_privileged_refs : !strcontains(r, "*")])
    error_message = "Wildcards are not supported: attribute.ref is matched exactly, so a pattern grants access to nobody while looking like it grants access to everybody."
  }
}

variable "enable_ci_deploy_identity" {
  description = <<-EOT
    Create the service account CI impersonates to apply infra/estate (TD-5),
    and let the federated principal mint tokens for it.

    Off by default, on the same reasoning as grant_ci_state_access: the trust
    should be demonstrably working before it is given anything to reach. It
    also means the first bootstrap apply creates no privileged subject at all.
  EOT
  type        = bool
  default     = false
}

variable "ci_deploy_account_id" {
  description = "Account ID of the CI deploy identity. Names a role in the build, not a deployment."
  type        = string
  default     = "ci-deploy"
}

variable "ci_deploy_roles" {
  description = <<-EOT
    Project roles held by the CI deploy identity. The estate needs service
    account and project IAM administration; nothing else here is inherited
    from ci_project_roles, which stays empty.

    The budget in infra/billing-guard/ is a billing-account resource, not a
    project one, so the role it needs cannot be granted from this module. See
    "The deploy identity" in README.md for the one binding an operator applies
    by hand.
  EOT
  type        = list(string)
  default = [
    # The estate.
    "roles/iam.serviceAccountAdmin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/bigquery.admin",
    "roles/storage.admin",
    "roles/datastore.owner",
    "roles/firebaserules.admin",
    "roles/secretmanager.admin",
    "roles/logging.admin",
    "roles/serviceusage.serviceUsageAdmin",

    # The billing guard. A gen2 function is a Cloud Run service built by Cloud
    # Build into Artifact Registry and triggered through Eventarc, so deploying
    # one touches five services rather than the one its name suggests.
    "roles/pubsub.admin",
    "roles/cloudfunctions.admin",
    "roles/run.admin",
    "roles/eventarc.admin",
    "roles/artifactregistry.admin",
    "roles/cloudbuild.builds.editor",
    # Deploying a service that runs as another identity is an act on that
    # identity. Needed for the guard's own service account.
    "roles/iam.serviceAccountUser",

    # infra/hosting. Neither of these is implied by anything above, and the
    # first CI apply of the site failed on the first of them with a bare
    # `403: The caller does not have permission` — the hand-apply that preceded
    # it ran as an owner, which is exactly the difference an operator does not
    # see until CI runs the same module.
    #
    # identityplatform.admin manages the tenant's sign-in configuration. It is
    # the authentication surface personas are minted against, so this is a real
    # grant rather than a formality: an identity holding it could enable a
    # sign-in provider the lab deliberately disables (`sign_in` in
    # identity_platform.tf turns anonymous and email/password off explicitly).
    # It is accepted because the same identity already holds projectIamAdmin,
    # which reaches further, and narrowing it needs a role that does not exist.
    "roles/identityplatform.admin",
    #
    # `roles/serviceusage.apiKeysAdmin` was here and has been removed. It
    # existed for the browser API key, which now lives in this module
    # (identity_platform_key.tf) and is applied by an operator holding
    # roles/owner. CI no longer creates, reads or destroys a key, so the grant
    # no longer has a caller — and an unused grant on an identity reachable
    # from `main` is the shape TD-2 exists to keep out.

    # Teardown verification. `scripts/verify-teardown.sh` enumerates workload
    # identity pools and their providers, because bootstrap's pools survive a
    # destroy by design (TD-1) and the check has to tell a survivor apart from
    # an orphan. An enumerator that cannot answer fails the run rather than
    # reporting a clean project, so without this the baseline cannot be
    # captured at all.
    #
    # **Viewer, not Admin, and that distinction is the whole of TD-6.** This
    # role carries `get` and `list` and nothing else. Admin would additionally
    # carry `update` — the ability to rewrite the attribute condition on the
    # pool that authenticates the identity holding it, from an account
    # impersonable from `main`. Read is safe here; write is the escalation
    # TD-2's ref-scoping exists to prevent.
    "roles/iam.workloadIdentityPoolViewer",
    # The same argument for the same script's backstop enumerator. Cloud Asset
    # Inventory is what catches a resource type none of the other enumerators
    # know about, and `searchAllResources` is read-only.
    "roles/cloudasset.viewer",
  ]

  validation {
    # The two IAM-admin roles above are granted deliberately and are what TD-5
    # is about. These three are not the same decision and are refused here for
    # the same reason the federated principal refuses them.
    #
    # keyAdmin is the one that matters: the whole argument for impersonation
    # over a direct grant is that no key is ever issued. An identity that can
    # create keys makes that argument false. Note that projectIamAdmin above
    # can grant keyAdmin to itself, so this is a tripwire and not a boundary —
    # it catches the deliberate edit, not the determined one.
    #
    # workloadIdentityPoolAdmin is refused for a different reason and is the
    # one TD-6 turned up. This identity is impersonable from `main`; pool admin
    # would let it rewrite the attribute condition on the provider that
    # authenticates it, widening the trust to a repository someone else
    # controls. The Viewer role above is what the teardown scan actually needs,
    # and it is deliberately not a step away from this one.
    condition = length(setintersection(toset(var.ci_deploy_roles), toset([
      "roles/owner",
      "roles/editor",
      "roles/iam.serviceAccountKeyAdmin",
      "roles/iam.workloadIdentityPoolAdmin",
    ]))) == 0
    error_message = "owner, editor, serviceAccountKeyAdmin and workloadIdentityPoolAdmin are refused here. keyAdmin voids the reason this identity is impersonated rather than granted directly (TD-5): no key is ever issued. workloadIdentityPoolAdmin would let an identity reachable from the privileged ref rewrite the trust that authenticates it (TD-6) — grant workloadIdentityPoolViewer instead, which is what reading the estate requires."
  }
}
