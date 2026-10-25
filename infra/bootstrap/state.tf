# Terraform state for every module in this repository. Created here because it
# has to exist before any module can use a remote backend, including this one —
# see README.md for the two-step apply that resolves that ordering.

resource "google_storage_bucket" "state" {
  project  = var.project_id
  name     = var.state_bucket_name
  location = var.state_bucket_location

  # State carries resource identifiers and, for later modules, secret material.
  # None of it is ever public and none of it is served.
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # A corrupted or truncated state file is recoverable only from a prior
  # version, so versioning is not optional here.
  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 20
    }
    action {
      type = "Delete"
    }
  }

  # Refuse to delete a bucket that still holds objects. State is the one thing
  # in this project that cannot be regenerated from the repository.
  force_destroy = false

  lifecycle {
    # Destroying this bucket orphans every resource in every other module,
    # leaving a project full of things Terraform no longer knows about. Removing
    # the bucket is a deliberate act that should require editing this file.
    prevent_destroy = true
  }

  depends_on = [google_project_service.bootstrap]
}

# CI cannot run Terraform at all without read/write on the state bucket, so this
# is arguably part of the trust rather than a deployment permission. It is still
# off by default: the trust should be verifiable before it is granted anything.
#
# objectUser covers get, create, list and delete, which is everything the GCS
# backend does — delete included, since the backend removes its own lock object.
# objectAdmin was the obvious choice and is wrong: it adds objects.setIamPolicy,
# setRetention and overrideUnlockedRetention, none of which the backend uses.
#
# The residual is worth naming rather than implying away. Delete extends to
# noncurrent versions, so this principal can destroy the history that the
# versioning block above calls non-optional. Versioning protects against a bad
# apply; it does not protect against this grant.
resource "google_storage_bucket_iam_member" "ci_state" {
  count = var.grant_ci_state_access ? 1 : 0

  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectUser"
  member = local.ci_principal_set

  depends_on = [time_sleep.pool_propagation]
}
