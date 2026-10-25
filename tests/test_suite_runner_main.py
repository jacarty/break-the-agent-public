"""`suite/runner/__main__.py` — the operator entry point, offline.

Nothing in this module reaches a network when it is misconfigured or refused,
which is most of what is worth testing here: FR-22a's model-pin refusal, the
missing-`BTA_SITE_URL` refusal, `RunnerRefusal` surfacing as exit code 2
before `asyncio.run(run(...))` gets anywhere near `httpx`, and
`--coverage-only`, which is documented as spending nothing by design.

The one path that *would* reach a network — a real run — is exercised by
patching `suite.runner.__main__.run` itself (the coroutine `main` awaits)
rather than by giving it a real deployment, the same seam the module's own
`--coverage-only` flag exists to avoid needing. `tests/test_suite_end_to_end.py`
is where the actual HTTP protocol is proven, in process, with no network
either.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from suite.runner import __main__ as runner_main
from suite.runner.execute import RunSummary
from suite.runner.results import ModelPin, ResultStore, RunResult


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every model/deployment variable this module reads, cleared first, so a
    developer's own exported `BTA_*` values (from a real run on their machine)
    cannot make one of these tests pass or fail by accident."""
    for variable in (
        "BTA_MODEL_GOOGLE",
        "BTA_MODEL_ANTHROPIC",
        "BTA_MODEL_XAI",
        "BTA_SITE_URL",
        "BTA_ACCESS_CODE",
        "BTA_SUITE_BYPASS_TOKEN",
        "BTA_HARNESS_OTHER_INCIDENT",
        "BTA_HARNESS_OTHER_EMPLOYEE",
        "BTA_HARNESS_OWN_EMPLOYEE",
    ):
        monkeypatch.delenv(variable, raising=False)


def test_coverage_only_spends_nothing_and_needs_no_configuration(capsys, tmp_path) -> None:
    """Documented explicitly as "No network, no spend." — must work with no
    `BTA_*` variable set at all, which a first-time operator running it before
    touching any deployment configuration relies on.

    `--out` is passed only because argparse marks it required regardless of
    `--coverage-only`; it is never opened on this path."""
    out_path = tmp_path / "unused.jsonl"
    rc = runner_main.main(["--coverage-only", "--out", str(out_path)])

    out, _err = capsys.readouterr()
    assert "cell(s) below SC-3's floor" in out
    # The registered case set is asserted elsewhere (test_suite_cases.py) to
    # clear the floor for every cell, so the real entry point should agree.
    assert rc == 0
    assert not out_path.exists(), "--coverage-only opened a ResultStore it should never touch"


def test_coverage_only_narrows_by_flag_and_level(capsys, tmp_path) -> None:
    rc = runner_main.main(
        [
            "--coverage-only",
            "--flag",
            "C1",
            "--level",
            "L1",
            "--out",
            str(tmp_path / "unused.jsonl"),
        ]
    )

    out, _err = capsys.readouterr()
    lines = [line for line in out.splitlines() if line.strip().startswith(("ok", "LOW"))]
    assert lines, "expected at least one coverage row"
    for line in lines:
        assert " C1 " in line
        assert " L1 " in line
    assert rc in (0, 1)


def test_no_pinned_model_refuses_before_any_network_call(capsys) -> None:
    """FR-22a: a matrix assembled from unpinned models is not reproducible, so
    the refusal must happen before `RunConfig` is even built — no
    `BTA_SITE_URL` is set either, and if model-pinning were checked second the
    error message would misdirect an operator who only forgot one of the two."""
    rc = runner_main.main(["--out", "unused.jsonl"])

    assert rc == 2
    _out, err = capsys.readouterr()
    assert "No model pinned" in err
    assert "BTA_MODEL_GOOGLE" in err


def test_missing_site_url_refuses_after_a_model_is_pinned(monkeypatch, capsys) -> None:
    monkeypatch.setenv("BTA_MODEL_GOOGLE", "gemini-x")

    rc = runner_main.main(["--out", "unused.jsonl"])

    assert rc == 2
    _out, err = capsys.readouterr()
    assert "BTA_SITE_URL" in err


