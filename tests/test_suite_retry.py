"""Bounded retry for a rate-limited execution.

Gemini on Vertex serves this project from dynamic shared quota: no row to
raise, no rate to stay under, only capacity at the moment of the call. Three
live L1 runs lost 29, then 88 executions to it, and the only retry was the
operator re-running the command.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter

import pytest

from suite.runner import execute as ex
from suite.runner.client import RateLimited
from suite.runner.results import ModelPin, RunResult


def result(outcome: str = "not_captured", error: str | None = None) -> RunResult:
    return RunResult(
        run_id="R",
        case_id="AC-F1-001",
        flag_id="F1",
        level="L1",
        model=ModelPin(provider="google", identifier="g", pinned_at="t"),
        repeat_index=0,
        started_at="2026-09-05T00:00:00.000Z",
        duration_ms=1,
        outcome=outcome,
        error=error,
    )


THROTTLED = result("error", "rate limited: provider (the model call was throttled)")


def drive(outcomes, retry_limit=3, monkeypatch=None):
    """Run `execute_with_retry` over a scripted sequence of `execute_one`s."""
    calls = iter(outcomes)
    slept: list[float] = []

    async def fake_execute_one(execution, config):
        return next(calls)

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(ex, "execute_one", fake_execute_one)
    monkeypatch.setattr(ex.asyncio, "sleep", fake_sleep)
    config = ex.RunConfig(
        run_id="R",
        base_url="u",
        access_code="c",
        bypass_token="b",
        models=(ModelPin(provider="google", identifier="g", pinned_at="t"),),
        personas={"L1": "alice"},
        retry_limit=retry_limit,
    )
    got = asyncio.run(ex.execute_with_retry(object(), config))
    return got, slept


def test_a_throttled_execution_is_re_attempted_and_can_succeed(monkeypatch):
    (final, throttles), slept = drive([THROTTLED, THROTTLED, result()], monkeypatch=monkeypatch)
    assert final.outcome == "not_captured"
    assert len(slept) == 2, "one sleep per re-attempt, none after the last"


def test_a_survived_throttle_is_still_published(monkeypatch):
    """NFR-2 asks which limit bound the run. A limit that cost ninety seconds
    bound it whether or not the retry then worked; counting only the fatal
    ones would report `none hit` for a run that spent half its time waiting."""
    (_, throttles), _ = drive([THROTTLED, result()], monkeypatch=monkeypatch)
    assert throttles == Counter({"rate limited: provider": 1})


def test_attempts_are_bounded_and_the_last_failure_is_returned(monkeypatch):
    (final, throttles), slept = drive([THROTTLED] * 3, retry_limit=2, monkeypatch=monkeypatch)
    assert final.outcome == "error"
    assert sum(throttles.values()) == 3, "every attempt counted"
    assert len(slept) == 2, "no sleep after the final attempt"


def test_zero_retries_restores_the_previous_behaviour(monkeypatch):
    (final, throttles), slept = drive([THROTTLED], retry_limit=0, monkeypatch=monkeypatch)
    assert final.outcome == "error" and slept == []
    assert sum(throttles.values()) == 1


def test_an_ordinary_failure_is_not_retried(monkeypatch):
    """Retrying a 400 or a tool error would triple the cost of a deterministic
    failure and change nothing about it."""
    (final, throttles), slept = drive([result("error", "ProviderError")], monkeypatch=monkeypatch)
    assert final.outcome == "error" and slept == [] and not throttles


# --- the backoff itself, as a pure function --------------------------------


@pytest.mark.parametrize("attempt,expected", [(0, 2.0), (1, 4.0), (2, 8.0)])
def test_the_delay_doubles(attempt, expected):
    assert ex._backoff_seconds(attempt, "", jitter=0.5) == pytest.approx(expected)


def test_jitter_spreads_returns_by_a_quarter_either_way():
    """Without it every worker throttled by one capacity event comes back at
    the same instant and rebuilds the burst that caused it."""
    low = ex._backoff_seconds(0, "", jitter=0.0)
    high = ex._backoff_seconds(0, "", jitter=1.0)
    assert low == pytest.approx(1.5) and high == pytest.approx(2.5)


def test_the_sites_own_advice_wins_when_it_asks_for_longer():
    advised = str(RateLimited(limit="per-minute", retry_after_seconds=30))
    assert ex._backoff_seconds(0, advised, jitter=0.5) == pytest.approx(30.0)


def test_the_parser_reads_what_rate_limited_actually_writes():
    """A stringly-typed coupling, pinned rather than trusted: a drift would
    silently discard the site's advice and retry into the same closed window."""
    assert ex._RETRY_AFTER.search(str(RateLimited(limit="x", retry_after_seconds=7)))


def test_a_long_advisory_cannot_park_a_worker_for_the_run():
    advised = str(RateLimited(limit="per-hour", retry_after_seconds=3600))
    assert ex._backoff_seconds(0, advised, jitter=1.0) <= ex.RETRY_CAP_SECONDS


def test_rate_limited_survives_propagation_out_of_an_async_context_manager():
    """`send` raises `RateLimited` inside `async with http.stream(...)`, and the
    interpreter assigns `__traceback__` on the way out.

    This was `@dataclass(frozen=True, slots=True)` until 2026-09-15. Frozen
    forbids that assignment, and slots rebuilds the class so the generated
    `__setattr__` calls `super()` against the superseded one — which surfaced as
    `super(type, obj): obj (instance of RateLimited) is not an instance or
    subtype of type (RateLimited)` and crashed 265 of 324 L2 executions, every
    one of them a retryable 429. Constructing the exception is not enough to
    catch it; it has to propagate.
    """

    @contextlib.asynccontextmanager
    async def stream():
        yield object()

    async def raise_inside() -> None:
        async with stream():
            raise RateLimited(limit="provider", retry_after_seconds=30)

    with pytest.raises(RateLimited) as caught:
        asyncio.run(raise_inside())

    assert caught.value.limit == "provider"
    assert caught.value.retry_after_seconds == 30
    assert caught.value.__traceback__ is not None


def test_rate_limited_accepts_the_interpreters_exception_bookkeeping():
    """`__cause__` and `__context__` are assigned by `raise ... from`, which a
    frozen exception also refuses."""
    limited = RateLimited(limit="provider", retry_after_seconds=1)
    with pytest.raises(RuntimeError) as caught:
        raise RuntimeError("wrapped") from limited
    assert caught.value.__cause__ is limited
