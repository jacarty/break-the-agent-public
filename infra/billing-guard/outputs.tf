output "budget_topic_id" {
  description = "Topic the budget publishes to. The rehearsal publishes here by hand."
  value       = google_pubsub_topic.budget_alerts.id
}

output "budget_topic_name" {
  description = "Short topic name, for `gcloud pubsub topics publish`."
  value       = google_pubsub_topic.budget_alerts.name
}

output "function_name" {
  description = "Function to read logs from when checking whether the guard fired."
  value       = google_cloudfunctions2_function.guard.name
}

output "guard_service_account" {
  description = "Identity holding roles/billing.projectManager."
  value       = google_service_account.guard.email
}

output "kill_switch_armed" {
  description = "False means the guard logs the decision and detaches nothing."
  value       = var.enable_kill_switch
}
