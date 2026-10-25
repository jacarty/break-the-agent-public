"""The part of the guard that decides. No clients, no environment, no imports.

Separate from main.py so `tests/test_billing_guard.py` can run it in the repo's
own venv, which has neither functions-framework nor the billing client. A
kill switch whose logic is only exercised by deploying it is a kill switch
nobody has tested.
"""

from __future__ import annotations

from typing import Any


class MalformedNotification(ValueError):
    """The message did not carry the two numbers the decision needs."""


def parse_notification(payload: dict[str, Any]) -> tuple[float, float]:
    """Return (cost, budget) from a budget notification body.

    `costAmount` is absent from the first notification after a budget is
    created, and `budgetAmount` is absent when a budget tracks last month's
    spend rather than a specified amount. Neither is supported here, and
    substituting a default for either decides the kill switch on a number
    nobody sent.
    """
    try:
        cost = float(payload["costAmount"])
        budget = float(payload["budgetAmount"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedNotification(f"missing or non-numeric cost/budget: {exc}") from exc

    if budget <= 0:
        raise MalformedNotification(f"budgetAmount is {budget}, which cannot be a cap")

    return cost, budget


def should_disable(cost: float, budget: float) -> bool:
    """Whether spend has reached the hard cap.

    `>=`, not `>`: the cap is the amount that must not be exceeded, and the
    budget's own 100% threshold fires on equality.
    """
    return cost >= budget
