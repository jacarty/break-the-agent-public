# As in infra/estate, this module enables the APIs it needs rather than
# inheriting a list from bootstrap.

locals {
  guard_apis = [
    # Declared so the dependency is visible, but **this list cannot bootstrap
    # them**. The provider resolves the project through Cloud Resource Manager
    # before it can read any `google_project_service` back — including this one
    # — so against a project where that API has never been used, every entry
    # here fails with a 403 naming Cloud Resource Manager. The error reads as a
    # credential problem and is a missing dependency.
    #
    # Missing until the SEC-3 rehearsal, because the lab gets both from
    # `infra/bootstrap/` and the module had therefore never met a project
    # without them. On a bare project they must be enabled first:
    #
    #   gcloud services enable cloudresourcemanager.googleapis.com \
    #     iam.googleapis.com --project "$PROJECT"
    #
    # `iam` is needed for the guard's service account and its custom role.
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",

    "cloudbilling.googleapis.com",
    "billingbudgets.googleapis.com",
    "pubsub.googleapis.com",
    "cloudfunctions.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "eventarc.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
    "logging.googleapis.com",
  ]
}

resource "google_project_service" "guard" {
  for_each = toset(local.guard_apis)

  project = var.project_id
  service = each.value

  # Left enabled on destroy. Disabling cloudbilling would also break the guard's
  # ability to run during a partial teardown, which is when it matters most.
  disable_on_destroy = false
}
