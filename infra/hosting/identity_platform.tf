# Identity Platform — FR-3, SEC-10.
#
# The persona picker mints a **custom token** server-side for the selected
# persona and exchanges it for an ID token carrying a `team` claim. That token
# is what spike 0.1 established the whole L3 credential chain on, and what
# TD-11's harness has been waiting for.
#
# ---------------------------------------------------------------------------
# SEC-10: no self-service sign-in provider. None. Not one.
# ---------------------------------------------------------------------------
#
# The public API key Identity Platform issues is **not a secret** — it is served
# to browsers and must be assumed known. With any self-service provider enabled
# it becomes an open registration endpoint on an internet-reachable deployment
# hosting deliberately vulnerable agents. With none enabled the key alone
# yields nothing, because minting a custom token needs signing material only
# Google holds.
#
# Anonymous sign-in is called out explicitly in SEC-10 because it is one console
# toggle away, commonly enabled while debugging, and silently permanent once
# forgotten. The `sign_in` block below disables it and email/password
# explicitly rather than by omission — an absent block is a default, and a
# default is not a decision.
#
# The blocking-function pattern (`beforeCreate` rejecting non-allowlisted
# addresses) is the right control for a deployment that genuinely needs
# self-service. This one does not, and adding a Cloud Function to make an
# unwanted capability safe is the wrong trade.

# **Adopted, not created.** Identity Platform is already initialised on every
# project this module will ever be applied to, because `infra/estate/` creates
# the Firestore database and that brings a Firebase project with it, which
# initialises identitytoolkit. `docs/first-deploy.md` mandates estate before
# hosting, so a create always fails with
# `INVALID_PROJECT_ID : Identity Platform has already been enabled` — which is
# exactly what the first real apply hit.
#
# The import block makes the adoption part of the configuration rather than a
# step an operator has to know about. It is correct *because* of that ordering
# guarantee: on a project where hosting somehow ran first there would be
# nothing to import and this would fail instead. That is the trade, and it is
# the right way round — the documented order is the supported one.
import {
  to = google_identity_platform_config.arena
  id = var.project_id
}

resource "google_identity_platform_config" "arena" {
  project = var.project_id

  # Personas are created by an administrative credential, never by a caller.
  autodelete_anonymous_users = false

  sign_in {
    allow_duplicate_emails = false

    anonymous {
      enabled = false
    }

    email {
      enabled           = false
      password_required = false
    }

    phone_number {
      enabled = false
    }
  }

  depends_on = [google_project_service.hosting]
}

# Identity Platform config is eventually consistent: a custom-token exchange
# issued immediately after this resource returns can be refused by a config
# that has not propagated. The same pattern infra/estate uses for IAM and
# infra/bootstrap uses for pool propagation, for the same reason and with the
# same honesty about it being an absorber rather than a fix.
resource "time_sleep" "identity_platform_propagation" {
  depends_on      = [google_identity_platform_config.arena]
  create_duration = "60s"
}

# ---------------------------------------------------------------------------
# What is NOT here
# ---------------------------------------------------------------------------
#
# The persona records themselves. `alice` on `team-a`, `bob` on `team-b` and
# `svc-platform` on `platform` (PRD §3) are created by the site at startup
# through the Admin SDK, not by Terraform, because the `team` custom claim is
# set with the same call that creates the user and Terraform has no resource
# that sets custom claims. Creating them here and claiming them there would
# leave two sources of truth for which personas exist.
#
# The consequence to know: a persona that fails to be created at startup is a
# persona the picker cannot offer, and the site refuses to start rather than
# serving a picker with a missing entry. That is the same fail-closed rule
# ADR 0003 applies to the C1 marker.

# ---------------------------------------------------------------------------
# Keyless custom-token signing — SEC-5
# ---------------------------------------------------------------------------
#
# Minting an Identity Platform custom token conventionally means signing a JWT
# with a downloaded service account private key. SEC-5 forbids long-lived
# service account keys anywhere in the deployment, so the runtime signs through
# the IAM Credentials API as itself instead — the one capability spike 0.4
# established it always has.
#
# This grant lets the runtime identity sign assertions **as itself**. It grants
# no estate access and SEC-11 is unaffected: a JWT saying `sub: alice` is worth
# exactly what alice is worth, which at L3 is one team's data. What it must not
# become is a grant on the *shared* or *provisioning* accounts, which would let
# the runtime mint tokens for the identities that do hold estate access —
# `infra/estate/identity.tf` already grants impersonation on those two, and
# this is deliberately scoped to `runtime` alone.
resource "google_service_account_iam_member" "runtime_signs_as_itself" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.runtime_service_account_email}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${var.runtime_service_account_email}"
}

# ---------------------------------------------------------------------------
# The browser API key — not a secret, and SEC-10 is why that is safe
# ---------------------------------------------------------------------------
#
# **The browser API key is not here. It is in `infra/bootstrap`.**
#
# Identity Toolkit's `signInWithCustomToken` needs the project's public API key
# and this module consumes it as `var.identity_platform_api_key`, wired to the
# runtime in cloudrun.tf. It used to be created here, and moving it was not a
# tidy-up.
#
# Destroying an API key soft-deletes it and locks the key id for 30 days. The
# id is derived from the service name, so it is the same string every cycle —
# and `teardown.yml` destroys this module on every cycle, as NFR-6 requires it
# to. A key created here is therefore a key that cannot be recreated for a
# month after the first teardown, which makes the lab undeployable for reasons
# that have nothing to do with the lab.
#
# `infra/bootstrap/identity_platform_key.tf` carries the full reasoning, next
# to the resource. This module keeps the tenant configuration above, which is
# destroyed and recreated freely, and holds no resource whose destroy is
# irreversible.
