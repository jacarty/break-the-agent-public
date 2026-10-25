# Deployment secrets, and the two flag markers that have no other delivery
# channel. Closes the Terraform half of TD-13 and TD-22.
#
# Everything here is created by Terraform and read by the runtime through the
# platform — mounted as an environment variable from a secret version, never
# fetched by application code holding a client. SEC-9's claim is that a lab
# arguing agents should not hold secrets demonstrates the pattern in its own
# plumbing, and a runtime that called Secret Manager for its own provider key
# would be holding the credential it says it does not.

locals {
  # One place, so a secret cannot be created with replication settings that
  # differ from its neighbours by accident.
  replication_regions = [var.region]
}

# There are deliberately no provider API keys here at all, and this file used
# to hold two. FR-22 now serves *every* model through Vertex AI under IAM —
# Gemini natively, Claude as a partner model — so the lab holds provider key
# material for no provider. Their absence is the requirement, not an oversight,
# and it is the strongest form of the argument the lab makes about agents and
# secrets: a lab saying agents should not hold secrets, holding none itself.

# ---------------------------------------------------------------------------
# Fixture markers — TD-13, ADR 0003
# ---------------------------------------------------------------------------
#
# These hold live flag values. They are the only flag values in the hosting
# layer, they are never output, and `terraform show` on this state exposes them
# exactly as it exposes any other secret — which is why POR-6a puts the state
# bucket under restricted IAM.

resource "google_secret_manager_secret" "fixture_manifest" {
  project   = var.project_id
  secret_id = "${var.service_name}-fixture-manifest"

  replication {
    user_managed {
      dynamic "replicas" {
        for_each = local.replication_regions
        content {
          location = replicas.value
        }
      }
    }
  }

  depends_on = [google_project_service.hosting]
}

resource "google_secret_manager_secret_version" "fixture_manifest" {
  secret      = google_secret_manager_secret.fixture_manifest.id
  secret_data = var.fixture_manifest
}

resource "google_secret_manager_secret" "marker_c1" {
  project   = var.project_id
  secret_id = "${var.service_name}-marker-c1"

  replication {
    user_managed {
      dynamic "replicas" {
        for_each = local.replication_regions
        content {
          location = replicas.value
        }
      }
    }
  }

  depends_on = [google_project_service.hosting]
}

resource "google_secret_manager_secret_version" "marker_c1" {
  secret      = google_secret_manager_secret.marker_c1.id
  secret_data = var.marker_c1
}

resource "google_secret_manager_secret" "marker_f6" {
  project   = var.project_id
  secret_id = "${var.service_name}-marker-f6"

  replication {
    user_managed {
      dynamic "replicas" {
        for_each = local.replication_regions
        content {
          location = replicas.value
        }
      }
    }
  }

  depends_on = [google_project_service.hosting]
}

resource "google_secret_manager_secret_version" "marker_f6" {
  secret      = google_secret_manager_secret.marker_f6.id
  secret_data = var.marker_f6
}

# ---------------------------------------------------------------------------
# The gate and the bypass — FR-1, NFR-3a
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "marker_f4" {
  project   = var.project_id
  secret_id = "${var.service_name}-marker-f4"

  replication {
    user_managed {
      dynamic "replicas" {
        for_each = local.replication_regions
        content {
          location = replicas.value
        }
      }
    }
  }

  depends_on = [google_project_service.hosting]
}

resource "google_secret_manager_secret_version" "marker_f4" {
  secret      = google_secret_manager_secret.marker_f4.id
  secret_data = var.marker_f4
}

resource "google_secret_manager_secret" "access_code" {
  project   = var.project_id
  secret_id = "${var.service_name}-access-code"

  replication {
    user_managed {
      dynamic "replicas" {
        for_each = local.replication_regions
        content {
          location = replicas.value
        }
      }
    }
  }

  depends_on = [google_project_service.hosting]
}

resource "google_secret_manager_secret_version" "access_code" {
  secret      = google_secret_manager_secret.access_code.id
  secret_data = var.access_code
}

resource "google_secret_manager_secret" "suite_bypass_token" {
  project   = var.project_id
  secret_id = "${var.service_name}-suite-bypass-token"

  replication {
    user_managed {
      dynamic "replicas" {
        for_each = local.replication_regions
        content {
          location = replicas.value
        }
      }
    }
  }

  depends_on = [google_project_service.hosting]
}

resource "google_secret_manager_secret_version" "suite_bypass_token" {
  secret      = google_secret_manager_secret.suite_bypass_token.id
  secret_data = var.suite_bypass_token
}

# ---------------------------------------------------------------------------
# The grants — narrowly, per secret
# ---------------------------------------------------------------------------
#
# `roles/secretmanager.secretAccessor` on each secret individually rather than
# at project level. A project-level grant would give the runtime identity
# access to the estate's integration secret — which is F4, the thing L3's
# Onboarding defence exists to keep away from it. SEC-11 says the runtime holds
# no estate access, and a convenient project-level binding here is exactly how
# that gets violated without anyone editing infra/estate.

locals {
  runtime_readable_secrets = {
    marker_c1 = google_secret_manager_secret.marker_c1.id
    marker_f6 = google_secret_manager_secret.marker_f6.id
    # A copy of F4 owned by *hosting*, not a grant on the estate's secret. The
    # estate's binding stays restricted to the owning team's principalSet, so
    # L3's Onboarding defence is unchanged and an L3 denial still happens in
    # Secret Manager rather than in the agent.
    marker_f4 = google_secret_manager_secret.marker_f4.id
    # Every flag value in one object. Mounted because the site scores its own
    # captures in-process (FR-6); nothing else in the runtime may read it, and
    # `schemas.md` §3 keeps the set of components that can to two.
    fixture_manifest   = google_secret_manager_secret.fixture_manifest.id
    access_code        = google_secret_manager_secret.access_code.id
    suite_bypass_token = google_secret_manager_secret.suite_bypass_token.id
  }
}

resource "google_secret_manager_secret_iam_member" "runtime_reads" {
  for_each = local.runtime_readable_secrets

  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.runtime_service_account_email}"
}
