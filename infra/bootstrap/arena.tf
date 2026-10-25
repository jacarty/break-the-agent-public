# The arena pool. A second, separate workload identity pool that federates
# personas — not CI — into the project (SEC-8).
#
# It lives here rather than in infra/estate/ because SEC-8 requires two pools,
# not two modules, and this module already carries the two things a pool needs:
# the propagation absorber (spike 0.1 finding 5) and the soft-delete naming
# discipline (finding 6). Duplicating both into estate/ invites them to drift
# apart, and a propagation delay that is right in one module and stale in the
# other is a flaky apply nobody attributes correctly.
#
# Estate consumes the `arena_pool_id` and `project_number` outputs and builds
# its own principalSet strings. No resource here knows what the estate contains.

resource "google_iam_workload_identity_pool" "arena" {
  project                   = var.project_id
  workload_identity_pool_id = var.arena_pool_id
  display_name              = "Arena personas"
  description               = "Federates Identity Platform personas into the lab for L3. Separate from the CI pool (SEC-8)."

  depends_on = [google_project_service.bootstrap]
}

resource "google_iam_workload_identity_pool_provider" "arena" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.arena.workload_identity_pool_id
  workload_identity_pool_provider_id = var.arena_provider_id
  display_name                       = "Identity Platform"

  oidc {
    # Identity Platform signs ID tokens for this project under its own issuer,
    # and the project ID is part of the issuer URI — so no separate input, and
    # no way to point this at another project's tokens by editing one variable.
    issuer_uri = "https://securetoken.google.com/${var.project_id}"

    # Identity Platform sets `aud` to the project ID. Without this the provider
    # would require its own full resource name as the audience, which no
    # Identity Platform token will ever carry, and every exchange fails on aud.
    allowed_audiences = [var.project_id]
  }

  # `attribute.team` is the whole L3 authorisation story: every estate binding
  # names a principalSet keyed on it, and BigQuery, Storage and Firestore reach
  # their conclusions from this claim alone (spike 0.1). Mapping it here is what
  # makes the claim addressable by IAM at all.
  attribute_mapping = {
    "google.subject" = "assertion.sub"
    "attribute.team" = "assertion.team"
  }

  # A token with no team claim maps to an empty attribute, and an empty
  # attribute would silently match a malformed principalSet rather than being
  # rejected. This turns that into a refusal at the exchange.
  #
  # It is deliberately not an allowlist of team names. Spike 0.1 finding 3
  # showed the grant attaches to the attribute rather than the person: an unseen
  # subject presenting `team-a` gets team-a's access. An allowlist here would
  # narrow nothing that matters — the teams are the only values ever minted —
  # while implying IAM validates who may claim a team. It does not. The persona
  # picker and the signing service are the root of trust (FR-3), and no
  # condition written on this provider can move that boundary.
  attribute_condition = "assertion.team != ''"
}

# Same eventual-consistency problem as the CI pool, one apply removed. Nothing
# in this module binds a role to an arena principalSet — estate does that, in a
# separate apply — so the wait is absorbed here, at creation, rather than left
# for estate to rediscover as an intermittent bad-request error.
#
# The pool ID is republished through `triggers` so that `arena_pool_id`
# genuinely depends on the wait having happened. An output reading the resource
# directly would be available the instant the pool is created, which is exactly
# the moment it cannot yet be named in a binding.
resource "time_sleep" "arena_pool_propagation" {
  depends_on = [google_iam_workload_identity_pool_provider.arena]

  create_duration = var.pool_propagation_delay

  triggers = {
    pool_id     = google_iam_workload_identity_pool.arena.workload_identity_pool_id
    provider_id = google_iam_workload_identity_pool_provider.arena.workload_identity_pool_provider_id
  }
}
