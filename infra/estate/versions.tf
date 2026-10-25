terraform {
  required_version = ">= 1.9"

  required_providers {
    # Pinned exactly, matching infra/bootstrap. A provider bump is a reviewed
    # change, not something that happens between two applies of the same
    # commit — which matters more here than there, because this module's
    # output is compared byte-for-byte across applies.
    google = {
      source  = "hashicorp/google"
      version = "7.43.0"
    }

    # Used only to absorb IAM propagation delay. See identity.tf.
    time = {
      source  = "hashicorp/time"
      version = "0.14.0"
    }
  }

  # Partial configuration: the bucket is supplied at init time from the
  # bootstrap output, because it is deployment-specific (POR-6) and this file
  # is committed. Unlike bootstrap, estate has no chicken-and-egg problem — the
  # bucket exists before this module is ever applied — so the backend block is
  # committed rather than shipped as a .migrate template.
  #
  #   terraform init -backend-config="bucket=${TF_VAR_state_bucket_name}"
  backend "gcs" {
    prefix = "estate"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
