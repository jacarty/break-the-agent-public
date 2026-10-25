# Every value here is an input (POR-6). Nothing in this module names a project,
# a region, a bucket, a service URL or a model. Documentation shows placeholders.

variable "project_id" {
  type        = string
  description = "Deployment project. Disposable, separate billing account, hard budget cap (SEC-2)."
}

variable "region" {
  type        = string
  description = "Cloud Run region. Must be a region where the pinned Vertex models are available (OD-8)."
}

variable "project_number" {
  type        = string
  description = "From infra/bootstrap (GCP_PROJECT_NUMBER). Half of the STS audience the arena pool is addressed by; the runtime derives that audience rather than being handed it (src/bta/config.py)."
}

# ---------------------------------------------------------------------------
# Estate coupling — one direction only
# ---------------------------------------------------------------------------
#
# POR-4 requires the estate to have no coupling to hosting. The reverse is
# allowed and is what these are: hosting consumes `terraform output` from
# infra/estate. Nothing in infra/estate reads anything from here, which is what
# lets the estate be reseeded without touching the site.

variable "runtime_service_account_email" {
  type        = string
  description = "From infra/estate. Cloud Run runs as this. Holds no estate access (SEC-11)."
}

variable "arena_pool_id" {
  type        = string
  description = "From infra/bootstrap. The arena workload identity pool — separate from CI's (SEC-8)."
}

variable "arena_provider_id" {
  type        = string
  description = "From infra/bootstrap. The provider within the arena pool. The runtime needs it to build the STS audience for an L3 exchange; without it every L3 exchange fails on an audience mismatch."
}

variable "estate_dataset_id" {
  type        = string
  description = "From infra/estate. Passed to the runtime as a deployment input, never hardcoded."
}

variable "estate_ops_dataset_id" {
  type        = string
  description = "From infra/estate."
}

variable "artefact_bucket_name" {
  type        = string
  description = "From infra/estate."
}

variable "firestore_database_id" {
  type        = string
  description = "From infra/estate."
}

variable "integration_secret_id" {
  type        = string
  description = "From infra/estate. Read by the provisioning identity at L3, never by the tool."
}

variable "shared_service_account_email" {
  type        = string
  description = "From infra/estate. Impersonated for the L1/L2 shared credential mode."
}

variable "provisioning_service_account_email" {
  type        = string
  description = "From infra/estate. Impersonated for the L3 `service` credential mode."
}

# ---------------------------------------------------------------------------
# The container
# ---------------------------------------------------------------------------

variable "registry_id" {
  type        = string
  description = "From infra/bootstrap. The Artifact Registry repository holding `var.image`, so the runtime identity can be granted read on that repository alone."
}

variable "image" {
  type        = string
  description = "Fully qualified container image, digest-pinned. A tag would let two applies of one commit deploy different code, which NFR-5 and SC-6 both rely on not happening."

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.image))
    error_message = "image must be digest-pinned (…@sha256:…). A mutable tag breaks the reproducibility SC-6 promises a deployer."
  }
}

# ---------------------------------------------------------------------------
# Deployment secrets — FR-1, NFR-3a
# ---------------------------------------------------------------------------

variable "access_code" {
  type        = string
  sensitive   = true
  description = "FR-1's shared-secret gate. NOT authentication (FR-2) — it exists to stop drive-by token consumption on an internet-reachable service, and is never described as a security boundary."

  validation {
    condition     = length(var.access_code) >= 16
    error_message = "access_code must be at least 16 characters. It is the only thing between the open internet and a deliberately vulnerable agent with a token budget."
  }
}

variable "suite_bypass_token" {
  type        = string
  sensitive   = true
  description = "NFR-3a. Exempts the attack suite from per-persona rate limits, never from the daily token ceiling."

  validation {
    condition     = length(var.suite_bypass_token) >= 32
    error_message = "suite_bypass_token must be at least 32 characters. It removes the only rate control on this service."
  }
}

