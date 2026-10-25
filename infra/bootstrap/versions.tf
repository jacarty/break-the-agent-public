terraform {
  required_version = ">= 1.9"

  required_providers {
    # Pinned exactly rather than by range. This module is applied by hand and
    # rarely, so an unreviewed provider bump between applies is a surprise
    # nobody is watching for. Bump deliberately, in a PR.
    google = {
      source  = "hashicorp/google"
      version = "7.43.0"
    }

    # Used only to absorb workload identity pool propagation delay. See wif.tf.
    time = {
      source  = "hashicorp/time"
      version = "0.14.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  # Required as of the browser API key moving here (ADR 0012), and set for the
  # same reason `infra/hosting` sets it. `apikeys.googleapis.com` attributes
  # quota to the *calling client* project rather than to `project` above, so
  # under local Application Default Credentials it bills Google's own default
  # client — which has the API disabled. The failure is a 403 SERVICE_DISABLED
  # naming a project number that is not yours, which reads as a missing API on
  # this project and is not.
  #
  # `infra/hosting/versions.tf` carries the identical block and says it was set
  # pre-emptively after `infra/billing-guard` hit it (TD-7). This module had no
  # such resource until now; it does, so it needs the same.
  user_project_override = true
  billing_project       = var.project_id
}
