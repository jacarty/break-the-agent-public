variable "project_id" {
  description = "Project whose billing is detached when the budget is exceeded."
  type        = string
}

variable "region" {
  description = "Region for the function and its source bucket."
  type        = string
}

variable "billing_account_id" {
  description = <<-EOT
    Billing account the project is attached to, as `XXXXXX-XXXXXX-XXXXXX`.

    SEC-2 requires this to be a billing account dedicated to the lab. The
    reason is this module: the guard detaches a project from its billing
    account, and the blast radius of a mistake here is bounded by what else
    that account pays for.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$", var.billing_account_id))
    error_message = "billing_account_id must look like 0X0X0X-0X0X0X-0X0X0X, not a project or account name."
  }
}

variable "budget_amount" {
  description = "Hard cap in whole currency units. Billing is detached at 100% of this."
  type        = number

  validation {
    condition     = var.budget_amount > 0
    error_message = "budget_amount must be positive. A zero budget fires the guard immediately."
  }
}

variable "currency_code" {
  description = "Must match the billing account's currency; the budget API rejects a mismatch."
  type        = string
  default     = "GBP"
}

variable "alert_thresholds" {
  description = <<-EOT
    Fractions of the budget at which a notification is published. The guard
    acts only at >= 1.0; the lower ones exist so the operator hears about it
    first.

    Every threshold publishes to the same topic, and Pub/Sub also delivers a
    message on ordinary cost updates. The function is invoked constantly and
    must decide for itself — see `should_disable` in function/main.py.
  EOT
  type        = list(number)
  default     = [0.5, 0.9, 1.0]
}

variable "enable_kill_switch" {
  description = <<-EOT
    When false, the function logs the decision and detaches nothing.

    False is the default because an untested kill switch and a tested one look
    identical until the day they run. Deploy false, rehearse per README, then
    set true. SEC-3 says tested, not deployed.
  EOT
  type        = bool
  default     = false
}

variable "source_bucket_name" {
  description = "Bucket holding the zipped function source. Deployment-specific (POR-6)."
  type        = string
}

variable "function_name" {
  description = "Name of the Cloud Function. Describes the lab, not the deployment."
  type        = string
  default     = "billing-guard"
}

variable "propagation_delay" {
  description = <<-EOT
    How long to wait for IAM to become visible to Eventarc before deploying
    the function.

    Deploying a gen2 function whose trigger runs as another identity makes
    Eventarc check that the deployer can `actAs` that identity. A first apply
    against a clean project failed there with a 403 on
    `iam.serviceAccounts.ActAs` while the project IAM policy, read directly,
    already carried the binding. It is a terminal error rather than something
    Terraform re-polls, so re-running the apply is the only other fix — which
    is the manual intervention NFR-5 says a deploy must not need.

    **This absorber is precautionary, not proven.** The apply that introduced
    it also ran against a service account created a full run earlier, so the
    wait and the extra elapsed time are confounded and either could explain
    the success. It costs 90 seconds on a create and nothing afterwards, which
    is cheap enough not to want a second failed deploy to settle the question.

    A second absorber sat here for one commit, covering a Pub/Sub publisher
    binding that turned out to be impossible rather than slow. See TD-7 and
    the comment in budget.tf.
  EOT
  type        = string
  default     = "90s"
}
