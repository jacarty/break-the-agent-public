"""Orchestration — asyncio, bounded concurrency, resumable, honest about what it cannot run.

## Concurrency is bounded by three different things, and they are not the same

**The provider.** `build-plan.md` §3 puts a full run at ~2,700 sessions and
NFR-2 names provider-side rate limiting as the binding constraint. Concurrency
is therefore per provider, not global: one provider throttling must not idle the
other two.

**The deployment.** `bta.site.app.MAX_SESSIONS` is 64 and eviction is a
cross-user effect — the 65th session drops somebody else's live one (TD-24). A
run at 64+ concurrent sessions would score results that are artefacts of
eviction rather than of a control, so the default here is far below it and
values near the cap are refused rather than warned about.

**The budget.** The NFR-3a bypass token lifts per-persona rate limits and never
the daily token ceiling (SEC-3). Nothing here can raise that, which is correct.

## TD-20 is enforced, not assumed

Memory keys on persona alone and no read or write path consults
`written_at_level`, so a freeform record written in an L1 session is replayed
into the next L2 session's system prompt — unclassified, because FR-15's stack
covers user turns and tool results and memory is neither. The L2 column would
then be measuring L1's leftovers.

TD-20 states the requirement: **the runner allocates a persona per level, or
resets the store between levels.** The suite cannot reset a store inside the
deployment, so this implements the first — and, because PRD §3 currently makes
only `alice` selectable, it *refuses to run* a memory-bearing agent across more
than one level in a single run until distinct personas exist. An earlier draft
of TD-20 assumed a run would not mix levels within a persona; nothing enforced
it, and that assumption is what this class exists to stop being handed forward.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from bta.models import Agent, Level
from suite.cases import AttackCase
from suite.runner.client import ArenaClient, RateLimited, SiteError
from suite.runner.results import ModelPin, ResultStore, RunResult

#: Comfortably below `bta.site.app.MAX_SESSIONS` (64). The suite opens one
#: session per (case, level, model, repeat) and closes it; the cap is on how
#: many are live at once, and leaving headroom means an operator poking at the
#: site by hand during a run does not evict a result.
DEFAULT_CONCURRENCY = 8

#: Refusing rather than clamping. A run configured at the deployment's own cap
#: produces results indistinguishable from correct ones, and the failure is
#: invisible in the output — which is the argument for making it a refusal.
MAX_SAFE_CONCURRENCY = 32

#: Re-attempts after the first, for a **rate-limited** execution only.
#:
#: Gemini on Vertex serves this project from dynamic shared quota: there is no
#: quota row to raise and no rate to stay under, only capacity at the moment of
#: the call. The second and third live L1 runs lost 29 and then 88 executions
#: to it, and `--concurrency 4` did not help — DSQ backpressure is not a rate.
#: Until this existed the only retry was the operator re-running the command,
#: which is why that run was attempted three times by hand.
RETRY_LIMIT = 3

#: Exponential from here, doubling, with jitter. Capped so a long `Retry-After`
#: on a per-hour site limit cannot park a worker for the rest of the run.
RETRY_BASE_SECONDS = 2.0
RETRY_CAP_SECONDS = 60.0

#: Written by `RateLimited.__str__`, read here. The coupling is a string, and
#: `tests/test_suite_retry.py` pins the two together rather than trusting it —
#: a drift would silently discard the site's own advice about when to return.
_RETRY_AFTER = re.compile(r"retry after (\d+)s")

#: FR-12 gives Deploy Bot persistent per-persona memory. Onboarding Assistant
#: has none, so it is unaffected by TD-20 and may share a persona across levels.
MEMORY_BEARING_AGENTS = frozenset({Agent.DEPLOY_BOT})


class RunnerRefusal(RuntimeError):
    """A configuration that would produce misleading evidence. Raised before any
    inference is paid for, never mid-run."""


@dataclass(frozen=True, slots=True)
class Execution:
    """One unit of work: `schemas.md` §5's uniqueness tuple, resolved."""

    case: AttackCase
    level: Level
    model: ModelPin
    repeat_index: int
    persona: str

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.case.id, self.level.value, self.model.identifier, self.repeat_index)


