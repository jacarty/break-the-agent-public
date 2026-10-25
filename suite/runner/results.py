"""`schemas.md` §5 — the run result, and the incremental store that makes a run resumable.

## Why results are written as they are produced

`build-plan.md` §3 puts a full run on the order of 2,700 sessions, bounded by
provider rate limits nobody here controls. A run that holds everything in
memory and writes at the end is a run where an abuse-detection trip four fifths
of the way through costs the whole thing — which the risk register names twice.

So each result is appended as one JSON line the moment it completes, and a
resumed run reads back what is already there and skips those executions. The
unique key is `(case_id, level, model.identifier, repeat_index)`, exactly as
`schemas.md` §5 defines it — not a line number and not a position in an
iteration order, either of which would silently re-map if the case set grew.

JSONL rather than a JSON array, because an array has to be closed to be valid
and a run killed mid-write would leave an unparseable file — the one failure
mode that turns a partial run into no run at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

#: `schemas.md` §5. `error` contributes nothing to the SC-3 threshold: a case
#: that failed to execute has not tested anything, and an error on one repeat
#: does not invalidate its siblings.
OUTCOMES = frozenset({"captured", "not_captured", "error"})


@dataclass(frozen=True, slots=True)
class ModelPin:
    """FR-22a. Closes the half of TD-18 that had no consumer.

    `bta.config.ModelPin` has existed since phase 3 and was never constructed,
    because a pin needs a record with a `model` object to live on and nothing
    built one until the runner. `pinned_at` did not exist anywhere in the tree.
    """

    provider: str
    identifier: str
    pinned_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "identifier": self.identifier,
            "pinned_at": self.pinned_at,
        }


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    case_id: str
    flag_id: str
    level: str
    model: ModelPin
    repeat_index: int
    started_at: str
    duration_ms: int
    outcome: str
    captured: tuple[dict[str, Any], ...] = ()
    turn_count: int = 0
    tool_call_ids: tuple[str, ...] = ()
    token_usage: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})
    transcript_ref: str | None = None
    redacted_transcript_ref: str | None = None
    error: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome {self.outcome!r} is not in schemas.md §5")
        # `captured` and `outcome` must agree or the matrix counts one thing
        # and reports another. §5: `captured` is set when there is any entry on
        # either channel; the channel split governs the verdict, not this field.
        if self.outcome == "captured" and not self.captured:
            raise ValueError("outcome 'captured' with no captured entries")
        if self.outcome == "not_captured" and self.captured:
            raise ValueError("outcome 'not_captured' with captured entries")

    @property
    def key(self) -> tuple[str, str, str, int]:
        """`schemas.md` §5's uniqueness tuple, and the resume key."""
        return (self.case_id, self.level, self.model.identifier, self.repeat_index)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "flag_id": self.flag_id,
            "level": self.level,
            "model": self.model.to_dict(),
            "repeat_index": self.repeat_index,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "outcome": self.outcome,
            "captured": list(self.captured),
            "turn_count": self.turn_count,
            "tool_call_ids": list(self.tool_call_ids),
            "token_usage": dict(self.token_usage),
            "transcript_ref": self.transcript_ref,
            "redacted_transcript_ref": self.redacted_transcript_ref,
            "error": self.error,
        }


def key_of(record: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(record["case_id"]),
        str(record["level"]),
        str(record["model"]["identifier"]),
        int(record["repeat_index"]),
    )


class ResultStore:
    """Append-only JSONL, with the completed keys read back on open.

    Not thread-safe and not meant to be: the runner has many concurrent
    sessions and exactly one writer, which is why `record` is called from the
    orchestrating coroutine rather than from the workers.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._done: set[tuple[str, str, str, int]] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    # **Errors stay in the file and do not count as done.**
                    # Every cell carries exactly twelve cases against a floor of
                    # twelve, and §8 rule 2 excludes any case without a complete
                    # non-error repeat set — so one transient 429 permanently
                    # drops a cell to `insufficient`, and a resume that treated
                    # the error as finished could never repair it. The only
                    # recovery was hand-editing the evidence file, which is
                    # worse than re-attempting.
                    #
                    # This weakens no threshold: the error row is kept as
                    # evidence and counted in `errors`, and a re-attempt writes
                    # a second row that the matrix reads on the same terms as
                    # any other.
                    if record.get("outcome") != "error":
                        self._done.add(key_of(record))
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    # **This refuses any unreadable line, including the last,
                    # and the earlier comment here claimed otherwise.** JSONL is
                    # used so that an interrupted run leaves the *completed*
                    # rows readable, not so that a damaged row is silently
                    # dropped — a dropped row is an execution the resume would
                    # redo, which is harmless, or a capture that vanishes, which
                    # is not. The operator is told which line and what to do.
                    raise ValueError(
                        f"{self.path}:{number} is not a readable run result ({exc}). "
                        f"If this is the last line, truncate it and resume; "
                        f"otherwise the file is corrupt and the run should restart."
                    ) from None

    def already_done(self, key: tuple[str, str, str, int]) -> bool:
        return key in self._done

    @property
    def completed(self) -> int:
        return len(self._done)

    def record(self, result: RunResult) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result.to_dict(), separators=(",", ":")) + "\n")
            handle.flush()
        if result.outcome != "error":
            self._done.add(result.key)

    def read_all(self) -> list[dict[str, Any]]:
        """Every execution once, as its **latest** attempt.

        The file is append-only and a resume re-attempts errors, so a retried
        execution has two rows: the error and what replaced it. `schemas.md`
        §5 makes the key unique, and `_load`'s note above claimed the matrix
        would read the second row "on the same terms as any other" — it read
        *both*. The first live run ended at 426 rows for 324 executions, which
        double-counted `errors`, double-counted captures, and counted one
        repeat twice toward the completeness test in §8 rule 2 — inflating
        `cases_run` toward `min_cases` on evidence that did not exist.

        Last-wins rather than a rewritten file: append-only is what makes an
        interrupted run's completed rows readable, and the superseded attempt
        stays on disk as the record of what happened.
        """
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        latest: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        for row in rows:
            latest[key_of(row)] = row
        return list(latest.values())


__all__ = ["OUTCOMES", "ModelPin", "ResultStore", "RunResult", "key_of"]
