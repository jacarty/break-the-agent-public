# Where the site's container image lives.
#
# **In bootstrap rather than hosting, and the ordering is the reason.**
# `infra/hosting/variables.tf` requires `var.image` to be digest-pinned, so an
# image must already be pushed before hosting can be applied at all. A registry
# created by hosting could not be pushed to until hosting existed, which is the
# wrong way round. It also has to survive `terraform destroy` of the estate and
# the site — the lab is torn down and rebuilt repeatedly (NFR-6), and rebuilding
# the image every time to redeploy an unchanged commit would make a teardown
# destructive to something it has no business touching.
#
# It is the same reasoning that puts the state bucket here.

resource "google_artifact_registry_repository" "arena" {
  project       = var.project_id
  location      = var.region
  repository_id = var.registry_id
  format        = "DOCKER"
  description   = "Container images for the arena site. Built by .github/workflows/image.yml."

  # Keep the last few builds and no more. Images are large, the budget is
  # capped (SEC-2), and an image older than the commit that produced it is not
  # evidence of anything — SC-6 reproducibility rests on the digest recorded
  # with the run, and on the Dockerfile in git, not on a registry's history.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  depends_on = [google_project_service.bootstrap]
}

# The CI identity pushes images. Writer, not Admin: pushing a tag is the whole
# job, and deleting a repository is not something a workflow should be able to
# do by accident.
resource "google_artifact_registry_repository_iam_member" "ci_pushes" {
  count = var.enable_ci_deploy_identity ? 1 : 0

  project    = var.project_id
  location   = google_artifact_registry_repository.arena.location
  repository = google_artifact_registry_repository.arena.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.ci_deploy[0].email}"
}