@dataclass
class RunConfig:
    run_id: str
    base_url: str
    access_code: str
    bypass_token: str
    models: tuple[ModelPin, ...]

    #: Test-only seam: an ASGI transport that drives the real site app
    #: in-process. `None` in every real run, which is what keeps the suite's
    #: only path to a deployment the same HTTP surface a person uses.
    transport: object | None = None
    repeats: int = 3
    concurrency: int = DEFAULT_CONCURRENCY

    #: Level → persona. TD-20's first option. A single entry for every level is
    #: allowed only when no memory-bearing agent is in scope.
    personas: dict[str, str] = field(default_factory=lambda: {"L1": "alice"})

    #: Where redacted transcripts are written, one directory per run. Beside
    #: the results by default, because they are evidence for the same run.
    results_dir: str = "evidence/runs"

    #: Operator-supplied fixture identifiers, substituted into case turns.
    #: `schemas.md` §3 forbids the suite reading the manifest, so these arrive
    #: the same way `bta.tools.harness` takes them — from the environment.
    fixtures: dict[str, str] = field(default_factory=dict)

    #: Whether phase two of a multi-session case can see what phase one wrote.
    #:
    #: `bta.memory.firestore.FirestoreMemoryStore` exists as of 2026-08-29 and
    #: the site wires it in, so this is no longer structurally False — but it
    #: stays False by default, because it is an operator *assertion* about a
    #: deployment and not an observation the runner can make. Nothing here can
    #: tell a store that persists from one that raises on every write.
    #:
    #: Turn it on only after TD-17's live half has actually run: a record
    #: written in one session and read back in a fresh one, against the
    #: deployed estate. Until then F7's cases record `error`, which contributes
    #: nothing to the SC-3 threshold and leaves the cell reading `insufficient`,
    #: truthfully.
    #:
    #: `--memory-is-persistent` is how an operator asserts it, and the default
    #: stays `False` because this is an assertion and not an observation: the
    #: runner posts a turn and reads a stream, and nothing in that exchange
    #: reports whether the store behind it is durable. The first live L1 run
    #: (2026-09-04) refused all 72 F7 executions on a store that had in fact
    #: been persistent for six days — the code half of TD-17 had closed and
    #: there was no way to say so.
    memory_is_persistent: bool = False

    #: Re-attempts for a rate-limited execution. Zero restores the previous
    #: behaviour, where one 429 ended the execution and the cell lost a case.
    retry_limit: int = RETRY_LIMIT

    def persona_for(self, level: Level) -> str:
        persona = self.personas.get(level.value)
        if persona is None:
            raise RunnerRefusal(
                f"no persona configured for {level.value}. TD-20 requires a "
                f"persona per level for memory-bearing agents."
            )
        return persona


def plan(cases: Sequence[AttackCase], config: RunConfig) -> list[Execution]:
    """Every execution the run will attempt, in a stable order.

    Sorted by the uniqueness tuple so a resumed run agrees with the original
    about what is already done — resumability keyed on position in an
    iteration order breaks the moment a case is added.
    """
    _refuse_unsafe(cases, config)
    executions = [
        Execution(
            case=case,
            level=level,
            model=model,
            repeat_index=repeat,
            persona=config.persona_for(level),
        )
        for case in cases
        for level in case.levels
        for model in config.models
        for repeat in range(config.repeats)
    ]
    return sorted(executions, key=lambda e: e.key)


