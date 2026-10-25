terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "7.43.0"
    }

    # Zips the function source at plan time. The alternative is committing a
    # zip, which makes the deployed code something nobody reviews.
    archive = {
      source  = "hashicorp/archive"
      version = "2.7.1"
    }

    # Propagation absorbers, as in infra/bootstrap. See the two time_sleep
    # resources and the comment on `propagation_delay` for what each waits on.
    time = {
      source  = "hashicorp/time"
      version = "0.14.0"
    }
  }

  # Partial configuration, as in infra/estate:
  #
  #   terraform init -backend-config="bucket=${TF_VAR_state_bucket_name}"
  #
  # The rehearsal runs against a scratch project with no state bucket of its
  # own. Delete this block from a copy of the module there — `-backend=false`
  # does not work: it skips backend *initialisation*, then `apply` parses this
  # block anyway and refuses. Nothing here is worth keeping state for once the
  # scratch project is deleted.
  backend "gcs" {
    prefix = "billing-guard"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  # `billingbudgets.googleapis.com` refuses a request that names no quota
  # project. Without these two settings the provider sends no
  # `X-Goog-User-Project` header, Google attributes the call to its own shared
  # client project, and the budget fails with a 403 `SERVICE_DISABLED` citing a
  # project number belonging to Google — while the API is plainly enabled on
  # ours. The error names the wrong project and the wrong problem.
  #
  # `gcloud auth application-default set-quota-project` does not fix this: the
  # provider does not read ADC's quota project. It has to be told here.
  #
  # Requires `serviceusage.services.use` on `billing_project`. The CI deploy
  # identity holds `roles/serviceusage.serviceUsageAdmin`, which includes it,
  # so this is safe for a workflow apply as well as a local one.
  user_project_override = true
  billing_project       = var.project_id
}