# ---------------------------------------------------------------------------
# Fixture markers — TD-13
# ---------------------------------------------------------------------------
#
# C1 and F6 are not cloud resources: C1 is text in a tool description and F6 is
# a write-path outcome. Both carry fixture-manifest entries, and ADR 0003 chose
# platform injection as the delivery channel. These are the Terraform half that
# ADR left open.
#
# The operator supplies them from the manifest the generator wrote. They are
# flag values and are handled as such: `sensitive`, never logged, never
# defaulted.

variable "fixture_manifest" {
  type        = string
  sensitive   = true
  description = <<-EOT
    The fixture manifest as JSON (`schemas.md` §3), from the generator. Passed
    as a value rather than a path so Terraform never reads a file the operator
    might have committed by accident.

    This is every flag value at once and it is mounted into the runtime, which
    is the most concentrated secret in the deployment. It is here because the
    site scores its own captures in-process (FR-6): the verifier is the only
    component permitted to hold values, and on this deployment it runs inside
    the site. Delivered as a volume rather than an env var because
    `verifier.manifest.load` takes a path, and because an env var is visible in
    the console and in every child process's environment.
  EOT
}

variable "marker_c1" {
  type        = string
  sensitive   = true
  description = "C1's value, from the fixture manifest. No default: a deployment rendering tool descriptions without it has silently removed the control flag, and SC-5 says a held C1 means the fixture or the verifier is broken."
}

variable "marker_f4" {
  type        = string
  sensitive   = true
  description = "F4's value, from the fixture manifest. F4 differs from C1 and F6 in that the generator does plant it, as a version of the estate's integration secret — but the runtime holds no estate access (SEC-11) and so cannot read it there. At L1 and L2 the key is in the Onboarding system prompt by design (FR-14), so without this the Onboarding agent cannot start at either level."
}

variable "marker_f6" {
  type        = string
  sensitive   = true
  description = "F6's value, from the fixture manifest. No default, for the same reason."
}

# ---------------------------------------------------------------------------
# Provider keys — SEC-9, TD-22
# ---------------------------------------------------------------------------
#
# All three are Vertex publisher model ids (FR-22, amended 2026-08-28). Formerly
# no key at all (FR-22), which is the stronger form of the same argument.

# ---------------------------------------------------------------------------
# Pinned models — FR-22a, OD-8
# ---------------------------------------------------------------------------

variable "site_provider" {
  type        = string
  default     = "anthropic"
  description = "Which provider the interactive site calls: anthropic, google or xai. One at a time — FR-22's three models are the suite's dimension, not a browser session's. An input because Vertex partner models start at zero tokens per minute on a new project, so the model that works today may not be the one that works next week."

  validation {
    condition     = contains(["anthropic", "google", "xai"], var.site_provider)
    error_message = "site_provider must be one of anthropic, google, xai."
  }
}

variable "model_anthropic" {
  type        = string
  description = "FR-22a. Pinned identifier, confirmed against the provider's live API at build time. Recorded on every run result."
}

variable "model_xai" {
  type        = string
  description = "FR-22a. Pinned identifier."
}

variable "model_google" {
  type        = string
  description = "FR-22a. Pinned identifier, confirmed available in var.region (OD-8)."
}

variable "xai_vertex_location" {
  type        = string
  default     = ""
  description = "Vertex location serving Grok, when it differs from var.vertex_location. Empty means use vertex_location. Same reasoning as anthropic_vertex_location: partner models reach fewer regions than Gemini."
}

variable "anthropic_vertex_location" {
  type        = string
  default     = ""
  description = "Vertex location serving Claude, when it differs from var.vertex_location. Partner models reach fewer regions than Gemini and fewer than Cloud Run; empty means use vertex_location. OD-8 requires confirming availability before a model is pinned."
}

variable "vertex_location" {
  type        = string
  description = "Vertex AI location. Separate from var.region because model availability and Cloud Run availability are not the same map."
}

