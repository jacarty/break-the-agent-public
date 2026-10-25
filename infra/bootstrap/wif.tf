data "google_project" "this" {
  project_id = var.project_id

  # Reads through Cloud Resource Manager. Deferred until this module has
  # asserted the API is enabled, so a first plan against a project where it is
  # off does not fail before anything has been created.
  depends_on = [google_project_service.bootstrap]
}

resource "google_iam_workload_identity_pool" "ci" {
  project                   = var.project_id
  workload_identity_pool_id = var.pool_id
  display_name              = "GitHub Actions CI"
  description               = "Federates GitHub Actions into the project. Separate from the arena pool (SEC-8)."

  depends_on = [google_project_service.bootstrap]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.ci.workload_identity_pool_id
  workload_identity_pool_provider_id = var.provider_id
  display_name                       = "GitHub OIDC"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  # Mapped attributes are the only claims usable in IAM conditions and member
  # names. `ref` and `environment` are mapped but unused today, because adding
  # an attribute later does not retroactively apply to bindings that were
  # written without it.
  attribute_mapping = {
    "google.subject"        = "assertion.sub"
    "attribute.repository"  = "assertion.repository"
    "attribute.ref"         = "assertion.ref"
    "attribute.environment" = "assertion.environment"
  }

  # Without this condition the provider trusts every repository on GitHub, and
  # the pool becomes a public entry point into the project. GCP requires a
  # condition whenever a non-Google issuer is used, which is the right default.
  attribute_condition = join(" && ", [
    "assertion.repository == ${jsonencode(var.github_repository)}",
    "assertion.repository_owner_id == ${jsonencode(var.github_owner_id)}",
  ])
}

locals {
  # The federated principal. Roles are granted directly to this member wherever
  # that is possible, so CI holds no long-lived credential and there is no key
  # anywhere in the flow (SEC-6).
  #
  # Direct granting is not always possible. The estate needs two roles this
  # member may not hold, so deploy.tf gives it token-creator on one service
  # account instead. Still keyless — the token comes from STS and expires — but
  # the authority is one hop away rather than absent, and TD-5 says so.
  #
  # That is a property of CI's access path, not of the project. The project
  # holds service accounts — the SEC-11 runtime identity among them — and
  # infra/hosting/ will grant CI roles/iam.serviceAccountUser on one.
  #
  # It also degrades with ci_project_roles: a principal able to administer
  # project IAM or service accounts can mint itself a keyed identity. See the
  # validation on that variable.
  ci_principal_set = format(
    "principalSet://iam.googleapis.com/projects/%s/locations/global/workloadIdentityPools/%s/attribute.repository/%s",
    data.google_project.this.number,
    google_iam_workload_identity_pool.ci.workload_identity_pool_id,
    var.github_repository,
  )

  # The same population narrowed to one ref (TD-2). `attribute.ref` was mapped
  # on the provider from the start precisely so this could be written later
  # without reissuing the trust — an attribute added afterwards does not
  # retroactively apply to bindings written without it.
  #
  # Anything that can change the project is granted to this rather than to
  # ci_principal_set above. A workflow on a branch still authenticates; it just
  # reaches nothing privileged.
  ci_main_principal_set = format(
    "principalSet://iam.googleapis.com/projects/%s/locations/global/workloadIdentityPools/%s/attribute.ref/%s",
    data.google_project.this.number,
    google_iam_workload_identity_pool.ci.workload_identity_pool_id,
    var.ci_privileged_ref,
  )

  # Every ref allowed to impersonate the deploy identity, keyed by the ref so
  # that adding or removing a working branch touches one binding and leaves the
  # others alone. `ci_privileged_ref` is always present; anything else is a
  # deliberate, temporary addition — see `ci_additional_privileged_refs`.
  ci_privileged_refs = toset(concat(
    [var.ci_privileged_ref],
    var.ci_additional_privileged_refs,
  ))

  ci_privileged_principal_sets = {
    for ref in local.ci_privileged_refs : ref => format(
      "principalSet://iam.googleapis.com/projects/%s/locations/global/workloadIdentityPools/%s/attribute.ref/%s",
      data.google_project.this.number,
      google_iam_workload_identity_pool.ci.workload_identity_pool_id,
      ref,
    )
  }
}

# Spike 0.1 finding 5: pool resources are eventually consistent. A provider read
# immediately after creation returned NOT_FOUND, and IAM binding changes took
# time to take effect. An IAM binding naming a principalSet on a pool that has
# not yet propagated is the failure this absorbs — it is a bad-request error on
# apply, not a retryable one, so Terraform will not recover on its own.
resource "time_sleep" "pool_propagation" {
  depends_on = [google_iam_workload_identity_pool_provider.github]

  create_duration = var.pool_propagation_delay
}

resource "google_project_iam_member" "ci" {
  for_each = toset(var.ci_project_roles)

  project = var.project_id
  role    = each.value
  member  = local.ci_principal_set

  depends_on = [time_sleep.pool_propagation]
}
