# The single Cloud Run service — FR-1.
#
# One service exposes all three levels and both agents. FR-13's constant-agent
# guarantee is therefore structural rather than enforced by isolation: the same
# process serves an L1 session and an L3 one, and what differs between them is
# `bta.credentials.policy`'s twelve-entry table and nothing else.
#
# `infra/estate/README.md` records the alternative that was considered and
# declined — one service per level, which would make the guarantee enforced at
# the cost of three deployments and a claim that the levels are configured
# identically that nothing could check. The residual is named in build-plan.md
# §"Phase 2" and closed by a test rather than by architecture.

resource "google_cloud_run_v2_service" "arena" {
  project  = var.project_id
  name     = var.service_name
  location = var.region

  # The site is internet-reachable by design (an attacker needs to reach it)
  # and gated by the FR-1 access code, which FR-2 insists is not a security
  # boundary. Both statements are true at once and the README says so above the
  # quickstart (DIS-4).
  ingress = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    # SEC-11. The default compute service account reads the entire estate, as
    # measured in spike 0.4. Leaving this field blank is the single change that
    # would show L3 holding under normal operation while the service sits one
    # unauthenticated metadata-server request from total bypass — with nothing
    # in a passing run to reveal it. SEC-11a is the test that closes it.
    service_account = var.runtime_service_account_email

    # NFR-1 wants streaming to begin within 5s at p95. A cold start on a
    # container that imports three provider SDKs and the Google client
    # libraries does not meet that, so one instance stays warm.
    scaling {
      min_instance_count = var.min_instance_count
      max_instance_count = var.max_instance_count
    }

    # **Browser and suite sessions are per-instance, so requests must return to
    # the instance that holds them.** `bta.site.state` keeps gate tokens and
    # sessions in process memory (TD-24); without affinity a mid-session request
    # can reach a process that has never seen the cookie, which presents to a
    # person as being bounced to the picker and to the attack suite as a turn
    # that produced nothing.
    #
    # The suite now refuses to score that as a non-capture rather than trusting
    # this setting, because affinity is best-effort and not a guarantee. Both
    # halves are wanted: this makes the case rare, and the runner makes it loud
    # when it happens anyway.
    session_affinity = true

    # SSE holds a connection open for the length of a model turn (ADR 0007).
    volumes {
      name = "fixture-manifest"
      secret {
        secret = google_secret_manager_secret.fixture_manifest.secret_id
        items {
          # Pinned to the version this apply created, never "latest". See the
          # note above the secret env vars below — this is the reference that
          # made the verifier score a live estate against a previous seed.
          version = google_secret_manager_secret_version.fixture_manifest.version
          path    = "fixture-manifest.json"
          mode    = 256 # 0400 — read-only, owner only.
        }
      }
    }

    timeout = "600s"

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        # Streaming needs CPU between token batches, not only during request
        # handling. Throttling here would show up as stalled SSE rather than as
        # a slow response, which is the harder failure to diagnose.
        cpu_idle          = var.cpu_idle
        startup_cpu_boost = true
      }

      ports {
        container_port = 8080
      }

      # -------------------------------------------------------------------
      # Deployment inputs — POR-5, POR-6
      # -------------------------------------------------------------------
      #
      # Every one of these is a variable. Nothing about this deployment is
      # baked into the image, which is what lets the same digest run in
      # somebody else's project.

      # The fixture manifest — every flag value, `schemas.md` §3. A file rather
      # than an env var: `verifier.manifest.load` takes a path, and an env var
      # would be visible in the console and inherited by every child process.
      #
      # Without this `main()` exits 2 on `MissingConfiguration`, the startup
      # probe never passes, and no revision ever serves. The site needs it
      # because it scores its own captures in-process (FR-6).
      volume_mounts {
        name       = "fixture-manifest"
        mount_path = "/var/run/bta"
      }

      # TD-34. Absent on a deployment with no evidence store, in which case
      # the site still redacts and simply records no transcript_ref.
      env {
        name  = "BTA_EVIDENCE_BUCKET"
        value = google_storage_bucket.evidence.name
      }
      env {
        name  = "BTA_FIXTURE_MANIFEST"
        value = "/var/run/bta/fixture-manifest.json"
      }
      env {
        name  = "BTA_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "BTA_PROJECT_NUMBER"
        value = var.project_number
      }
      env {
        name  = "BTA_REGION"
        value = var.region
      }
      env {
        name  = "BTA_DATASET_ID"
        value = var.estate_dataset_id
      }
      env {
        name  = "BTA_OPS_DATASET_ID"
        value = var.estate_ops_dataset_id
      }
      env {
        name  = "BTA_ARTEFACT_BUCKET"
        value = var.artefact_bucket_name
      }
      env {
        name  = "BTA_FIRESTORE_DATABASE_ID"
        value = var.firestore_database_id
      }
      env {
        name  = "BTA_INTEGRATION_SECRET_ID"
        value = var.integration_secret_id
      }
      env {
        name  = "BTA_ARENA_POOL_ID"
        value = var.arena_pool_id
      }
      env {
        name  = "BTA_ARENA_PROVIDER_ID"
        value = var.arena_provider_id
      }
      env {
        name  = "BTA_RUNTIME_SERVICE_ACCOUNT"
        value = var.runtime_service_account_email
      }

      # Public by design (SEC-10). Passed as a plain env var rather than a
      # secret because treating a browser-served key as confidential teaches
      # the wrong thing about which values in this deployment actually are.
      env {
        name  = "BTA_IDENTITY_PLATFORM_API_KEY"
        value = var.identity_platform_api_key
      }
      env {
        name  = "BTA_SHARED_SERVICE_ACCOUNT"
        value = var.shared_service_account_email
      }
      env {
        name  = "BTA_PROVISIONING_SERVICE_ACCOUNT"
        value = var.provisioning_service_account_email
      }

      # FR-22a. Pinned, recorded per run, never inferred at runtime.
      env {
        name  = "BTA_SITE_PROVIDER"
        value = var.site_provider
      }
      env {
        name  = "BTA_MODEL_ANTHROPIC"
        value = var.model_anthropic
      }
      env {
        name  = "BTA_MODEL_XAI"
        value = var.model_xai
      }
      env {
        name  = "BTA_MODEL_GOOGLE"
        value = var.model_google
      }
      env {
        name  = "BTA_VERTEX_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BTA_XAI_VERTEX_LOCATION"
        value = var.xai_vertex_location
      }
      env {
        name  = "BTA_ANTHROPIC_VERTEX_LOCATION"
        value = var.anthropic_vertex_location
      }
      env {
        name  = "BTA_VERTEX_LOCATION"
        value = var.vertex_location
      }

      # NFR-3.
      env {
        name  = "BTA_RATE_LIMIT_PER_MINUTE"
        value = tostring(var.rate_limit_per_minute)
      }
      env {
        name  = "BTA_REQUESTS_PER_PERSONA_PER_DAY"
        value = tostring(var.requests_per_persona_per_day)
      }
      env {
        name  = "BTA_DAILY_TOKEN_CEILING"
        value = tostring(var.daily_token_ceiling)
      }

      # TD-27 — the gate's own limits. Separate counters from the NFR-3 three:
      # a gate attempt neither spends an interactive budget nor is refused by
      # the token ceiling.
      env {
        name  = "BTA_GATE_ATTEMPTS_PER_MINUTE"
        value = tostring(var.gate_attempts_per_minute)
      }
      env {
        name  = "BTA_GATE_ATTEMPTS_PER_HOUR"
        value = tostring(var.gate_attempts_per_hour)
      }
      env {
        name  = "BTA_GATE_ATTEMPTS_PER_HOUR_GLOBAL"
        value = tostring(var.gate_attempts_per_hour_global)
      }

      # -------------------------------------------------------------------
      # Secrets — injected by the platform, never fetched by the app
      # -------------------------------------------------------------------
      #
      # SEC-9's demonstration. The runtime never constructs a Secret Manager
      # client for its own provider keys; the platform mounts them. Application
      # code reads an environment variable and cannot reach the secret it came
      # from, which is the pattern the lab argues for.
      #
      # **Every reference below is pinned to a version, and "latest" is a bug
      # here rather than a convenience.** An earlier revision of this file
      # argued the opposite — that these rotate by operator action and a
      # revision should pick up the current one. That reasoning is wrong twice
      # and cost a debugging session on 2026-08-29.
      #
      # **It does not do what it appears to.** A secret reference resolves when
      # an *instance starts*, not when a version is written. Nothing in the
      # service spec changes when a new version appears, so Terraform creates
      # no new revision and the running instance keeps serving what it mounted.
      # `bta.site.__main__` compounds it by reading the manifest once into
      # memory at startup.
      #
      # **What that produced.** A reseed wrote fixture-manifest version 4 at
      # 13:01; the serving revision had been created at 12:50 holding version
      # 3. The estate had new flag values, the site scored against the old
      # ones, and a genuine L1 disclosure showed "Nothing captured yet". In a
      # suite run that is every execution recording `not_captured` and the
      # matrix reporting `held` across the board — evidence manufactured for
      # the exact claim the lab exists to test, which SC-4 would classify as
      # `absent` if anyone ever noticed.
      #
      # **And it is not even stable.** "latest" resolves per instance, so under
      # autoscaling two concurrent instances can mount different fixture
      # versions and score one run inconsistently.
      #
      # Pinning to the Terraform-managed version makes the revision a function
      # of the fixture content: a reseed changes the version, which changes the
      # spec, which forces a new revision that necessarily mounts what was
      # actually planted.

      # TD-13 / ADR 0003. `bta.config.markers_from_env` has no default and
      # `Session.__post_init__` refuses to start without C1's marker, so a
      # revision that failed to mount these does not serve a lab with the
      # control flag quietly removed — it does not serve at all.
      env {
        name = "BTA_MARKER_C1"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.marker_c1.secret_id
            version = google_secret_manager_secret_version.marker_c1.version
          }
        }
      }
      env {
        name = "BTA_MARKER_F6"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.marker_f6.secret_id
            version = google_secret_manager_secret_version.marker_f6.version
          }
        }
      }
      # F4. Hosting's own copy — see secrets.tf. Without it
      # `bta.levels.composition.render` raises for Onboarding at L1 and L2, so
      # the agent cannot be started at either level through the UI.
      env {
        name = "BTA_MARKER_F4"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.marker_f4.secret_id
            version = google_secret_manager_secret_version.marker_f4.version
          }
        }
      }

      env {
        name = "BTA_ACCESS_CODE"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.access_code.secret_id
            version = google_secret_manager_secret_version.access_code.version
          }
        }
      }
      env {
        name = "BTA_SUITE_BYPASS_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.suite_bypass_token.secret_id
            version = google_secret_manager_secret_version.suite_bypass_token.version
          }
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 12
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.runtime_reads,
    google_project_service.hosting,
    google_service_account_iam_member.runtime_signs_as_itself,
    time_sleep.identity_platform_propagation,
  ]
}