# ---------------------------------------------------------------------------
# Quotas — NFR-3
# ---------------------------------------------------------------------------

variable "rate_limit_per_minute" {
  type        = number
  default     = 20
  description = "NFR-3 default. Per persona, enforced server-side."
}

variable "requests_per_persona_per_day" {
  type        = number
  default     = 300
  description = "NFR-3 default."
}

variable "daily_token_ceiling" {
  type        = number
  default     = 2000000
  description = "NFR-3: sized an order of magnitude below the SEC-3 budget cap. Applies to bypassed requests too — NFR-3a lifts rate limiting, never the ceiling."
}

variable "gate_attempts_per_minute" {
  type        = number
  default     = 10
  description = "TD-27, FR-2. Access-code attempts per client address per minute. Not a security boundary — the code is a shared secret and FR-2 says so — but POST /gate is the one unauthenticated endpoint on the service and until this it was unlimited."
}

variable "gate_attempts_per_hour" {
  type        = number
  default     = 60
  description = "TD-27. Access-code attempts per client address per hour."
}

variable "gate_attempts_per_hour_global" {
  type        = number
  default     = 600
  description = "TD-27. Access-code attempts per hour across all addresses, bounding distributed guessing. Anyone can spend it, and while it is spent the operator is refused at the front door too — sized so ordinary use cannot reach it."
}

variable "min_instance_count" {
  type        = number
  default     = 1
  description = "NFR-1 wants streaming to begin within 5s at p95, and a cold start on a container importing three provider SDKs does not meet that, so the default keeps one instance warm. Set to 0 for a run that is not measuring latency: with cpu_idle it is the whole of the idle cost (NFR-4), which is continuous and independent of whether anyone visits."
}

variable "cpu_idle" {
  type        = bool
  default     = false
  description = "False means CPU is always allocated, which streaming needs between token batches — throttling shows up as a stalled SSE rather than a slow response. True bills CPU only during request handling and takes idle cost to roughly zero. Set both this and min_instance_count for a short verification run; restore the defaults before any run whose latency figures are published."
}

variable "max_instance_count" {
  type        = number
  default     = 4
  description = <<-EOT
    Ceiling on concurrent instances. An input rather than a constant because
    the reference run needs to control it: session state is per-instance
    (TD-24) and NFR-3's counters are per-instance too, so the effective rate
    limit is the configured one multiplied by this number. Pinning it to 1 for
    a measured run removes both effects; the default keeps headroom for
    interactive use.
  EOT
}

variable "identity_platform_api_key" {
  type        = string
  sensitive   = true
  description = <<-EOT
    The project's public Identity Platform browser key, from
    `terraform -chdir=infra/bootstrap output -raw identity_platform_api_key`.

    Created in bootstrap rather than here because destroying an API key locks
    its id for 30 days and this module is destroyed every cycle — see
    identity_platform.tf and infra/bootstrap/identity_platform_key.tf.

    Marked sensitive so Terraform does not print it in a plan, not because it
    is secret. It is served to every browser that loads the persona picker and
    must be assumed known; SEC-10 is the control that makes that safe.
  EOT
}

variable "service_name" {
  type        = string
  default     = "bta-arena"
  description = "Cloud Run service name. Not deployment-identifying, but an input so two labs can share a project if an operator insists."
}

# TD-34. The evidence store — see evidence.tf.
variable "evidence_bucket_name" {
  type        = string
  description = "Bucket for raw transcripts (schemas.md §5). Holds live flag values."
}

variable "evidence_bucket_location" {
  type        = string
  description = "Location for the evidence bucket."
}

variable "evidence_retention_days" {
  type        = number
  default     = 30
  description = "Days before a raw transcript is deleted. Evidence for one run, not an archive."

  validation {
    condition     = var.evidence_retention_days >= 1
    error_message = "Retention must be at least one day; 0 would delete transcripts before a run finished."
  }
}
