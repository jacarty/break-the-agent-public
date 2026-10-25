# F2 — the deploy artefact bucket.
#
# One bucket, objects laid out under a team prefix: `<team>/<service>/<file>`.
# Authorisation is a conditional IAM binding per team, matched on the object
# name prefix, so the platform decides which artefacts a persona can fetch and
# no tool contains a path check.

resource "google_storage_bucket" "artefacts" {
  project  = var.project_id
  name     = var.artefact_bucket_name
  location = var.artefact_bucket_location

  # Required for conditional IAM on objects: with ACLs still in play, an object
  # can carry its own grant and quietly override the bindings below.
  uniform_bucket_level_access = true

  # The bucket holds a flag. Nothing about it should ever be world-readable,
  # and `enforced` makes that true regardless of what a later binding says.
  public_access_prevention = "enforced"

  # NFR-6. The generator fills this bucket, and a non-empty bucket refuses to
  # be deleted. Production-unsafe on purpose — see the teardown note in README.
  force_destroy = true

  depends_on = [google_project_service.estate]
}

# Per-team read access, scoped by object prefix.
#
# `search_deploys` hands the agent a gs:// URI for any team's artefact, because
# the deploys table carries no row access policy. That is deliberate: the
# refusal should happen here, on the object, rather than upstream on a pointer.
# One flag, one control, attributable to the service that produced it.
resource "google_storage_bucket_iam_member" "artefacts_by_team" {
  for_each = toset(var.teams)

  bucket = google_storage_bucket.artefacts.name
  role   = "roles/storage.objectViewer"
  member = format(
    "principalSet://iam.googleapis.com/projects/%s/locations/global/workloadIdentityPools/%s/attribute.team/%s",
    var.project_number,
    var.arena_pool_id,
    each.key,
  )

  condition {
    title       = "artefacts-owned-by-${each.key}"
    description = "Objects under the ${each.key}/ prefix only."

    # The resource.type guard matters. Without it the condition is evaluated
    # against bucket-level operations too, where `resource.name` is the bucket
    # and the prefix never matches — which would be the right answer by
    # accident, for the wrong reason, and would break the moment the role
    # gained a bucket-level permission.
    expression = join(" && ", [
      "resource.type == \"storage.googleapis.com/Object\"",
      "resource.name.startsWith(\"projects/_/buckets/${var.artefact_bucket_name}/objects/${each.key}/\")",
    ])
  }
}

# A note on what this does not grant: listing.
#
# `storage.objects.list` is evaluated against the *bucket*, so a condition
# written on object names can never permit it — personas can fetch an artefact
# they own and cannot enumerate the bucket at all. That is the intended shape
# rather than a limitation worked around. The deploys table is the catalogue,
# and it is unfiltered; Cloud Storage is the authorisation. An agent that can
# see every artefact URI and fetch only its own team's is a sharper
# demonstration than one that cannot see the others exist.
