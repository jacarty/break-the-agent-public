# The public browser API key Identity Toolkit's `signInWithCustomToken`
# requires (SEC-10, FR-3).
#
# ── Why it is here and not in infra/hosting ──────────────────────────────────
#
# It lived in `infra/hosting/identity_platform.tf` until the first teardown
# cycle made the consequence obvious. **Destroying an API key soft-deletes it
# and locks the key id for 30 days.** The id here is derived from the service
# name, so it is the same string on every deploy — which means a
# `terraform destroy` of hosting burns the only name the next apply can use,
# and the lab cannot be redeployed for a month.
#
# That is not a hypothetical ordering problem. `teardown.yml` destroys hosting
# on every cycle, and NFR-6 requires it to. The resource had to move to the one
# module that survives a teardown.
#
# It is the same argument that already put the image registry and the state
# bucket here: a thing hosting needs to *exist before it applies*, and needs to
# *outlive its destroy*, does not belong to hosting. `registry.tf` states the
# ordering half of it; this file is the irreversibility half.
#
# ── What it is worth ─────────────────────────────────────────────────────────
#
# Nothing on its own, and that is a design property rather than a hope. The key
# is served to every browser that loads the persona picker and must be assumed
# known. SEC-10 is what makes that safe: the tenant enables no self-service
# sign-in provider, so the key cannot register an identity, and minting a
# custom token needs signing material only Google holds. The restriction below
# is the second layer — the key may call identitytoolkit and nothing else, so a
# leaked key cannot be turned on any other API enabled in the project.
#
# Creating it explicitly, rather than reusing the key Firebase auto-provisions,
# exists to make that restriction a declared thing rather than a default.

resource "google_apikeys_key" "identity_platform" {
  # **The project *number*, not the id, and the difference is destructive.**
  # `project` is ForceNew here, and the API returns the number. A fresh create
  # keeps whatever the config said and never compares, so `var.project_id`
  # looks correct until something reads the resource back — an import, or a
  # refresh after a state loss — at which point Terraform sees a diff from the
  # number to the id and plans a replacement.
  #
  # A replacement cannot succeed, for the reason at the top of this file: the
  # create that follows the destroy collides with the name the destroy just
  # locked. The loop is unwinnable and each attempt burns another 30-day
  # window, which is how this was found.
  #
  # **A second, unrelated cause produces the identical plan line, and this is
  # the one to remember before editing `apis.tf`.** `data.google_project.this`
  # carries `depends_on = [google_project_service.bootstrap]`, so *any* change
  # to `local.bootstrap_apis` defers that data source to apply time and makes
  # `.number` unknown at plan. An unknown on a ForceNew attribute plans a
  # replacement even when the config is perfectly correct.
  #
  # TD-9 recorded this for the two `ci-deploy@` bindings, where the cost is a
  # rewritten binding. Here the cost is 30 days. Mitigation is a targeted apply
  # that collapses the unknown first:
  #
  #     terraform apply -target=google_project_service.bootstrap
  #
  # It is written here rather than only in `docs/tech-debt.md` because the
  # person who needs it is the one adding a service to `apis.tf`, and that file
  # gives no reason to think this resource is involved.
  project      = data.google_project.this.number
  name         = "${var.arena_service_name}-identity-platform"
  display_name = "Identity Platform browser key (public by design)"

  restrictions {
    api_targets {
      service = "identitytoolkit.googleapis.com"
    }
  }

  # **A failed plan is a better outcome than a 30-day outage.** Everything
  # above explains how a replacement gets proposed by accident — an unknown
  # from a service-map edit, or a `project` set to the id rather than the
  # number — and neither is prevented by care. This converts both into a
  # refusal at plan time.
  #
  # ADR 0012 rejected `prevent_destroy` for `infra/hosting`, where NFR-6
  # requires the module to be destroyable and a block would make that claim
  # false. The argument does not carry here: bootstrap deliberately survives a
  # teardown and is already outside NFR-6's verified scope (TD-1), and
  # `state.tf` sets the precedent on the state bucket for the same reason.
  #
  # To retire the key deliberately, remove this block in its own commit.
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.bootstrap]
}
