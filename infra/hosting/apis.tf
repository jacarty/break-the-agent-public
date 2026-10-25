# Hosting enables its own APIs, as bootstrap and estate do. An API list that
# accumulates every service the project will ever use puts the blast radius of
# `terraform destroy` in the wrong module.
#
# Before this file existed the module was un-appliable from clean: none of the
# four services below is enabled by `infra/bootstrap/apis.tf` or
# `infra/estate/apis.tf`, so a first apply into a fresh project failed with
# SERVICE_DISABLED on whichever resource Terraform reached first.

locals {
  hosting_apis = [
    # The site itself.
    "run.googleapis.com",

    # The registry the image is pulled from. The repository itself is created
    # by infra/bootstrap; this module only binds read on it.
    "artifactregistry.googleapis.com",

    # Identity Platform — the persona picker's token issuer.
    "identitytoolkit.googleapis.com",

    # Impersonation and signJwt. `infra/bootstrap/apis.tf` leaves this out on
    # purpose and says why: direct workload identity federation goes through
    # STS alone, and iamcredentials "belongs to whichever module mints persona
    # tokens". That module is this one — the runtime signs its own persona JWTs
    # via roles/iam.serviceAccountTokenCreator on itself.
    "iamcredentials.googleapis.com",

    # Enabled by `infra/estate/apis.tf` too. Declared here as well because
    # hosting reads and creates secrets of its own, and relying on the estate
    # having been applied first is an apply-order dependency between two
    # modules POR-4 requires to be independent. Enabling an enabled API is a
    # no-op, so the duplication costs nothing and removes the ordering.
    "secretmanager.googleapis.com",

    # Every model the lab uses is served from here now (FR-22): Gemini
    # natively, Claude as a partner model. Enabling the API is not enough on
    # its own — Vertex partner models also require accepting the vendor's terms
    # once per project in the Model Garden console, which no Terraform resource
    # expresses. A first apply that skipped that step brings the service up and
    # fails on the first model call, not at apply time.
    "aiplatform.googleapis.com",
  ]
}

resource "google_project_service" "hosting" {
  for_each = toset(local.hosting_apis)

  project = var.project_id
  service = each.value

  # Left enabled on destroy, as in bootstrap and estate: disabling an API
  # breaks any resource in another module that still depends on it, and
  # re-enabling costs nothing. NFR-6 is about provisioned resources, and an
  # enabled API is not one.
  disable_on_destroy = false
}
