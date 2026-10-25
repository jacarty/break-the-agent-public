"""The runner: planning, resumability, and the refusals that protect the evidence.

Offline by contract. Nothing here reaches a network — the deployment-facing
half is exercised against a stub, because what these tests are for is the
arithmetic and the guard rails, not httpx.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from bta.models import Agent, Level
from suite.cases.schema import AttackCase, Phase
from suite.runner.execute import (
    MAX_SAFE_CONCURRENCY,
    RunConfig,
    RunnerRefusal,
    execute_one,
    plan,
)
from suite.runner.results import ModelPin, ResultStore, RunResult

PIN = ModelPin(provider="google", identifier="gemini-x", pinned_at="2026-08-28T00:00:00.000Z")
PIN2 = ModelPin(provider="xai", identifier="grok-x", pinned_at="2026-08-28T00:00:00.000Z")


def case(
    case_id: str = "AC-F1-001",
    *,
    flag_id: str = "F1",
    agent: Agent = Agent.DEPLOY_BOT,
    levels: tuple[Level, ...] = (Level.L1,),
    phases: int = 1,
    fixtures: tuple[str, ...] = (),
) -> AttackCase:
    return AttackCase(
        schema_version=1,
        id=case_id,
        flag_id=flag_id,
        agent=agent,
        technique_family="direct_override",
        levels=levels,
        expected_outcome="capture_at_L1",
        phases=tuple(
            Phase(
                session=n + 1,
                # Placeholders are substituted only where they appear, so a
                # case that declares a fixture must use it for the missing-
                # fixture path to be reachable at all.
                turns=tuple(f"do the thing with {{{name}}}" for name in fixtures)
                or ("do the thing",),
            )
            for n in range(phases)
        ),
        requires_fixtures=fixtures,
    )


def config(**kwargs) -> RunConfig:
    base = {
        "run_id": "R-test",
        "base_url": "https://example.invalid",
        "access_code": "code",
        "bypass_token": "bypass",
        "models": (PIN,),
        "repeats": 3,
        "personas": {"L1": "alice"},
    }
    return RunConfig(**{**base, **kwargs})


def test_plan_is_the_product_of_cases_levels_and_repeats() -> None:
    executions = plan([case(levels=(Level.L1,))], config(models=(PIN,), repeats=3))
    assert len(executions) == 1 * 1 * 1 * 3


def test_more_than_one_model_pin_is_refused() -> None:
    """A deployment serves one model per process and `start_session` posts no
    model, so a second pin duplicates executions under a provider label nothing
    observed — and §8 rule 2 counts those labels toward `held`. A paper route
    to a verdict, so it is refused rather than warned about."""
    with pytest.raises(RunnerRefusal, match="one model per process"):
        plan([case()], config(models=(PIN, PIN2)))


def test_plan_is_ordered_by_the_uniqueness_tuple() -> None:
    """Resumability keys on `(case_id, level, model, repeat)` (schemas.md §5).
    An order that depended on iteration would re-map when a case is added."""
    executions = plan([case("AC-F1-002"), case("AC-F1-001")], config(models=(PIN,), repeats=2))
    assert [e.key for e in executions] == sorted(e.key for e in executions)


def test_the_concurrency_bound_is_the_peak_not_the_per_provider_value() -> None:
    """The semaphore is per provider, so live sessions peak at
    `concurrency x len(models)`. Checking the bare value let `--concurrency 32`
    with three pins reach 96 sessions against a cap of 64 — the refusal's own
    stated failure mode, reached through the refusal. The multiplier is one
    today because B2 caps the pins at one; the product is kept deliberately."""
    with pytest.raises(RunnerRefusal, match="peaks at"):
        plan([case()], config(concurrency=MAX_SAFE_CONCURRENCY + 1))
    # At the bound itself, with the one pin B2 permits, it is allowed.
    assert plan([case()], config(concurrency=MAX_SAFE_CONCURRENCY, repeats=1))


def test_concurrency_near_the_deployment_cap_is_refused() -> None:
    """TD-24: MAX_SESSIONS is 64 and eviction is a cross-user effect, so a run
    at the cap scores artefacts of eviction rather than of a control."""
    with pytest.raises(RunnerRefusal, match="MAX_SESSIONS"):
        plan([case()], config(concurrency=MAX_SAFE_CONCURRENCY + 1))


def test_a_memory_bearing_agent_may_not_share_a_persona_across_levels() -> None:
    """TD-20. Memory keys on persona alone and no path consults
    written_at_level, so L1's records reach the next L2 session's prompt."""
    with pytest.raises(RunnerRefusal, match="TD-20"):
        plan(
            [case(levels=(Level.L1, Level.L2))],
            config(personas={"L1": "alice", "L2": "alice"}),
        )


def test_distinct_personas_per_level_are_accepted() -> None:
    executions = plan(
        [case(levels=(Level.L1, Level.L2))],
        config(personas={"L1": "alice", "L2": "alice-l2"}, repeats=1),
    )
    assert {e.persona for e in executions} == {"alice", "alice-l2"}


def test_an_agent_without_memory_may_share_a_persona() -> None:
    """FR-12 gives memory to Deploy Bot only, so Onboarding is unaffected."""
    executions = plan(
        [
            case(
                "AC-F4-001",
                flag_id="F4",
                agent=Agent.ONBOARDING_ASSISTANT,
                levels=(Level.L1, Level.L2),
            )
        ],
        config(personas={"L1": "alice", "L2": "alice"}, repeats=1),
    )
    assert len(executions) == 2


def test_a_multi_phase_case_errors_while_memory_is_not_persistent() -> None:
    """TD-17: F7 cannot execute at all until a Firestore-backed store exists.

    Recorded as `error`, which contributes nothing to the SC-3 threshold — so
    the cell reads `insufficient`. Scoring it `not_captured` would manufacture
    evidence for a `held`.
    """
    result = asyncio.run(execute_one(plan([case(phases=2)], config())[0], config()))
    assert result.outcome == "error"
    assert "TD-17" in (result.error or "")


def test_a_missing_fixture_is_an_error_not_a_silent_skip() -> None:
    result = asyncio.run(
        execute_one(plan([case(fixtures=("other_incident_id",))], config())[0], config())
    )
    assert result.outcome == "error"
    assert "other_incident_id" in (result.error or "")


def test_result_store_resumes_on_the_uniqueness_tuple(tmp_path) -> None:
    store = ResultStore(tmp_path / "run.jsonl")
    result = RunResult(
        run_id="R-test",
        case_id="AC-F1-001",
        flag_id="F1",
        level="L1",
        model=PIN,
        repeat_index=0,
        started_at="2026-08-28T00:00:00.000Z",
        duration_ms=10,
        outcome="not_captured",
    )
    store.record(result)
    assert ResultStore(tmp_path / "run.jsonl").already_done(result.key)


def test_result_store_reads_a_minimal_but_complete_line(tmp_path) -> None:
    """Not a truncation test — every field `key_of` needs is present and the
    JSON is well-formed, just sparse. Kept separate from the genuine
    truncation case below: an earlier version of this file used exactly this
    fixture under the name "survives a truncated final line", which would
    still have passed with the `try/except` in `ResultStore._load` deleted
    outright, since nothing here is malformed enough to raise in the first
    place."""
    path = tmp_path / "run.jsonl"
    path.write_text('{"case_id":"A","level":"L1","model":{"identifier":"m"},"repeat_index":0}\n')
    assert ResultStore(path).completed == 1


def test_result_store_refuses_a_genuinely_truncated_final_line(tmp_path) -> None:
    """The actual shape of an interrupted run: a process killed mid-`write()`
    leaves a line that is not valid JSON at all — cut off mid-string, as here,
    rather than merely missing optional fields.

    `ResultStore._load` does not special-case line position: it raises
    `ValueError` for a malformed line whether it is first or last, and the
    message tells the operator to truncate the file by hand and resume rather
    than attempting that automatically. That is a deliberate fail-loud
    design — a run that silently dropped its last, possibly-incomplete
    execution and carried on could as easily be discarding a real result as a
    partial write — but it is a design decision worth pinning with a test
    that uses real truncation, since `test_result_store_reads_a_minimal_but_
    complete_line` does not exercise this path at all despite what its
    previous name claimed.
    """
    path = tmp_path / "run.jsonl"
    path.write_text(
        '{"case_id":"A","level":"L1","model":{"identifier":"m"},"repeat_index":0}\n'
        '{"case_id":"B","level":"L1","model":{"identifier'
    )
    with pytest.raises(ValueError, match="truncate it and resume"):
        ResultStore(path)


def test_result_store_refuses_a_corrupt_earlier_line(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    path.write_text("not json\n" + json.dumps({"x": 1}) + "\n")
    with pytest.raises(ValueError, match="not a readable run result"):
        ResultStore(path)


def test_outcome_and_captured_must_agree() -> None:
    """A result claiming a capture with nothing captured would count toward
    `captures_model_output` as zero while reading `captured` in the matrix."""
    with pytest.raises(ValueError, match="no captured entries"):
        RunResult(
            run_id="R",
            case_id="AC-F1-001",
            flag_id="F1",
            level="L1",
            model=PIN,
            repeat_index=0,
            started_at="2026-08-28T00:00:00.000Z",
            duration_ms=1,
            outcome="captured",
        )


def test_model_pin_carries_pinned_at() -> None:
    """FR-22a, and the half of TD-18 that had no consumer until the runner."""
    assert PIN.to_dict()["pinned_at"] == "2026-08-28T00:00:00.000Z"
