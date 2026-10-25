"""SEC-3 — the kill switch's decision logic.

Loaded by path: infra/billing-guard/function/ is deployed source, not part of
the `bta` package, and putting it on the package path would ship the billing
client into the agent's dependency tree.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DECISION_PY = REPO_ROOT / "infra" / "billing-guard" / "function" / "decision.py"


def _load():
    spec = importlib.util.spec_from_file_location("billing_guard_decision", DECISION_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


decision = _load()


@pytest.mark.parametrize(
    ("cost", "budget", "expected"),
    [
        (0.0, 50.0, False),
        (49.99, 50.0, False),
        # The budget's own 100% threshold fires on equality, so the guard must
        # agree with it rather than wait for one more penny.
        (50.0, 50.0, True),
        (73.4, 50.0, True),
    ],
)
def test_the_cap_is_inclusive(cost: float, budget: float, expected: bool) -> None:
    assert decision.should_disable(cost, budget) is expected


def test_a_normal_notification_parses() -> None:
    payload = {"costAmount": 12.5, "budgetAmount": 50, "budgetDisplayName": "cap"}
    assert decision.parse_notification(payload) == (12.5, 50.0)


def test_strings_parse_because_the_api_sends_them_that_way() -> None:
    assert decision.parse_notification({"costAmount": "51", "budgetAmount": "50"}) == (51.0, 50.0)


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"budgetAmount": 50}, "first notification after creation carries no costAmount"),
        ({"costAmount": 10}, "last-period budgets carry no budgetAmount"),
        ({"costAmount": 10, "budgetAmount": None}, "null is not a cap"),
        ({"costAmount": 10, "budgetAmount": "n/a"}, "non-numeric"),
    ],
)
def test_an_incomplete_notification_raises_rather_than_defaulting(payload: dict, why: str) -> None:
    """Defaulting either number to zero would detach billing on a message that
    said nothing about spend."""
    with pytest.raises(decision.MalformedNotification):
        decision.parse_notification(payload)


def test_a_zero_budget_is_rejected() -> None:
    with pytest.raises(decision.MalformedNotification):
        decision.parse_notification({"costAmount": 0, "budgetAmount": 0})
