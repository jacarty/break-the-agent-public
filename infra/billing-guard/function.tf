resource "google_service_account" "guard" {
  project      = var.project_id
  account_id   = var.function_name
  display_name = "Billing guard (SEC-3)"
  description  = "Detaches ${var.project_id} from its billing account at the cap."
}

# Project-level, not billing-account-level, and that asymmetry is the point.
# The role carries exactly two permissions —
# `resourcemanager.projects.{create,delete}BillingAssignment` — and detaching is
# the one the guard uses. It also cannot re-attach in practice: creating an
# assignment additionally needs `billing.resourceAssociations.create` on the
# billing account, which nothing here grants. Re-enabling billing after the
# guard fires stays a deliberate human act with a credential this function does
# not hold.
resource "google_project_iam_member" "guard_billing" {
  project = var.project_id
  role    = "roles/billing.projectManager"
  member  = "serviceAccount:${google_service_account.guard.email}"
}

# `billing.projectManager` grants no *read*, and `main.py` reads before it
# writes. `GetProjectBillingInfo` needs `resourcemanager.projects.get`, which
# that role does not carry.
#
# The read is there for idempotence — re-checking `billing_enabled` rather than
# remembering — but the rehearsal showed that branch is close to unreachable:
# once billing is detached the project is read-denied and the function stops
# running, so the budget publishing past the cap reaches nothing. The read still
# earns its place by making the firing path *attributable* — acted, or found
# already acted — which is the distinction this project refuses to blur.
#
# **`roles/browser` rather than a one-permission custom role, and the reason is
# the deployer rather than the workload.** A custom role is tighter here — this
# needs exactly `resourcemanager.projects.get` and browser brings five more, all
# read-only. But creating one requires `iam.roles.get`/`create` on the project,
# which `ci_deploy_roles` does not grant, so the custom role could only be
# applied by adding `roles/iam.roleAdmin` to CI. That buys least privilege for a
# function's service account by granting CI the authority to mint and edit any
# role on the project — and CI's authority is both the more consequential of the
# two and the one whose changes leave no audit trail (TD-6).
#
# Found by CI run 32292027232 failing on `iam.roles.get` after the SEC-3
# rehearsal had already validated the custom-role form locally, where the
# operator holds owner. The rehearsal established which *permission* was
# missing; it could not establish which *grant* was affordable.
resource "google_project_iam_member" "guard_reader" {
  project = var.project_id
  role    = "roles/browser"
  member  = "serviceAccount:${google_service_account.guard.email}"
}

resource "google_storage_bucket" "source" {
  project                     = var.project_id
  name                        = var.source_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true

  depends_on = [google_project_service.guard]
}

data "archive_file" "source" {
  type        = "zip"
  source_dir  = "${path.module}/function"
  output_path = "${path.module}/.terraform/tmp/function.zip"
}

# Hash in the object name so a source change produces a new object, which is
# what makes the function redeploy. A fixed name uploads new bytes under the
# same URL and the function keeps serving the old build.
resource "google_storage_bucket_object" "source" {
  name   = "${var.function_name}-${data.archive_file.source.output_md5}.zip"
  bucket = google_storage_bucket.source.name
  source = data.archive_file.source.output_path
}

resource "google_cloudfunctions2_function" "guard" {
  project  = var.project_id
  name     = var.function_name
  location = var.region

  build_config {
    runtime     = "python313"
    entry_point = "handle_budget_alert"

    source {
      storage_source {
        bucket = google_storage_bucket.source.name
        object = google_storage_bucket_object.source.name
      }
    }
  }

  service_config {
    service_account_email = google_service_account.guard.email

    # One instance. The budget publishes frequently and the firing path is not
    # a race worth having: concurrent invocations would each read
    # `billing_enabled` before any of them wrote.
    max_instance_count = 1
    min_instance_count = 0
    available_memory   = "256M"
    timeout_seconds    = 60

    # Ingress is internal because the only legitimate caller is Eventarc.
    ingress_settings = "ALLOW_INTERNAL_ONLY"

    environment_variables = {
      GUARD_PROJECT_ID = var.project_id
      GUARD_ENABLED    = tostring(var.enable_kill_switch)
    }
  }

  event_trigger {
    trigger_region        = var.region
    event_type            = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic          = google_pubsub_topic.budget_alerts.id
    service_account_email = google_service_account.guard.email

    # A notification the function cannot parse is a notification it will never
    # parse. Retrying it forever would bury the real ones.
    retry_policy = "RETRY_POLICY_DO_NOT_RETRY"
  }

  depends_on = [
    google_project_service.guard,
    google_project_iam_member.guard_invoker,
    google_project_iam_member.guard_event_receiver,
    time_sleep.guard_identity,
  ]
}

# Deploying a gen2 function whose trigger runs as another identity makes
# Eventarc check that the deployer can `actAs` that identity. The check reads
# from a different cache than the one the binding was written to, so a service
# account created moments earlier fails validation with a 403 on
# `iam.serviceAccounts.ActAs` — while the IAM policy, read directly, is
# already correct. That is what makes this worth absorbing rather than
# debugging: the error names a permission the deployer demonstrably holds.
resource "time_sleep" "guard_identity" {
  depends_on = [
    google_service_account.guard,
    google_project_iam_member.guard_invoker,
    google_project_iam_member.guard_event_receiver,
    google_project_iam_member.guard_billing,
  ]

  create_duration = var.propagation_delay
}

# The trigger's identity is the function's own SA, so it needs to be allowed to
# invoke the service it fronts and to receive Eventarc events.
resource "google_project_iam_member" "guard_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.guard.email}"
}

resource "google_project_iam_member" "guard_event_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.guard.email}"
}
