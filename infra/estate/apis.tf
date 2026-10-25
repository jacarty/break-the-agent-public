# Estate enables its own APIs rather than inheriting a list from bootstrap.
# An API list that accumulates every service the project will ever use puts the
# blast radius of `terraform destroy` in the wrong module.
#
# This list grows as the estate does — Storage, Firestore, Secret Manager and
# Logging arrive with the resources that need them.

locals {
  estate_apis = [
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",

    # Firestore Security Rules are managed through the Firebase Rules API,
    # which is a separate service from Firestore itself. Enabling it does not
    # register the project with Firebase and adds no Firebase resources.
    "firebaserules.googleapis.com",
  ]
}

resource "google_project_service" "estate" {
  for_each = toset(local.estate_apis)

  project = var.project_id
  service = each.value

  # Left enabled on destroy, as in bootstrap: disabling an API breaks any
  # resource in another module that still depends on it, and re-enabling costs
  # nothing. NFR-6 is about provisioned resources, and an enabled API is not one.
  disable_on_destroy = false
}
