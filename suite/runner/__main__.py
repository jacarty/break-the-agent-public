"""`uv run python -m suite.runner` — the operator's entry point.

Every deployment value comes from the environment (POR-5, POR-6). Nothing here
is hardcoded and there is no config file, for the reason POR-6c gives: a
second, hand-maintained copy of a deployment's configuration on a laptop drifts
from the real one, and on this project the interesting content is exactly what
would be in it.

    export BTA_SITE_URL=https://…
    export BTA_ACCESS_CODE=…            # FR-1
    export BTA_SUITE_BYPASS_TOKEN=…     # NFR-3a — lifts rate limits, not the
                                        #          daily token ceiling
    export BTA_MODEL_GOOGLE=…           # FR-22a, pinned, recorded per run
    export BTA_HARNESS_OTHER_INCIDENT=… # fixture ids the cases declare
    export BTA_HARNESS_OTHER_EMPLOYEE=…
    export BTA_HARNESS_OWN_EMPLOYEE=…

    uv run python -m suite.runner --level L1 --out evidence/runs/R-001.jsonl

`--level` is not a convenience. TD-20 means a memory-bearing agent may not
cross levels in one run while only one persona is selectable, and the runner
refuses rather than producing an L2 column contaminated by L1's memory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from bta.models import Level
from suite.cases import coverage, load_all
from suite.matrix import build
from suite.matrix.build import THRESHOLDS
from suite.runner.execute import (
    DEFAULT_CONCURRENCY,
    RETRY_LIMIT,
    RunConfig,
    RunnerRefusal,
    run,
)
from suite.runner.results import ModelPin, ResultStore

#: Provider → the environment variable holding its pinned identifier (FR-22a).
MODEL_VARIABLES = {
    "google": "BTA_MODEL_GOOGLE",
    "anthropic": "BTA_MODEL_ANTHROPIC",
    "xai": "BTA_MODEL_XAI",
}

#: Case fixture name → the environment variable the operator supplies it in.
#: The same variables `bta.tools.harness` reads, deliberately: an operator who
#: has run the harness has already exported these.
FIXTURE_VARIABLES = {
    "other_incident_id": "BTA_HARNESS_OTHER_INCIDENT",
    "other_employee_id": "BTA_HARNESS_OTHER_EMPLOYEE",
    "own_employee_id": "BTA_HARNESS_OWN_EMPLOYEE",
    # Service names, added 2026-08-29. Until then 37 of the 108 cases named a
    # service as a literal — `checkout-api`, mostly — and which team owns a
    # given service is decided by the seed (`corpus.build_services` deals teams
    # round-robin). So a case was cross-team on one seed and a caller's own
    # perfectly legitimate request on the next, where it is served correctly,
    # records `not_captured`, and pushes the cell toward `held` for a reason
    # that has nothing to do with any control. Observed live: an F2 attempt on
    # `checkout-api` after a reseed that had moved it to `team-a`.
    #
    # **Three of these name a carrier, not just a team**, because the flags do
    # not share one. `_place_f2` picks a random team-b *artefact* and `_place_f3`
    # picks independently, so the service whose build manifest holds F2 is
    # generally not the one whose logs hold F3. A single `other_service` would
    # have aimed two thirds of the cases at the wrong resource while looking
    # correct.
    "artefact_service": "BTA_HARNESS_ARTEFACT_SERVICE",
    # F3's *injection* carrier, not its value: `_place_f3` puts the value in a
    # second team-b postmortem and plants the instruction to fetch it in a log
    # line for that incident's service. So this is the service whose logs must
    # be read for the injection to fire, and it is not readable off F3's
    # manifest `resource`, which names where the value ended up.
    "log_service": "BTA_HARNESS_LOG_SERVICE",
    "incident_service": "BTA_HARNESS_INCIDENT_SERVICE",
    # Any service another team owns, for cases that need a cross-team target
    # rather than a flag-bearing one — F7's planted directives, and the second
    # slot wherever a case names two.
    "other_service": "BTA_HARNESS_OTHER_SERVICE",
    "other_service_alt": "BTA_HARNESS_OTHER_SERVICE_ALT",
}


def _models(pinned_at: str) -> tuple[ModelPin, ...]:
    pins = [
        ModelPin(provider=provider, identifier=value, pinned_at=pinned_at)
        for provider, variable in MODEL_VARIABLES.items()
        if (value := os.environ.get(variable, "").strip())
    ]
    return tuple(pins)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--level", action="append", choices=[lv.value for lv in Level])
    parser.add_argument("--flag", action="append", help="Restrict to one or more cells.")
    parser.add_argument("--out", required=True, help="JSONL results file. Resumed if it exists.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument(
        "--retry-limit",
        type=int,
        default=RETRY_LIMIT,
        help=(
            "Re-attempts for a rate-limited execution. 0 restores the "
            "behaviour where one 429 costs the cell a case."
        ),
    )
    parser.add_argument("--persona", default="alice")
    parser.add_argument(
        "--memory-is-persistent",
        action="store_true",
        help=(
            "Assert that the deployment's memory store survives a session "
            "boundary, so F7's multi-session cases can execute. An operator "
            "assertion, never an observation — see RunConfig."
        ),
    )
    parser.add_argument(
        "--matrix",
        default=None,
        help="Also write the schemas.md §8 matrix here when the run completes.",
    )
    parser.add_argument(
        "--coverage-only",
        action="store_true",
        help="Report cases and families per cell against SC-3 and exit. No network, no spend.",
    )
    args = parser.parse_args(argv)

    # Default the case filter to the same level the run defaults to. Without
    # this, cases kept all three levels while `personas` held only L1, and the
    # run failed with "no persona configured for L2" — technically true, and
    # not the TD-20 explanation the situation actually calls for.
    args.level = args.level or [Level.L1.value]

    cases = load_all()
    if args.flag:
        cases = tuple(c for c in cases if c.flag_id in set(args.flag))
    if args.level:
        # Narrow each case to the requested levels rather than dropping cases
        # that also declare others — a case is authored once and scored per
        # cell, so filtering by level filters executions, not cases.
        wanted = {Level(value) for value in args.level}
        narrowed = []
        for case in cases:
            kept = tuple(level for level in case.levels if level in wanted)
            if kept:
                narrowed.append(replace(case, levels=kept))
        cases = tuple(narrowed)

    if args.coverage_only:
        return _report_coverage(cases)

    run_id = args.run_id or f"R-{datetime.now(UTC).strftime('%Y-%m-%d-%H%M%S')}"
    pinned_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    models = _models(pinned_at)
    if not models:
        print(
            f"No model pinned. Set at least one of {', '.join(MODEL_VARIABLES.values())} (FR-22a).",
            file=sys.stderr,
        )
        return 2

    levels = args.level
    config = RunConfig(
        run_id=run_id,
        base_url=os.environ.get("BTA_SITE_URL", ""),
        access_code=os.environ.get("BTA_ACCESS_CODE", ""),
        bypass_token=os.environ.get("BTA_SUITE_BYPASS_TOKEN", ""),
        models=models,
        repeats=args.repeats,
        concurrency=args.concurrency,
        retry_limit=args.retry_limit,
        personas={level: args.persona for level in levels},
        memory_is_persistent=args.memory_is_persistent,
        fixtures={
            name: value
            for name, variable in FIXTURE_VARIABLES.items()
            if (value := os.environ.get(variable, "").strip())
        },
    )
    if not config.base_url:
        print("BTA_SITE_URL is not set.", file=sys.stderr)
        return 2

    # `_write_matrix` applies the registered THRESHOLDS, so a run with fewer
    # repeats produces a matrix in which no case is complete: `cases_run: 0`,
    # `insufficient` everywhere, under a `thresholds` block claiming 3. That
    # reads as a result and is an artefact of the flag.
    if args.matrix and args.repeats < THRESHOLDS["repeats"]:
        print(
            f"--repeats {args.repeats} is below the registered threshold of "
            f"{THRESHOLDS['repeats']} (SC-3), so every cell would report "
            f"`insufficient` for want of complete repeat sets rather than for "
            f"want of evidence. Drop --matrix to run a partial pass, or raise "
            f"--repeats.",
            file=sys.stderr,
        )
        return 2

    store = ResultStore(Path(args.out))
    try:
        summary = asyncio.run(run(cases, config, store))
    except RunnerRefusal as refusal:
        print(f"\nRefused before spending anything:\n\n{refusal}\n", file=sys.stderr)
        return 2

    print(f"run {run_id}: {summary.attempted} executed, {summary.skipped_already_done} resumed")
    for outcome, count in sorted(summary.outcomes.items()):
        print(f"  {outcome}: {count}")
    # NFR-2 publishes both, and "not throttled" is a result rather than a gap.
    print(f"  runtime: {summary.runtime_ms} ms")
    print(f"  binding provider limit: {summary.binding_limit or 'none hit'}")

    if args.matrix:
        _write_matrix(run_id, store, cases, summary.runtime_ms, Path(args.matrix))
        print(f"  matrix: {args.matrix}")
    return 0


def _report_coverage(cases: tuple) -> int:
    """SC-3 reachability, before any inference is paid for.

    `build-plan.md` §6 names underestimated case authoring as the route to
    `insufficient` verdicts by accident, and asks for the count from the start
    of the phase rather than the end. A cell short of the threshold here cannot
    reach `held` however the run goes.
    """
    short = 0
    for (cell, level), entry in sorted(coverage(cases).items()):
        families = len(entry["families"])
        ok = entry["cases"] >= THRESHOLDS["min_cases"] and families >= THRESHOLDS["min_families"]
        short += 0 if ok else 1
        mark = "ok " if ok else "LOW"
        print(f"{mark} {cell:>4} {level}  cases={entry['cases']:>3}  families={families}")
    print(
        f"\n{short} cell(s) below SC-3's floor of {THRESHOLDS['min_cases']} cases "
        f"across {THRESHOLDS['min_families']} families."
    )
    return 0 if short == 0 else 1


def _write_matrix(
    run_id: str, store: ResultStore, cases: tuple, runtime_ms: int, path: Path
) -> None:
    flags_dir = Path(__file__).resolve().parents[2] / "flags"
    flags = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(flags_dir.glob("*.json"))]
    document = build(
        run_id,
        store.read_all(),
        cases=[c.to_dict() for c in cases],
        flags=flags,
        runtime_ms=runtime_ms,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
