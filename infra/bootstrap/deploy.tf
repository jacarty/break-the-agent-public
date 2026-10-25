# TD-5. `infra/estate/` declares service accounts and project IAM bindings, so
# whatever applies it needs `roles/iam.serviceAccountAdmin` and
# `roles/resourcemanager.projectIamAdmin`. Granting those to the federated
# principal directly is what `ci_project_roles`' validation refuses, and it
# refuses them for a good reason: a principalSet holding project IAM admin can
# grant itself key admin and mint a key. NFR-5 needs CI to apply the estate
# anyway.
#
# Resolved by impersonation. The roles land on a named service account whose
# grants are declared here and reviewable in one place; CI holds only
# `roles/iam.serviceAccountTokenCreator` on that one account, as a resource
# binding rather than a project role. `ci_project_roles` stays empty and its
# tripwire stays armed.
#
# What this does not do is remove the privilege. A principal that can mint an
# access token for an account holding project IAM admin has project IAM admin,
# one hop away. Two things are genuinely different and neither is cosmetic:
#
#   - No key is ever issued. The token is short-lived and comes from STS, so
#     SEC-6's "no long-lived cloud credential" survives intact and SEC-5 is
#     untouched. The direct grant would have kept that too; this is the part
#     that is merely equal.
#   - The authority is attached to a subject rather than to the trust. Widening
#     it means editing `ci_deploy_roles`, which is one variable with its own
#     refusal list, rather than widening what every workflow in the repository
#     can do by default. Revoking it is deleting one binding.
#
# TD-2 would otherwise stop being theoretical here — repository-scoped trust
# plus IAM-admin authority means any ref reaches this account. So the binding
# below names `ci_main_principal_set`, not `ci_principal_set`: impersonation is
# conditioned on the ref, enforced by IAM at the token exchange rather than by
# a GitHub setting somebody has to remember to configure.

resource "google_service_account" "ci_deploy" {
  count = var.enable_ci_deploy_identity ? 1 : 0

  project      = var.project_id
  account_id   = var.ci_deploy_account_id
  display_name = "CI deploy (Terraform)"
  description  = "Impersonated by GitHub Actions to apply infra/estate. No keys (SEC-5, SEC-6)."

  depends_on = [google_project_service.bootstrap]
}

resource "google_project_iam_member" "ci_deploy" {
  for_each = var.enable_ci_deploy_identity ? toset(var.ci_deploy_roles) : toset([])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.ci_deploy[0].email}"
}

# Two narrowings, and they are independent of each other.
#
# Resource-scoped, not project-scoped: `roles/iam.serviceAccountTokenCreator` at
# project level would let CI impersonate every service account in the project,
# including the SEC-11 runtime identity — the one the whole L3 column depends on
# being unreachable.
#
# Ref-scoped, not repository-scoped: only workflows running on
# `ci_privileged_ref` can mint a token for it (TD-2).
resource "google_service_account_iam_member" "ci_impersonation" {
  for_each = var.enable_ci_deploy_identity ? local.ci_privileged_principal_sets : {}

  service_account_id = google_service_account.ci_deploy[0].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value

  depends_on = [time_sleep.pool_propagation]
}

# Keyed by ref rather than counted, so adding a working branch does not disturb
# main's binding. Without this the address change alone would destroy and
# recreate the grant CI authenticates with — a revocation window during an apply
# whose whole purpose is to widen access, and the kind of plan that reads as a
# mistake when it is not.
#
# A migration shim, and it assumes `ci_privileged_ref` is its default. A
# deployment that set something else must edit the key here or drop this block
# and accept one destroy-and-create. Deletable once applied.
moved {
  from = google_service_account_iam_member.ci_impersonation[0]
  to   = google_service_account_iam_member.ci_impersonation["refs/heads/main"]
}

# The deploy account writes state like any other Terraform runner. Gated on the
# same variable as the federated grant so the two cannot drift: turning on
# impersonation without this produces a 403 on `terraform init`, which reads as
# a broken trust rather than a missing role.
resource "google_storage_bucket_iam_member" "ci_deploy_state" {
  count = var.enable_ci_deploy_identity && var.grant_ci_state_access ? 1 : 0

  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.ci_deploy[0].email}"
}
