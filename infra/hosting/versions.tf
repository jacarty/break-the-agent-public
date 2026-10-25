terraform {
  required_version = ">= 1.9"

  required_providers {
    # Pinned exactly, matching infra/bootstrap and infra/estate. A provider
    # bump is a reviewed change, not something that happens between two
    # applies of the same commit.
    google = {
      source  = "hashicorp/google"
      version = "7.43.0"
    }

    # Identity Platform config and the Cloud Run service both take time to
    # become consistent after create. See identity_platform.tf.
    time = {
      source  = "hashicorp/time"
      version = "0.14.0"
    }
  }

  # Partial configuration, same shape as infra/estate: the bucket is supplied
  # at init time from the bootstrap output because it is deployment-specific
  # (POR-6) and this file is committed.
  #
  #   terraform init -backend-config="bucket=${TF_VAR_state_bucket_name}"
  backend "gcs" {
    prefix = "hosting"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  # billingbudgets and identitytoolkit both attribute quota to the calling
  # client project unless told otherwise, which surfaces as a SERVICE_DISABLED
  # 403 naming a project number that is not yours. infra/billing-guard hit this
  # exact failure (TD-7); it is set here pre-emptively rather than diagnosed
  # again.
  user_project_override = true
  billing_project       = var.project_id
}