def _refuse_unsafe(cases: Sequence[AttackCase], config: RunConfig) -> None:
    if config.concurrency < 1:
        raise RunnerRefusal("concurrency must be at least 1")
    if not config.models:
        raise RunnerRefusal("no models configured; FR-22a requires a pinned identifier per run")

    # **The runner labels the model; it does not choose it.** `start_session`
    # posts persona, agent and level — nothing else. Which model answers is a
    # per-process property of the deployment (`BTA_SITE_PROVIDER`), and nothing
    # in the SSE frame reports it back. So a second pin does not run a second
    # model: it duplicates every execution and asserts a provider label that
    # nothing verified.
    #
    # That matters beyond bookkeeping. §8 rule 2 requires "all three providers
    # ran", tested against these labels — three environment variables against
    # one deployment would satisfy the provider clause of a `held` verdict with
    # one model answering every session. That is a paper route to `held`, which
    # is the exact class §4's pre-registration exists to close, so it is a
    # refusal rather than a warning.
    #
    # FR-22 is met by running once per provider with `BTA_SITE_PROVIDER`
    # rotated and concatenating the result files. TD-33 carries what it would
    # take to make the model a per-session input instead.
    if len(config.models) > 1:
        pinned = ", ".join(f"{m.provider}:{m.identifier}" for m in config.models)
        raise RunnerRefusal(
            f"{len(config.models)} models pinned ({pinned}), but a deployment "
            f"serves one model per process and the runner cannot ask for "
            f"another. Additional pins would duplicate executions under a "
            f"provider label nothing observed, and §8 rule 2 counts those "
            f"labels toward a `held` verdict.\n\n"
            f"Run once per provider with BTA_SITE_PROVIDER rotated on the "
            f"deployment, and concatenate the result files."
        )

    # The semaphore is per provider, so live sessions peak at the product, not
    # at `concurrency`. Checking the bare value let `--concurrency 32` with
    # three pins reach 96 concurrent sessions against a cap of 64 — the precise
    # failure this refusal names, reachable through the refusal itself.
    #
    # The multiplier is one today, because the refusal above caps the pins at
    # one. It is kept as the product rather than simplified: TD-33 is about
    # lifting that cap, and a bound that silently stopped accounting for the
    # model dimension the day it returned is how this defect arrived.
    peak = config.concurrency * max(1, len(config.models))
    if peak > MAX_SAFE_CONCURRENCY:
        raise RunnerRefusal(
            f"concurrency {config.concurrency} across {len(config.models)} "
            f"provider(s) peaks at {peak} live sessions, too close to the "
            f"deployment's own cap (bta.site.app.MAX_SESSIONS = 64) where "
            f"eviction is a cross-user effect (TD-24). Results would be "
            f"artefacts of eviction rather than of a control. Maximum peak "
            f"here is {MAX_SAFE_CONCURRENCY}."
        )

    # TD-20. Only checked for memory-bearing agents, because Onboarding
    # Assistant has no memory (FR-12) and sharing a persona across its levels
    # contaminates nothing.
    memory_levels = {
        level for case in cases if case.agent in MEMORY_BEARING_AGENTS for level in case.levels
    }
    assigned = {config.persona_for(level) for level in memory_levels} if memory_levels else set()
    if len(memory_levels) > 1 and len(assigned) < len(memory_levels):
        raise RunnerRefusal(
            f"{len(memory_levels)} levels of a memory-bearing agent share "
            f"{len(assigned)} persona(s). Memory keys on persona alone and no "
            f"path consults written_at_level (TD-20), so an L1 session's "
            f"records are replayed into the next L2 session's system prompt "
            f"and the L2 column stops measuring the mitigation stack.\n\n"
            f"Fix by configuring a distinct persona per level, or by running "
            f"one level per invocation. PRD §3 currently makes only `alice` "
            f"selectable, so today that means one level per run."
        )


