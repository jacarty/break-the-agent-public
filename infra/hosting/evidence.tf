# The evidence bucket — VER-6, `schemas.md` §5, TD-34.
#
# Raw transcripts land here and nowhere else. `schemas.md` §5 gives every run
# result a `transcript_ref` and a `redacted_transcript_ref` and says both are
# written on every run; before this nothing wrote either, so a reference run
# produced capture counts and no evidence at all.
#
# **Hosting owns this, not the estate, and that is not filing convenience.**
# SEC-11 says the runtime service account holds no *estate* access, and this
# bucket is not the estate — it is the service's own output. Putting it in
# `infra/estate/` would mean granting the runtime identity a write role inside
# the estate, which is exactly the access L3 exists to withhold.
#
# **A raw transcript contains live flag values.** A `tool_result` capture *is* a
# value in a payload, so this bucket is at the same sensitivity as the fixture
# manifest: never public, never committed, never read by the suite. The suite
# records the `gs://` ref and fetches nothing — it may not hold a value, because
# SC-6 commits run results to git while VER-6 keeps raw transcripts out of it.
# The redacted copy travels inline instead and is written beside the results.

resource "google_storage_bucket" "evidence" {
  project  = var.project_id
  name     = var.evidence_bucket_name
  location = var.evidence_bucket_location

  uniform_bucket_level_access = true

  # It holds flag values in transcript form. Nothing here is ever world
  # readable, and `enforced` keeps that true regardless of a later binding.
  public_access_prevention = "enforced"

  # NFR-6. A run fills this bucket and a non-empty bucket refuses to be
  # deleted, so teardown would leave the one resource that carries values.
  # Production-unsafe on purpose, for the same reason the estate's is.
  force_destroy = true

  # Evidence for one reference run, not an archive. A published matrix cites
  # the redacted copy in git; the raw copy exists to investigate a defeated
  # cell (SC-4) while the run is current, and outliving that is a liability
  # rather than a feature.
  lifecycle_rule {
    condition {
      age = var.evidence_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

# The runtime writes; nothing reads but an operator investigating a defeat.
#
# `objectCreator` and not `objectAdmin`: the site writes an object per session
# and never needs to read one back, and a runtime that could read this bucket
# could read another persona's transcript — which is the containment the whole
# L3 column argues for, applied to the lab's own evidence.
resource "google_storage_bucket_iam_member" "runtime_writes_evidence" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${var.runtime_service_account_email}"
}
