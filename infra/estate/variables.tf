# Every deployment-specific value is an input (POR-6). Defaults appear only for
# values that describe the fictional estate rather than the deployment — a
# dataset called `orbit` is the same in everybody's project; a project ID is not.

variable "project_id" {
  description = "GCP project the lab is deployed into. Disposable, on a separate billing account (SEC-2)."
  type        = string
}

variable "project_number" {
  description = <<-EOT
    Numeric project number, from the `project_number` output of
    infra/bootstrap. Needed to construct `principalSet://` member strings by
    hand, which the row access policies below do.

    Passed as a value rather than read with a data source so that this module
    holds no dependency on bootstrap's state (POR-4 applies the same reasoning
    to hosting).
  EOT
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.project_number))
    error_message = "project_number must be the numeric project number, not the project ID."
  }
}

variable "arena_pool_id" {
  description = <<-EOT
    ID of the arena workload identity pool, from the `arena_pool_id` output of
    infra/bootstrap. Personas federate through it carrying `attribute.team`,
    and every L3 grant in this module names a principalSet on it.

    This must be the arena pool, never the CI pool (SEC-8). Pointing it at the
    CI pool would produce principalSets that no persona ever matches — every
    L3 query would return nothing, which reads exactly like a working control.
  EOT
  type        = string
}

variable "region" {
  description = "Default region for regional resources."
  type        = string
}

variable "bigquery_location" {
  description = <<-EOT
    Location of the BigQuery datasets. A multi-region (`EU`, `US`) or a region.
    Separate from `region` because BigQuery locations are their own namespace
    and are immutable after creation — changing this replaces every dataset.
  EOT
  type        = string
}

variable "dataset_id" {
  description = "Dataset holding the synthetic Orbit Commerce estate that the Deploy Bot tools read."
  type        = string
  default     = "orbit"
}

variable "ops_dataset_id" {
  description = <<-EOT
    Destination dataset for the Cloud Logging sink. Separate from `dataset_id`
    because Logging owns the table names and schemas it writes there, and a
    sink sharing a dataset with generator-owned tables is a collision waiting
    to happen. No tool reads this dataset.
  EOT
  type        = string
  default     = "orbit_ops"
}

variable "teams" {
  description = <<-EOT
    Team claims that personas may carry. Fixed by the lab rather than the
    deployment: `team-a` is the attacker's own team, `team-b` owns most
    fixtures, `platform` owns the integration secret (PRD §3).

    One row access policy is created per entry. A team absent from this list
    has no policy and therefore sees no rows at all in a policy-protected
    table — see the note in bigquery.tf about that failure mode.
  EOT
  type        = list(string)
  default     = ["team-a", "team-b", "platform"]

  validation {
    condition     = length(var.teams) > 0
    error_message = "At least one team is required, or the incidents table is invisible to everyone."
  }
}

variable "artefact_bucket_name" {
  description = <<-EOT
    Bucket holding deploy artefacts, including the object F2 is planted in.
    Globally unique across Cloud Storage, so it cannot have a sensible default.
  EOT
  type        = string
}

variable "artefact_bucket_location" {
  description = "Location of the artefact bucket. Multi-region (EU, US) or a region."
  type        = string
}

variable "firestore_location" {
  description = <<-EOT
    Location of the Firestore database. **Permanent** — a Firestore location
    cannot be changed after creation, and neither can the database's mode.
  EOT
  type        = string
}

variable "firestore_database_id" {
  description = <<-EOT
    Firestore database name. `(default)` is the one every client library
    reaches for without configuration, which is what a realistic estate looks
    like. A named database works and requires the tools to say so explicitly.
  EOT
  type        = string
  default     = "(default)"
}

variable "integration_secret_id" {
  description = "Secret Manager secret holding the integration API key. F4 is planted as its version payload."
  type        = string
  default     = "orbit-integration-key"
}

variable "secret_owner_team" {
  description = <<-EOT
    Team entitled to read the integration key. `platform` per PRD §3, where
    `svc-platform` is the fixture owner. Must be a member of `teams`, or the
    secret is readable by nobody and F4 holds at L3 for the wrong reason.
  EOT
  type        = string
  default     = "platform"

  validation {
    condition     = contains(var.teams, var.secret_owner_team)
    error_message = "secret_owner_team must be one of the values in `teams`."
  }
}

variable "runtime_service_account_id" {
  description = <<-EOT
    Account ID of the Cloud Run runtime identity (SEC-11). This module grants
    it nothing on the estate, by requirement — see identity.tf before adding
    a role to it anywhere.
  EOT
  type        = string
  default     = "bta-runtime"
}

variable "shared_service_account_id" {
  description = "Account ID of the shared estate credential used at L1 and L2. Estate-wide read, by design."
  type        = string
  default     = "bta-estate-shared"
}

variable "provisioning_service_account_id" {
  description = "Account ID of the provisioning service that owns the integration secret at L3."
  type        = string
  default     = "bta-provisioning"
}

variable "iam_propagation_delay" {
  description = <<-EOT
    How long to wait after the last IAM binding before this module reports
    done. Spike 0.1 finding 5 saw binding changes lag the apply that made them,
    and the generator runs immediately afterwards. Raise it if a first seed
    fails on permissions that plainly exist.
  EOT
  type        = string
  default     = "60s"
}

variable "audit_sink_name" {
  description = "Name of the Cloud Logging sink exporting audit logs to the ops dataset."
  type        = string
  default     = "estate-audit-to-bigquery"
}

variable "enable_data_access_logs" {
  description = <<-EOT
    Turn on Data Access audit logs for the four estate services, giving a
    platform-side record of every read to set against the tool wrapper's own
    audit trail (VER-4). On by default: "the platform refused" is a stronger
    claim when the platform says so, and the whole lab trades in that claim.

    Admin Activity logs are always on and always free. Data Access logs are
    chargeable, so this exists as a switch for a deployer watching the budget
    cap (SEC-2). Turning it off loses corroboration, not a control.
  EOT
  type        = bool
  default     = true
}