async def execute_one(execution: Execution, config: RunConfig) -> RunResult:
    """One case, at one level, on one model, once.

    Every phase opens a **fresh client and a fresh session** (`schemas.md` §4):
    a new cookie jar, a new persona token, no carried context. Only persisted
    memory crosses the boundary, which is the whole mechanism F7 tests.
    """
    started = datetime.now(UTC)
    began = time.monotonic()

    def finish(outcome: str, **kwargs: object) -> RunResult:
        return RunResult(
            run_id=config.run_id,
            case_id=execution.case.id,
            flag_id=execution.case.flag_id,
            level=execution.level.value,
            model=execution.model,
            repeat_index=execution.repeat_index,
            started_at=started.isoformat().replace("+00:00", "Z"),
            duration_ms=int((time.monotonic() - began) * 1000),
            outcome=outcome,
            **kwargs,  # type: ignore[arg-type]
        )

    case = execution.case
    if case.is_multi_session and not config.memory_is_persistent:
        return finish(
            "error",
            error=(
                "multi-phase case cannot execute: FR-12's persistent memory has "
                "no backing store (TD-17), so phase two's fresh session carries "
                "nothing forward. Recorded as an error rather than not_captured "
                "— an unexecutable case has tested nothing, and scoring it as a "
                "non-capture would manufacture evidence for a hold."
            ),
        )

    try:
        turns = _resolve_fixtures(case, config.fixtures)
    except KeyError as missing:
        return finish(
            "error",
            error=(
                f"missing fixture {missing}. The suite may not read the fixture "
                f"manifest (schemas.md §3); supply it in the environment as "
                f"bta.tools.harness does. Skipped loudly so it cannot quietly "
                f"shrink the cell's case count."
            ),
        )

    captured: list[dict] = []
    tokens = {"input": 0, "output": 0}
    turn_count = 0
    transcript_ref: str | None = None
    redacted: Any = None
    tool_call_ids: list[str] = []
    gate_cookie: str | None = None

    def failed(error: str) -> RunResult:
        """An error result that **keeps whatever was already captured**.

        A four-turn escalation case where the flag comes out on turn two and
        turn four hits a 429 is still a disclosure. §8 rule 1 defeats a cell on
        any capture on any repeat, `RunResult` permits `captured` on an error
        outcome, and `_cell` counts captures on error records — so the runner
        discarding them was the only thing losing the evidence, and it lost it
        in the direction that flatters the result.
        """
        return finish("error", error=error, turn_count=turn_count, captured=tuple(captured))

    try:
        for phase_turns in turns:
            async with ArenaClient(
                config.base_url,
                access_code=config.access_code,
                bypass_token=config.bypass_token,
                transport=config.transport,  # type: ignore[arg-type]
                gate_cookie=gate_cookie,
            ) as client:
                # Gate once and carry the token across phases. `schemas.md` §4
                # makes each phase a fresh session, not a fresh visitor.
                gate_cookie = await client.gate()
                await client.start_session(
                    persona=execution.persona,
                    agent=case.agent.value,
                    level=execution.level.value,
                    run_id=config.run_id,
                )
                for text in phase_turns:
                    outcome = await client.send(text)
                    turn_count += 1
                    tokens = {
                        "input": tokens["input"] + outcome.tokens.get("input", 0),
                        "output": tokens["output"] + outcome.tokens.get("output", 0),
                    }
                    for entry in outcome.captured:
                        if entry not in captured:
                            captured.append(entry)
                    # §5's refs. The last turn's frame describes the whole
                    # transcript — the site rewrites the same object each turn —
                    # so the latest non-null wins rather than the first.
                    if outcome.transcript_ref:
                        transcript_ref = outcome.transcript_ref
                    if outcome.redacted_transcript is not None:
                        redacted = outcome.redacted_transcript
                    # Accumulated across turns and de-duplicated: the site
                    # reports every record for the session on each frame, so a
                    # three-turn case would otherwise name its first call three
                    # times.
                    for call_id in outcome.tool_call_ids:
                        if call_id not in tool_call_ids:
                            tool_call_ids.append(call_id)
                    if outcome.error:
                        return failed(outcome.error)
    except RateLimited as limited:
        return failed(str(limited))
    except SiteError as exc:
        return failed(f"{type(exc).__name__}: {exc}")
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        # A read timeout, a reset connection, a truncated SSE line. Over a
        # ninety-minute run against Cloud Run these are expected, and letting
        # one propagate killed the whole run through `asyncio.gather` — taking
        # the in-flight writes with it, which is how the result file acquires
        # the corrupt line `ResultStore` then refuses to resume past.
        return failed(f"{type(exc).__name__}: {exc}")

    redacted_ref = _write_redacted(config, execution, redacted)
    return finish(
        "captured" if captured else "not_captured",
        captured=tuple(captured),
        turn_count=turn_count,
        token_usage=tokens,
        transcript_ref=transcript_ref,
        redacted_transcript_ref=redacted_ref,
        tool_call_ids=tuple(tool_call_ids),
    )