# FR-1: the service is reachable by anyone with the access code, so the gate is
# the application's, not IAM's. This binding is what makes that true — and it
# is the single most consequential line in the module, because it is what puts
# a deliberately vulnerable agent on the public internet.
#
# DIS-4 requires the README to carry that warning above the quickstart.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = google_cloud_run_v2_service.arena.location
  name     = google_cloud_run_v2_service.arena.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# The runtime identity pulls its own image, and nothing else in the registry.
# Reader rather than a project-level grant, for the same reason the secret
# bindings are per-secret (SEC-11): the container the service runs is the one
# thing it must be able to fetch and the one thing it must never replace.
resource "google_artifact_registry_repository_iam_member" "runtime_pulls" {
  project    = var.project_id
  location   = var.region
  repository = var.registry_id
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${var.runtime_service_account_email}"

  depends_on = [google_project_service.hosting]
}

# The runtime identity calls Vertex, which is now every model the lab uses:
# Gemini natively and Claude as a partner model (FR-22). This is the grant that
# replaced two Secret Manager entries — the lab holds no provider key at all.
#
# **This is the one broad grant the runtime holds, and the reason it is safe is
# SEC-11 rather than the role's own scope.** `roles/aiplatform.user` is
# project-level because Vertex publisher models are not resources you can bind
# a member to individually; there is no per-model equivalent of the per-secret
# bindings above. What keeps it from mattering is that the estate holds nothing
# in Vertex: no dataset, no index, no endpoint, no tuned model. The role grants
# reach over an empty surface, and SEC-11a's startup probe asserts the runtime
# cannot touch the surfaces that are not empty.
#
# If the estate ever gains a Vertex resource — an embedding index for a tool, a
# tuned model — this grant stops being harmless and has to be narrowed before
# that lands, because an L3 denial the runtime could route around in Vertex is
# an L3 column that means nothing.
resource "google_project_iam_member" "runtime_calls_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${var.runtime_service_account_email}"

  depends_on = [google_project_service.hosting]
}
