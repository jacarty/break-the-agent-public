# F5 and F7 — Firestore, in Native mode, with document-level Security Rules.
#
# Collections: `employees` (F5, team-owned records) and `agent_memory` (F7,
# per-persona memory, schemas.md §7). Neither is declared here — Firestore
# creates collections implicitly on first write, and the writer is the
# generator. The rules file is what actually defines the estate's shape.

locals {
  # `(default)` releases are named `cloud.firestore` with no suffix; named
  # databases get `cloud.firestore/<database>`. Derived from the variable
  # rather than from the resource so it is known at plan time.
  firestore_release_name = (
    var.firestore_database_id == "(default)"
    ? "cloud.firestore"
    : "cloud.firestore/${var.firestore_database_id}"
  )
}

resource "google_firestore_database" "orbit" {
  project     = var.project_id
  name        = var.firestore_database_id
  location_id = var.firestore_location

  # Native mode is required for Security Rules, and the choice is **permanent
  # per database** (spike 0.3). A database created in Datastore mode cannot be
  # converted; F5's L3 control would degrade to per-collection IAM, which is a
  # weaker demonstration that would then have to be labelled as such.
  type = "FIRESTORE_NATIVE"

  # NFR-6. Terraform otherwise refuses to delete a database holding documents,
  # and the generator will have written some.
  deletion_policy = "DELETE"

  depends_on = [google_project_service.estate]
}

resource "google_firebaserules_ruleset" "firestore" {
  project = var.project_id

  source {
    files {
      name    = "firestore.rules"
      content = file("${path.module}/firestore/firestore.rules")
    }
  }

  depends_on = [google_firestore_database.orbit]
}

# The release is a pointer: one name, repointed at a new ruleset on every rules
# change. Two things about it are not obvious and both broke the first apply.
#
# **The name normalises.** For the `(default)` database the release is called
# `cloud.firestore`, flat — not `cloud.firestore/(default)`. Only a named
# database gets the suffixed form. Writing the suffixed form for the default
# database resolves onto the same release and reads as a name collision.
#
# **It already exists before Terraform reaches it.** Creating a Firestore
# database causes Google to publish a default ruleset and release, so the
# create call fails with "Resource already exists - apply blocked by lifecycle
# params" on a project that has never been deployed to. See TD-8: adopting it
# currently needs a one-time `terraform import`, which NFR-5 does not allow for
# and which the next clean deploy will hit again.
#
# `replace_triggered_by` on the ruleset was removed with that. Replacing means
# destroy-then-create, and create is precisely the operation that cannot
# succeed here. Repointing is an in-place update, which is both what the API
# does and what the comment above always said was intended.
resource "google_firebaserules_release" "firestore" {
  project      = var.project_id
  name         = local.firestore_release_name
  ruleset_name = "projects/${var.project_id}/rulesets/${google_firebaserules_ruleset.firestore.name}"
}

# Rules propagation is slow enough to produce a false pass.
#
# Spike 0.3 finding 3: ten seconds after publishing, *every* read was denied,
# including the ones that should have succeeded. A test asserting only denials
# would have recorded a pass against rules that were not yet in effect — the
# same trap as spike 0.1 finding 2, arriving from a different direction.
#
# Nothing in this module can wait for propagation on the caller's behalf, and a
# sleep here would only move the guesswork. The control is in the test instead:
# every L3 denial assertion is paired with a positive case in the same run.
