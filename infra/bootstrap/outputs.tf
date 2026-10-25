output "workload_identity_provider" {
  description = <<-EOT
    Full resource name of the OIDC provider. This is the value that goes into
    the GitHub Actions variable read by google-github-actions/auth. It names an
    identity provider; it does not authenticate as one (POR-6a).
  EOT
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "ci_principal_set" {
  description = "IAM member string for the federated CI principal. Grant roles to this in later modules."
  value       = local.ci_principal_set
}

output "arena_pool_id" {
  description = <<-EOT
    ID of the arena pool. Passed to infra/estate/ alongside `project_number`,
    which builds its own `attribute.team` principalSet strings from the two.
    Estate is given the identifiers rather than the resource so that it holds no
    dependency on this module's state.

    Read through the propagation wait, so consuming it in a binding cannot race
    the pool into existence.
  EOT
  value       = time_sleep.arena_pool_propagation.triggers["pool_id"]
}

output "arena_provider_id" {
  description = <<-EOT
    ID of the provider within the arena pool. Consumed by infra/hosting/, which
    passes it to the runtime as `BTA_ARENA_PROVIDER_ID`: `DeploymentConfig`
    derives the STS audience from the project number, the pool and the provider
    rather than being handed the assembled string, so that an operator cannot
    supply an audience that disagrees with the three values it is built from.

    Published for the same reason `arena_pool_id` is, and read through the same
    propagation wait.
  EOT
  value       = time_sleep.arena_pool_propagation.triggers["provider_id"]
}

output "arena_sts_audience" {
  description = <<-EOT
    Audience for the STS token exchange the site performs on a persona's
    Identity Platform ID token (spike 0.1, hop 4). Consumed by infra/hosting/ as
    runtime configuration. Names a provider; authenticates as nothing (POR-6a).
  EOT
  value       = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.arena.name}"
}

output "ci_deploy_service_account" {
  description = <<-EOT
    Email of the identity CI impersonates to apply infra/estate (TD-5). Goes
    into the GCP_CI_DEPLOY_SERVICE_ACCOUNT variable. Names an identity; holds
    no key and authenticates as nothing on its own (POR-6a).

    Empty when enable_ci_deploy_identity is false.
  EOT
  value       = try(google_service_account.ci_deploy[0].email, "")
}

output "registry_repository" {
  description = <<-EOT
    Host and path of the image repository, without a tag or digest. The build
    workflow pushes here; `infra/hosting`'s `image` variable takes the digest
    that push produced. Names a repository; authenticates as nothing (POR-6a).
  EOT
  value       = "${google_artifact_registry_repository.arena.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.arena.repository_id}"
}

output "state_bucket_name" {
  description = "Bucket holding Terraform state. Pass to every module via -backend-config=bucket=..."
  value       = google_storage_bucket.state.name
}

output "project_number" {
  description = "Project number, needed to construct principal identifiers by hand."
  value       = data.google_project.this.number
}

output "identity_platform_api_key" {
  description = <<-EOT
    The public browser API key, for `infra/hosting`'s `identity_platform_api_key`
    input and for `scripts/mint-persona-token.py`.

    Marked sensitive because the provider marks `key_string` sensitive, not
    because the value is secret — it is served to every visitor and SEC-10 is
    what makes that safe. Read it with `terraform output -raw`.
  EOT
  value       = google_apikeys_key.identity_platform.key_string
  sensitive   = true
}
