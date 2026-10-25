"""`bta.providers.classify_failure` — NFR-2's other door.

A run bound by Vertex quota is as bound as one bound by NFR-3, and until this
existed only the second could be published: `ProviderError` carries a message
written by this repository rather than by the vendor (SEC-9, VER-5), so the
class lives in the chained cause or nowhere.
"""

from __future__ import annotations

import pytest

from bta.providers import FAILURE_OTHER, FAILURE_RATE_LIMITED, ProviderError, classify_failure


class VendorRateLimitError(Exception):
    """Anthropic and OpenAI both spell it roughly this way."""


class ClientError(Exception):
    """`google.genai` — the status is positional, the name says nothing."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code


def _chain(exc: BaseException) -> ProviderError:
    error = ProviderError("google call failed")
    error.__cause__ = exc
    return error


def test_the_google_429_that_bound_the_first_live_run_is_recognised():
    """`ClientError: 429 RESOURCE_EXHAUSTED` — 29 of 324 executions, reported
    by the run as `binding provider limit: none hit`."""
    cause = ClientError(429, "RESOURCE_EXHAUSTED. Resource exhausted.")
    assert classify_failure(_chain(cause)) == FAILURE_RATE_LIMITED


@pytest.mark.parametrize(
    "cause",
    [
        VendorRateLimitError("429"),
        ClientError(429, "whatever"),
        RuntimeError("Quota exceeded for aiplatform.googleapis.com/..."),
        RuntimeError("Too Many Requests"),
    ],
)
def test_every_vendor_spelling_lands_in_the_same_bucket(cause):
    assert classify_failure(_chain(cause)) == FAILURE_RATE_LIMITED


@pytest.mark.parametrize(
    "cause",
    [ValueError("invalid argument"), ClientError(400, "INVALID_ARGUMENT"), TimeoutError()],
)
def test_an_ordinary_failure_is_not_reported_as_a_limit(cause):
    """The expensive direction to get wrong: a 400 counted as throttling would
    publish a limit that never fired and send an operator to a quota page."""
    assert classify_failure(_chain(cause)) == FAILURE_OTHER


def test_a_cycle_in_the_cause_chain_terminates():
    first, second = RuntimeError("a"), RuntimeError("b")
    first.__cause__, second.__cause__ = second, first
    assert classify_failure(first) == FAILURE_OTHER


def test_the_vocabulary_is_two_words_and_describes_no_deployment():
    """SEC: the coarse class crosses to a bypass holder. A quota metric, a base
    model or a region must never be in it."""
    for value in (FAILURE_RATE_LIMITED, FAILURE_OTHER):
        assert value.islower() and " " not in value
    assert "aiplatform" not in FAILURE_RATE_LIMITED
