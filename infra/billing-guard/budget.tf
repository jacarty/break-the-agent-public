# SEC-3: budget alert → Pub/Sub → function that detaches billing.
#
# The budget is scoped to this one project even though it lives on the billing
# account, so a second project on the same account cannot push the lab over its
# cap and trigger the guard.

resource "google_pubsub_topic" "budget_alerts" {
  project = var.project_id
  name    = "${var.function_name}-budget-alerts"

  depends_on = [google_project_service.guard]
}

# There is no publisher binding here, and there used to be. Removed 2026-08-17
# (TD-7) after it blocked the first deploy twice.
#
# The resource granted `roles/pubsub.publisher` on this topic to
# `billing-budgets-pubsub@system.gserviceaccount.com`, with a comment claiming
# that without it the budget creates fine and silently never delivers. Both the
# binding and the claim were wrong:
#
#   - The principal does not resolve. IAM rejects it with 400 "Service account
#     ... does not exist", identically for the CI deploy identity and for an
#     operator with roles/owner. It is a global `@system.` account, not a
#     per-project service agent, so nothing provisions it on our side and no
#     wait helps — a 90s absorber was tried and the error was unchanged.
#   - Google's own tutorial for this exact architecture grants no such binding.
#     Publish permission is established by the platform when a budget is linked
#     to a topic, which is what `all_updates_rule` below does.
#
# **Delivery is therefore not proven by a successful apply**, and never was —
# the removed binding would not have proven it either. The SEC-3 rehearsal on a
# scratch project is what establishes that a threshold breach actually reaches
# the function, and `enable_kill_switch` stays false until it has been run for
# real. See README.md §Rehearsal.

resource "google_billing_budget" "cap" {
  billing_account = var.billing_account_id
  display_name    = "${var.project_id} hard cap (SEC-2)"

  budget_filter {
    projects = ["projects/${var.project_id}"]

    # Credits do not offset the spend that matters here. A trial credit
    # masking real usage is exactly the case the cap exists to catch.
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = var.currency_code
      units         = tostring(var.budget_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = var.alert_thresholds
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  all_updates_rule {
    pubsub_topic   = google_pubsub_topic.budget_alerts.id
    schema_version = "1.0"

    # Default IAM recipients — Billing Account Administrators and Users — are
    # left enabled, and the previous value here was `true` on a premise that
    # does not hold. The comment claimed default recipients would "send mail on
    # every cost update, not every threshold crossing". It is the reverse:
    # email fires on threshold crossings, while this topic receives a message
    # roughly every 50 minutes whether or not anything was crossed. The channel
    # being suppressed was the quiet one.
    #
    # It also could not be set at all. A budget with `disable_default_iam_
    # recipients = true` and no `monitoring_notification_channels` has no
    # recipients whatsoever, and the API rejects it with a bare
    # 400 INVALID_ARGUMENT naming no field. That is what blocked the first
    # deploy; see TD-7.
    #
    # Keeping it false means a human is told the cap is being approached, in
    # addition to the function acting at 1.0. For a lab whose entire safety
    # story is a spend cap, one email per threshold is not the noise worth
    # optimising away.
    disable_default_iam_recipients = false
  }

  depends_on = [google_pubsub_topic.budget_alerts]
}
