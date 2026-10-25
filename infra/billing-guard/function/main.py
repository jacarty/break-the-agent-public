"""SEC-3 kill switch: detach the project from its billing account at the cap.

Invoked from Pub/Sub by a Cloud Billing budget. The budget publishes on *every*
cost update, not only when a threshold is crossed, so most invocations do
nothing. The decision itself lives in decision.py, which imports nothing.
"""

from __future__ import annotations

import base64
import json
import logging
import os

import functions_framework
from decision import parse_notification, should_disable
from google.cloud.billing_v1 import CloudBillingClient, ProjectBillingInfo

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("billing-guard")


def _billing_is_enabled(client: CloudBillingClient, project_id: str) -> bool:
    info = client.get_project_billing_info(name=f"projects/{project_id}")
    return bool(info.billing_enabled)


def _detach(client: CloudBillingClient, project_id: str) -> None:
    client.update_project_billing_info(
        name=f"projects/{project_id}",
        project_billing_info=ProjectBillingInfo(billing_account_name=""),
    )


@functions_framework.cloud_event
def handle_budget_alert(cloud_event) -> None:
    project_id = os.environ["GUARD_PROJECT_ID"]
    armed = os.environ.get("GUARD_ENABLED", "false").lower() == "true"

    encoded = cloud_event.data["message"].get("data")
    if not encoded:
        log.info("notification carried no data; nothing to decide")
        return

    payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
    cost, budget = parse_notification(payload)

    if not should_disable(cost, budget):
        log.info("spend %.2f of %.2f — under cap", cost, budget)
        return

    # Past here is the firing path, and it runs on every later notification too:
    # the budget keeps publishing after the cap is reached. Idempotence comes
    # from re-reading billing_enabled, not from remembering.
    if not armed:
        log.warning(
            "CAP REACHED: spend %.2f of %.2f. GUARD_ENABLED is false — billing left attached.",
            cost,
            budget,
        )
        return

    client = CloudBillingClient()
    if not _billing_is_enabled(client, project_id):
        log.info("cap reached and billing already detached from %s", project_id)
        return

    log.critical("CAP REACHED: spend %.2f of %.2f. Detaching %s.", cost, budget, project_id)
    _detach(client, project_id)
    log.critical("billing detached from %s", project_id)