def test_a_runner_refusal_exits_2_and_reaches_no_network(monkeypatch, capsys, tmp_path) -> None:
    """`RunnerRefusal` (e.g. concurrency near `MAX_SESSIONS`, or TD-20's
    persona-per-level requirement) is raised inside `plan()`, called from
    `run()`, before any `ArenaClient` exists. This test proves the CLI's own
    exit-code contract for that path rather than re-proving `RunnerRefusal`'s
    triggers, which `test_suite_runner.py` already covers."""
    monkeypatch.setenv("BTA_MODEL_GOOGLE", "gemini-x")
    monkeypatch.setenv("BTA_SITE_URL", "https://example.invalid")
    monkeypatch.setenv("BTA_ACCESS_CODE", "code")

    rc = runner_main.main(
        ["--out", str(tmp_path / "run.jsonl"), "--concurrency", "999", "--flag", "C1"]
    )

    assert rc == 2
    _out, err = capsys.readouterr()
    assert "Refused before spending anything" in err


def test_a_successful_run_prints_the_summary_and_writes_the_matrix(
    monkeypatch, capsys, tmp_path
) -> None:
    """The `run()` coroutine itself is stubbed — this test is for the CLI's
    formatting and its `--matrix` wiring, not for the runner's own arithmetic
    (`test_suite_runner.py`) or the site protocol
    (`test_suite_end_to_end.py`)."""
    monkeypatch.setenv("BTA_MODEL_GOOGLE", "gemini-x")
    monkeypatch.setenv("BTA_SITE_URL", "https://example.invalid")
    monkeypatch.setenv("BTA_ACCESS_CODE", "code")

    pin = ModelPin(provider="google", identifier="gemini-x", pinned_at="t")
    result = RunResult(
        run_id="R-test",
        case_id="AC-C1-001",
        flag_id="C1",
        level="L1",
        model=pin,
        repeat_index=0,
        started_at="2026-08-28T00:00:00.000Z",
        duration_ms=1,
        outcome="not_captured",
    )
    out_path = tmp_path / "run.jsonl"
    matrix_path = tmp_path / "matrix.json"

    async def _fake_run(cases, config, store):
        store.record(result)
        return RunSummary(
            attempted=1,
            skipped_already_done=0,
            outcomes=Counter({"not_captured": 1}),
            runtime_ms=42,
        )

    monkeypatch.setattr(runner_main, "run", _fake_run)

    rc = runner_main.main(
        [
            "--out",
            str(out_path),
            "--flag",
            "C1",
            "--level",
            "L1",
            "--matrix",
            str(matrix_path),
        ]
    )

    assert rc == 0
    out, _err = capsys.readouterr()
    assert "1 executed, 0 resumed" in out
    assert "not_captured: 1" in out
    assert "runtime: 42 ms" in out
    assert "binding provider limit: none hit" in out
    assert f"matrix: {matrix_path}" in out

    assert ResultStore(out_path).completed == 1
    document = json.loads(matrix_path.read_text())
    assert document["run_id"]
    assert document["cells"], "expected at least one aggregated cell"


def test_a_resumed_run_reports_the_skip_count(monkeypatch, capsys, tmp_path) -> None:
    """`--out` pointing at an existing results file must resume rather than
    reprocessing every execution, and the operator-facing summary is the only
    signal that resumption happened at all."""
    monkeypatch.setenv("BTA_MODEL_GOOGLE", "gemini-x")
    monkeypatch.setenv("BTA_SITE_URL", "https://example.invalid")
    monkeypatch.setenv("BTA_ACCESS_CODE", "code")

    async def _fake_run(cases, config, store):
        return RunSummary(attempted=0, skipped_already_done=5, outcomes=Counter(), runtime_ms=1)

    monkeypatch.setattr(runner_main, "run", _fake_run)

    rc = runner_main.main(["--out", str(tmp_path / "run.jsonl")])

    assert rc == 0
    out, _err = capsys.readouterr()
    assert "0 executed, 5 resumed" in out
