output "dataset_id" {
  description = "Estate dataset. The generator writes rows into it; the Deploy Bot tools read from it."
  value       = google_bigquery_dataset.orbit.dataset_id
}

output "ops_dataset_id" {
  description = "Cloud Logging sink destination dataset. Operator-facing; no tool reads it."
  value       = google_bigquery_dataset.ops.dataset_id
}

output "table_ids" {
  description = "Tables created in the estate dataset, by name. The generator plants into these."
  value       = { for name, table in google_bigquery_table.estate : name => table.table_id }
}

output "artefact_bucket_name" {
  description = "Deploy artefact bucket. The generator writes objects under a `<team>/` prefix; F2 is one of them."
  value       = google_storage_bucket.artefacts.name
}

output "firestore_database_id" {
  description = "Firestore database holding the employees and agent_memory collections."
  value       = google_firestore_database.orbit.name
}

output "runtime_service_account_email" {
  description = <<-EOT
    Cloud Run runtime identity. Deploy with this set explicitly (SEC-11) — a
    deployment that leaves the field blank gets the default compute account,
    which spike 0.4 measured reading the entire estate.

    It holds no estate access. If a change to this module ever gives it some,
    the L3 column of the matrix stops meaning anything.
  EOT
  value       = google_service_account.runtime.email
}

output "shared_service_account_email" {
  description = <<-EOT
    The L1/L2 shared estate credential, impersonated by the runtime. Read
    through the IAM propagation wait, so a generator run that follows this
    apply does not race the bindings.
  EOT
  value       = time_sleep.iam_propagation.triggers["shared_service_account_email"]
}

output "provisioning_service_account_email" {
  description = "Identity of the provisioning service that owns the integration secret at L3."
  value       = google_service_account.provisioning.email
}

output "integration_secret_id" {
  description = "Secret holding the integration API key. Created empty — the generator writes the version (F4)."
  value       = google_secret_manager_secret.integration_key.secret_id
}
