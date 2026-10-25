output "service_url" {
  value       = google_cloud_run_v2_service.arena.uri
  description = "The lab. Gated by the FR-1 access code, which FR-2 says is not a security boundary."
}

output "service_name" {
  value = google_cloud_run_v2_service.arena.name
}

output "runtime_service_account_email" {
  value       = var.runtime_service_account_email
  description = "Echoed so `scripts/verify-teardown.sh` and the SEC-11a test can name the identity without reading estate state."
}

# No flag values, no access code, no bypass token and no provider key is output
# from this module. `terraform output` lands in CI logs and in an operator's
# scrollback; POR-6a puts every one of those in Secret Manager precisely so
# that nothing has to be printed to be used.

output "evidence_bucket_name" {
  value       = google_storage_bucket.evidence.name
  description = "Where raw transcripts land (schemas.md §5). Never read by the suite."
}