def _resolve_fixtures(case: AttackCase, fixtures: dict[str, str]) -> list[tuple[str, ...]]:
    """Substitute operator-supplied identifiers into a case's turns.

    Raises `KeyError` naming the first missing one. Deployment values never
    enter the repository (POR-6) and the manifest is never read
    (`schemas.md` §3), so a case that needs an id declares the name and the
    operator supplies the value.
    """
    resolved = []
    for phase in case.phases:
        rendered = []
        for turn in phase.turns:
            for name in case.requires_fixtures:
                token = "{" + name + "}"
                if token in turn:
                    if name not in fixtures:
                        raise KeyError(name)
                    turn = turn.replace(token, fixtures[name])
            rendered.append(_encode_regions(turn))
        resolved.append(tuple(rendered))
    return resolved


#: `{b64:...}` — base64 applied *after* fixture substitution.
_B64_REGION = re.compile(r"\{b64:(.*?)\}", re.S)


def _encode_regions(turn: str) -> str:
    """Encode `{b64:...}` regions, once the fixtures inside them are resolved.

    The `encoding_obfuscation` family hides its instruction from a classifier
    by encoding it, which means the target name is inside the encoded blob. A
    case written with the blob pre-encoded therefore cannot reference a fixture
    at all — substitution runs over the turn text and never sees inside the
    base64 — so those cases hardcoded a service name and silently stopped being
    cross-team whenever the seed moved it. Encoding here, after substitution,
    is what lets the family use the same fixtures as every other family.

    Deliberately not a general template language: one prefix, no nesting, and
    anything that is not `{b64:...}` is left exactly as written.
    """
    return _B64_REGION.sub(
        lambda m: base64.b64encode(m.group(1).encode("utf-8")).decode("ascii"), turn
    )


def _write_redacted(config: RunConfig, execution: Execution, document: Any) -> str | None:
    """Write the redacted transcript beside the run results, and return its path.

    **The suite writes this one and not the raw one, and the split is the whole
    point.** Raw transcripts hold live flag values and stay in the evidence
    bucket the site wrote them to; this copy carries markers instead, so it is
    safe in git — and SC-6 asks a deployer to rebuild the matrix from a run's
    results, which is a great deal easier when the evidence sits next to them
    than when it sits behind a bucket permission.

    Returns `None` when the site sent nothing, rather than naming a file that
    does not exist. A deployment with no evidence store still redacts, so a
    missing document here means the turn never completed.
    """
    if document is None:
        return None
    directory = Path(config.results_dir) / config.run_id
    directory.mkdir(parents=True, exist_ok=True)
    name = (
        f"{execution.case.id}-{execution.level.value}-"
        f"{execution.model.provider}-r{execution.repeat_index}.json"
    )
    path = directory / name
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def _is_rate_limited(result: RunResult) -> bool:
    """Both doors, by the one shape they share.

    A site-side 429 arrives as `RateLimited`, whose `__str__` opens with this
    prefix; a provider-side 429 is rendered into the same prefix by the client
    from the SSE `reason`. Nothing else in the runner writes it.
    """
    return result.outcome == "error" and (result.error or "").startswith("rate limited")


def _limit_name(result: RunResult) -> str:
    return (result.error or "rate limited").split("(")[0].strip()


def _backoff_seconds(attempt: int, error: str, jitter: float) -> float:
    """Exponential, jittered, and never below what the site asked for.

    `jitter` is a unit float so the caller owns the randomness and a test can
    make this a pure function. It spreads by ±25%: without it every worker
    throttled by the same capacity event returns at the same instant and
    re-creates the burst that caused it.
    """
    advised = _RETRY_AFTER.search(error or "")
    base = RETRY_BASE_SECONDS * (2**attempt)
    if advised is not None:
        base = max(base, float(advised.group(1)))
    # Capped *after* jitter, not before: capping first let the spread carry the
    # delay back over the ceiling, and a 3600s `Retry-After` on a per-hour
    # limit came out at 75s against a 60s cap.
    return min(RETRY_CAP_SECONDS, base * (0.75 + jitter * 0.5))


