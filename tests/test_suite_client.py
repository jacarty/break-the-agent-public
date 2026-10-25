"""The wire between the deployment and the operator's instrument.

The suite is *told* what happened over the same HTTP surface a person uses
(ADR 0013). That keeps the seam thin, and it means the two ends spell the
protocol independently — so the spellings are pinned together here rather than
shared as an import.
"""

from __future__ import annotations

from bta.providers import FAILURE_RATE_LIMITED
from suite.runner.client import RATE_LIMITED_REASON


def test_both_ends_spell_the_rate_limit_reason_the_same_way():
    """The decoupling is deliberate; a drift in it would be silent. The site
    would report throttling, the runner would record `ProviderError`, and
    NFR-2 would publish `none hit` again — the exact defect this closed."""
    assert RATE_LIMITED_REASON == FAILURE_RATE_LIMITED
