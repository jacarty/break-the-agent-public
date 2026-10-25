"""Build one `schemas.md` §8 matrix from several runs' results.

## Why this exists

`suite.runner` writes a matrix for the invocation it just performed. That is
enough only while a run is one invocation, and FR-22 makes it three: a
deployment serves one model per process (`BTA_SITE_PROVIDER`), so
`execute._refuse_unsafe` refuses more than one model pin and tells the operator
to *"run once per provider with `BTA_SITE_PROVIDER` rotated and concatenate the
result files."* Nothing stood at the end of that sentence. §8 rule 2 requires
all three providers to have run, so the merged document was the only one that
could ever read `held`, and it was the one document nothing could build.

TD-33 names this as half of its closure: *"the reference run is defined as
three invocations and the matrix builder is given a documented way to merge
them."* This is that way.

## What it refuses

**Rows that collide on `schemas.md` §5's uniqueness tuple.** Merging two runs
of the same provider — or a file with itself — would count one execution twice,
inflating `cases_run` toward `min_cases` with no additional evidence. That is
the paper route to `held` in a different costume, so it is a refusal rather
than a de-duplication: silently dropping the duplicate would hide that the
operator merged the wrong files.

**Mixed schema versions**, because a field that moved between versions would be
read under the wrong meaning by an aggregator that cannot see the difference.

## What it derives rather than accepts

`run_id` is computed from the source run ids, so the same inputs rebuild the
same document in any order — SC-6's rebuild-and-compare holds across the merged
case without depending on an operator retyping a name. `source_runs` records
what went in.

`models` still comes from the records themselves, in `build`, and never from
the file list. A provider that was pinned but produced no rows must not be able
to satisfy rule 2's provider clause by appearing in an argument list.

`runtime_ms` is the sum of each source run's own span, measured from its rows
rather than passed in. The invocations are necessarily sequential — one
deployment, one model at a time — so the sum is the honest wall clock for the
run as a whole, and it is derivable, which a number carried on the command line
would not be.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from suite.matrix.build import SCHEMA_VERSION, build

REPO_ROOT = Path(__file__).resolve().parents[2]


class MergeRefusal(RuntimeError):
    """The inputs would produce a misleading document."""


def _key(row: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(row["case_id"]),
        str(row["level"]),
        str(row["model"]["identifier"]),
        int(row["repeat_index"]),
    )


def _read_file(path: Path) -> dict[tuple[str, str, str, int], dict[str, Any]]:
    """One file's executions, each as its latest attempt.

    **Within a file a repeated key is a retry, not a duplicate.** The runner's
    store is append-only and a resume re-attempts errors, so an execution that
    failed and then succeeded has two rows; the second supersedes the first,
    exactly as `ResultStore.read_all` treats it. Across files the same key
    means two runs did the same work and neither supersedes the other, which
    is the refusal below.
    """
    latest: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            latest[_key(row)] = row
        except json.JSONDecodeError as exc:
            raise MergeRefusal(f"{path}:{number} is not valid JSON: {exc}") from exc
        except (KeyError, TypeError) as exc:
            raise MergeRefusal(f"{path}:{number} is not a run result ({exc})") from exc
    return latest


def _read_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    claimed: dict[tuple[str, str, str, int], Path] = {}
    for path in paths:
        for key, row in _read_file(path).items():
            first = claimed.get(key)
            if first is not None:
                raise MergeRefusal(
                    f"execution {key} appears in {first} and again in {path}. "
                    f"`schemas.md` §5 makes that tuple unique across a run, so one "
                    f"of these files repeats the other's work. Counting it twice "
                    f"would raise `cases_run` toward `min_cases` on no new evidence."
                )
            claimed[key] = path
            rows.append(row)
    if not rows:
        raise MergeRefusal("no run results in the given files")
    return rows


def _refuse_unmergeable(rows: list[dict[str, Any]]) -> None:
    versions = {row.get("schema_version") for row in rows}
    if versions != {SCHEMA_VERSION}:
        raise MergeRefusal(
            f"result files carry schema versions {sorted(map(str, versions))}, and this "
            f"builder reads version {SCHEMA_VERSION}. A field whose meaning changed "
            f"between versions would be aggregated under the wrong one."
        )


def _source_runs(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row["run_id"]) for row in rows})


def _merged_run_id(source_runs: list[str]) -> str:
    digest = hashlib.sha256("\n".join(source_runs).encode("utf-8")).hexdigest()
    return f"R-merged-{digest[:8]}"


def _instant(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000.0


def _runtime_ms(rows: list[dict[str, Any]]) -> int:
    """Each source run's own span, summed.

    Measured from the rows so that rebuilding gives the same number. A run's
    span is its first start to its last finish, which under concurrency is
    wall clock rather than the sum of session durations — summing
    `duration_ms` across rows would report the compute, and NFR-2 publishes
    the wall clock.
    """
    spans: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        started = _instant(str(row["started_at"]))
        spans[str(row["run_id"])].extend((started, started + float(row.get("duration_ms", 0))))
    return int(sum(max(points) - min(points) for points in spans.values()))


def _load_json_dir(directory: Path, what: str) -> list[dict[str, Any]]:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise MergeRefusal(f"no {what} found in {directory}")
    loaded: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        loaded.extend(payload if isinstance(payload, list) else [payload])
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m suite.matrix",
        description="Merge run results from several invocations into one §8 matrix.",
    )
    parser.add_argument("results", nargs="+", type=Path, help="run result files (JSONL)")
    parser.add_argument("--out", type=Path, help="write here instead of stdout")
    parser.add_argument("--cases-dir", type=Path, default=REPO_ROOT / "suite" / "cases")
    parser.add_argument("--flags-dir", type=Path, default=REPO_ROOT / "flags")
    args = parser.parse_args(argv)

    try:
        rows = _read_rows(args.results)
        _refuse_unmergeable(rows)
        cases = _load_json_dir(args.cases_dir, "attack cases")
        flags = _load_json_dir(args.flags_dir, "flag definitions")
    except MergeRefusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2

    source_runs = _source_runs(rows)
    document = build(
        _merged_run_id(source_runs),
        rows,
        cases=cases,
        flags=flags,
        runtime_ms=_runtime_ms(rows),
        source_runs=source_runs,
    )

    rendered = json.dumps(document, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(f"{args.out}: {len(document['cells'])} cells from {len(source_runs)} runs")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
