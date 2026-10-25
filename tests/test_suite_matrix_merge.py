"""`python -m suite.matrix` — the merged document FR-22 makes necessary.

A deployment serves one model per process, so a three-provider run is three
invocations and §8 rule 2's "all three providers ran" is only ever satisfiable
by a document nothing could build until this module existed (TD-33).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from suite.matrix.__main__ import main

FLAGS = [{"id": "F1", "expected": {"L1": "defeated", "L2": "defeated", "L3": "held"}}]
CASES = [{"id": f"AC-F1-{n:03d}", "technique_family": f"family_{n % 4}"} for n in range(1, 13)]


def row(run_id: str, provider: str, case: int, repeat: int, *, started: int = 0, ms: int = 1000):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "case_id": f"AC-F1-{case:03d}",
        "flag_id": "F1",
        "level": "L3",
        "model": {"provider": provider, "identifier": f"{provider}-x", "pinned_at": "t"},
        "repeat_index": repeat,
        "started_at": f"2026-09-01T10:{started:02d}:00.000Z",
        "duration_ms": ms,
        "outcome": "not_captured",
        "captured": [],
    }


def run_file(tmp_path: Path, run_id: str, provider: str, **kw) -> Path:
    path = tmp_path / f"{run_id}.jsonl"
    path.write_text(
        "".join(
            json.dumps(row(run_id, provider, case, repeat, **kw)) + "\n"
            for case in range(1, 13)
            for repeat in range(3)
        ),
        encoding="utf-8",
    )
    return path


def build_merged(tmp_path: Path, paths: list[Path], cases=CASES, flags=FLAGS) -> dict:
    cases_dir, flags_dir, out = tmp_path / "c", tmp_path / "f", tmp_path / "m.json"
    for directory, payload in ((cases_dir, cases), (flags_dir, flags)):
        directory.mkdir(exist_ok=True)
        (directory / "a.json").write_text(json.dumps(payload), encoding="utf-8")
    code = main(
        [
            *[str(p) for p in paths],
            "--out",
            str(out),
            "--cases-dir",
            str(cases_dir),
            "--flags-dir",
            str(flags_dir),
        ]
    )
    assert code == 0
    return json.loads(out.read_text(encoding="utf-8"))


def three_runs(tmp_path: Path) -> list[Path]:
    return [
        run_file(tmp_path, "R-a", "google", started=0),
        run_file(tmp_path, "R-b", "anthropic", started=10),
        run_file(tmp_path, "R-c", "xai", started=20),
    ]


def test_three_invocations_become_one_document_that_can_reach_a_verdict(tmp_path):
    """The point of the module. One invocation can never satisfy rule 2's
    provider clause, so `held` is unreachable until the three are merged."""
    document = build_merged(tmp_path, three_runs(tmp_path))
    assert len(document["models"]) == 3
    assert document["cells"][0]["verdict"] == "held"


def test_the_merged_id_is_derived_from_its_sources_and_not_from_their_order(tmp_path):
    """SC-6: rebuilding from the same results gives the same document. An id
    that depended on argument order, or on an operator retyping a name, would
    make that a matter of discipline rather than of the input."""
    paths = three_runs(tmp_path)
    forward = build_merged(tmp_path, paths)
    backward = build_merged(tmp_path, list(reversed(paths)))

    assert forward["run_id"] == backward["run_id"]
    assert forward["run_id"].startswith("R-merged-")
    assert forward["source_runs"] == ["R-a", "R-b", "R-c"] == backward["source_runs"]


def test_a_single_run_still_reports_its_source(tmp_path):
    document = build_merged(tmp_path, [run_file(tmp_path, "R-a", "google")])
    assert document["source_runs"] == ["R-a"]
    assert document["cells"][0]["verdict"] == "insufficient", "one provider cannot hold"


def test_merging_a_run_with_itself_is_refused(tmp_path, capsys):
    """The paper route to `held` in a different costume: duplicate executions
    raise `cases_run` toward `min_cases` on no new evidence. Refused rather
    than de-duplicated, because dropping the duplicate would hide that the
    wrong files were merged."""
    path = run_file(tmp_path, "R-a", "google")
    twin = tmp_path / "copy.jsonl"
    twin.write_text(path.read_text(encoding="utf-8").replace("R-a", "R-b"), encoding="utf-8")

    assert main([str(path), str(twin)]) == 2
    assert "appears in" in capsys.readouterr().err


def test_mixed_schema_versions_are_refused(tmp_path, capsys):
    path = run_file(tmp_path, "R-a", "google")
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = json.dumps({**json.loads(lines[0]), "schema_version": 2})
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert main([str(path)]) == 2
    assert "schema version" in capsys.readouterr().err


def test_runtime_is_each_run_s_own_span_summed(tmp_path):
    """Sequential invocations, so the sum is the run's wall clock. Summing
    every row's `duration_ms` instead would report compute, not elapsed time,
    and NFR-2 publishes elapsed."""
    document = build_merged(tmp_path, three_runs(tmp_path))
    # Each run's rows all start together and last 1000ms: a 1000ms span each.
    assert document["runtime_ms"] == 3000


def test_an_empty_input_is_refused_rather_than_producing_an_empty_matrix(tmp_path, capsys):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    assert main([str(empty)]) == 2
    assert "no run results" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["{not json", '{"schema_version": 1'])
def test_a_malformed_line_names_its_file_and_line(tmp_path, capsys, bad):
    path = tmp_path / "R-a.jsonl"
    path.write_text(bad + "\n", encoding="utf-8")
    assert main([str(path)]) == 2
    assert "R-a.jsonl:1" in capsys.readouterr().err


def test_a_retry_within_one_file_supersedes_the_attempt_it_replaces(tmp_path):
    """The runner's store is append-only and a resume re-attempts errors, so a
    retried execution has two rows. Counting both double-counted `errors`,
    double-counted captures, and counted one repeat twice toward §8 rule 2's
    completeness test — the first live run reached 426 rows for 324
    executions and inflated `cases_run` toward `min_cases` on evidence that
    did not exist."""
    path = run_file(tmp_path, "R-a", "google")
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    errored = {**first, "outcome": "error", "error": "ProviderError"}
    path.write_text(json.dumps(errored) + "\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    document = build_merged(tmp_path, [path])
    assert document["cells"][0]["errors"] == 0, "the superseded attempt was counted"
    assert document["cells"][0]["cases_run"] == 12
