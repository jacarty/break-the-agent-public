# Only the APIs the trust and the state bucket need. Estate and hosting enable
# their own: an api list that accumulates every service the project will ever
# use puts the blast radius of `terraform destroy` in the wrong module.

# **Adding or removing anything in `bootstrap_apis` has a cost that is invisible
# from this file.** `data.google_project.this` (wif.tf) depends on
# `google_project_service.bootstrap`, so a change here defers that data source
# to apply time and `.number` becomes unknown at plan. Everything built from it
# then plans a replacement: the two `ci-deploy@` bindings (TD-9, recoverable)
# and `google_apikeys_key.identity_platform` (identity_platform_key.tf, whose
# replacement locks its name for 30 days).
#
# Collapse the unknown first, then plan:
#
#     terraform apply -target=google_project_service.bootstrap
#
locals {
  # iamcredentials is deliberately absent. Direct workload identity federation
  # goes through STS alone; iamcredentials is the impersonation and signJwt API,
  # which the arena's token minting needs (spike 0.1, hop 1) and this trust does
  # not. It belongs to whichever module mints persona tokens.
  bootstrap_apis = [
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "sts.googleapis.com",
    "storage.googleapis.com",

    # The image repository lives here — see registry.tf.
    "artifactregistry.googleapis.com",

    # The Identity Platform browser key lives here too — see
    # identity_platform_key.tf. `identitytoolkit` itself stays in
    # infra/hosting: this module creates the key, it does not configure the
    # tenant the key talks to.
    "apikeys.googleapis.com",

    # Not needed by the trust or the bucket, and here anyway. Cloud Asset
    # Inventory is the backstop enumerator in `scripts/verify-teardown.sh` —
    # the only one that can find a resource type nobody wrote a function for.
    # A surface the check cannot read fails the run, so with this API off every
    # teardown reports `NFR-6 NOT verified` however clean the project is, which
    # is what the reference project's first teardown did after its orphans
    # were resolved.
    #
    # It belongs in this module rather than the estate for the same reason the
    # pools do: it has to be on *before* the baseline is captured and still on
    # *after* the estate is destroyed, and only bootstrap spans both.
    "cloudasset.googleapis.com",

    # Cloud Quotas, for the partner-model throughput this project has to ask
    # for rather than have. Vertex meters Anthropic and xAI per base model per
    # region, a new project starts at zero on both, and FR-22 needs all three
    # providers serving before any cell can reach a verdict other than
    # `insufficient` — so a quota preference is part of standing the lab up,
    # not an operator errand beside it.
    #
    # Here rather than in the estate for the same reason as cloudasset: a
    # quota preference is a project-level request that outlives every
    # `terraform destroy` and has to survive a torn-down estate, and bootstrap
    # is the only module that spans both. Reads work without it — they bill to
    # the caller's own quota project — so the API is needed for
    # `quotaPreferences.create`, which is where it was first missed.
    "cloudquotas.googleapis.com",
  ]
}

resource "google_project_service" "bootstrap" {
  for_each = toset(local.bootstrap_apis)

  project = var.project_id
  service = each.value

  # Leave the APIs enabled on destroy. Disabling them would break any resource
  # in another module that still depends on them, and re-enabling is free.
  disable_on_destroy = false
}