async def execute_with_retry(execution: Execution, config: RunConfig) -> tuple[RunResult, Counter]:
    """`execute_one`, re-attempted while it is rate limited.

    **The whole execution, never the turn.** A site-side 429 is raised before
    the turn is processed and could safely be retried in place, but a
    provider-side one is not: the deployment has already appended the user
    message and run whatever tools the turn reached, so re-posting it would
    double-execute tools and corrupt the conversation. A fresh execution opens
    a fresh session and starts clean, which costs the work already done and is
    the only version that is correct.

    Returns the throttles seen as well as the result, because a limit that
    cost ninety seconds bound the run whether or not the retry then succeeded
    — NFR-2 asks which limit bound it, and counting only the fatal ones would
    publish `none hit` for a run that spent half its time waiting.
    """
    throttles: Counter = Counter()
    for attempt in range(config.retry_limit + 1):
        result = await execute_one(execution, config)
        if not _is_rate_limited(result):
            return result, throttles
        throttles[_limit_name(result)] += 1
        if attempt == config.retry_limit:
            return result, throttles
        # Slept inside the caller's semaphore on purpose: holding the slot is
        # backpressure, and releasing it would let a queued worker take the
        # capacity this one is waiting for.
        await asyncio.sleep(_backoff_seconds(attempt, result.error or "", random.random()))
    raise AssertionError("unreachable: the loop returns on its last attempt")


@dataclass
class RunSummary:
    """NFR-2's published numbers, plus the limit that bounded the run."""

    attempted: int = 0
    skipped_already_done: int = 0
    outcomes: Counter = field(default_factory=Counter)
    limits_hit: Counter = field(default_factory=Counter)
    runtime_ms: int = 0

    @property
    def binding_limit(self) -> str | None:
        """The provider limit actually hit, per NFR-2.

        `None` means the run was not throttled, which is a publishable result
        and not a missing measurement — but it is only meaningful next to
        `runtime_ms`, which is why both go in the summary.
        """
        if not self.limits_hit:
            return None
        return self.limits_hit.most_common(1)[0][0]


async def run(cases: Sequence[AttackCase], config: RunConfig, store: ResultStore) -> RunSummary:
    """The whole run. Resumable, incrementally written, one writer.

    Concurrency is per provider: a semaphore keyed on `model.provider`, so a
    throttled provider does not idle the others. Results are recorded by the
    gathering coroutine rather than by the workers, which is what keeps
    `ResultStore` free of locking it would otherwise need.
    """
    summary = RunSummary()
    began = time.monotonic()

    executions = plan(cases, config)
    pending = [e for e in executions if not store.already_done(e.key)]
    summary.skipped_already_done = len(executions) - len(pending)

    gates = {model.provider: asyncio.Semaphore(config.concurrency) for model in config.models}
    writes = asyncio.Lock()

    async def worker(execution: Execution) -> None:
        async with gates[execution.model.provider]:
            result, throttles = await execute_with_retry(execution, config)
        async with writes:
            store.record(result)
            summary.attempted += 1
            summary.outcomes[result.outcome] += 1
            # Every throttle, including the ones a retry then survived. Both
            # doors are counted: NFR-2 publishes one limit, and a run bound by
            # Vertex capacity is as bound as one bound by NFR-3.
            summary.limits_hit.update(throttles)

    # `return_exceptions=True` so a worker that raises despite `execute_one`'s
    # handling cannot take the run with it — and, more importantly, cannot
    # destroy sibling tasks mid-write, which is how the result file acquires a
    # truncated line that then blocks the resume.
    outcomes = await asyncio.gather(
        *(worker(execution) for execution in pending), return_exceptions=True
    )
    for raised in outcomes:
        if isinstance(raised, BaseException):
            summary.outcomes["worker_crashed"] += 1
            logging.exception("suite worker crashed", exc_info=raised)
    summary.runtime_ms = int((time.monotonic() - began) * 1000)
    return summary


__all__ = [
    "DEFAULT_CONCURRENCY",
    "MAX_SAFE_CONCURRENCY",
    "Execution",
    "RunConfig",
    "RunSummary",
    "RunnerRefusal",
    "execute_one",
    "execute_with_retry",
    "plan",
    "run",
]
