"""Persisting the evidence a run result points at — VER-6, `schemas.md` §5, TD-34.

§5 gives every run result a `transcript_ref` and a `redacted_transcript_ref` and
says **both are written on every run**. Before this nothing wrote either: the
transcript lived in process memory and died with the session (FR-7), so a
reference run produced capture counts and no evidence at all — nothing for a
write-up to cite, and nothing for `verifier/redaction.py` to run over, which is
the failure VER-6 exists to prevent.

**Redaction happens here, in the site process, and that placement is forced.**
The raw transcript holds live flag values — a `tool_result` capture *is* a value
in a payload — and `suite` may not import `verifier` or `bta.generator`, because
run results are committed to git (SC-6) while raw transcripts deliberately are
not (VER-6). So the suite can never hold the raw copy, and the redaction has to
happen in the one process that already holds both the transcript and the
verifier.

**The two artefacts travel differently, and deliberately.**

- **Raw** goes to the evidence bucket, written by the runtime identity under
  restricted IAM. It never leaves that bucket, never enters the suite's process
  and is never committed.
- **Redacted** is returned inline to the caller, which writes it beside the run
  results it belongs to. It contains markers rather than values, so it is safe
  in git, and SC-6 asks a deployer to rebuild the matrix from a run's results —
  evidence they already hold beats evidence behind a bucket permission.

**Only sessions carrying a `run_id` are persisted.** A person browsing the arena
generates transcripts too, and FR-7 says their session leaves nothing behind.
`run_id` is what distinguishes a suite execution from a human at a keyboard, and
it is the only thing that should cause an object to be written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from verifier.redaction import redact


class EvidenceNotConfigured(RuntimeError):
    """A bucket was configured without a writer to reach it."""


@dataclass(frozen=True, slots=True)
class Evidence:
    """What a caller needs to fill in §5's two ref fields.

    `raw_ref` is a `gs://` URI the caller may record but must not fetch;
    `redacted` is the document itself, markers in place of values.
    """

    raw_ref: str | None
    redacted: Any


class ObjectWriter(Protocol):
    """How a raw transcript reaches the bucket.

    Injected so the tests drive the real redaction and the real ref format
    without a network and without asserting on a mocking library's idea of a
    call — the same argument `bta.tools.clients.HttpTransport` makes.
    """

    def write(self, bucket: str, name: str, body: str) -> None: ...


class StorageObjectWriter:
    """The real writer. Imported inside the call so the offline suite — which
    never uses this class — does not need the client library present."""

    def __init__(self, project_id: str) -> None:
        self._project_id = project_id

    def write(self, bucket: str, name: str, body: str) -> None:
        from google.cloud import storage

        client = storage.Client(project=self._project_id)
        client.bucket(bucket).blob(name).upload_from_string(body, content_type="application/json")


def object_name(run_id: str, session_id: str, sequence: int) -> str:
    """`raw/<run_id>/<session_id>/t<NNN>.json`.

    Keyed on the session rather than on `(case, level, model, repeat)` because
    the site does not know a case exists — the runner opens exactly one session
    per execution, so the session id identifies the execution, and the run
    result carries the ref that ties the two together.

    **A new object per turn, never an overwrite.** The bucket grants the runtime
    `roles/storage.objectCreator` and nothing more, and in GCS overwriting an
    object is a create *plus* a delete — so rewriting one object per turn 403s
    on the second turn. That is not a permission to widen: a runtime that could
    overwrite could also read, and reading is how one persona's transcript
    reaches another. Observed 2026-08-29 as seven failed executions, every one
    of them a multi-turn case, every single-turn case passing.

    Each object holds the whole transcript so far, so the highest sequence is
    the complete one and that is what a result's `transcript_ref` names. The
    earlier objects are partial snapshots — redundant, bounded by turn count,
    and removed by the bucket's retention rule with everything else.
    """
    return f"raw/{run_id}/{session_id}/t{sequence:03d}.json"


def persist(
    document: Any,
    values: dict[str, str],
    *,
    run_id: str | None,
    session_id: str,
    sequence: int,
    bucket: str | None,
    writer: ObjectWriter | None = None,
) -> Evidence:
    """Redact always; write the raw copy only for a run.

    **Redaction is not conditional on the bucket.** VER-6 makes it a stage of
    every run rather than a step before publication, and a deployment with no
    evidence bucket configured must still not hand an unredacted document to a
    caller. `redact` fails closed — it re-scans its own output and raises
    `RedactionIncomplete` rather than returning a copy that looks redacted and
    is not — so a configuration mistake here cannot become a disclosure.
    """
    redacted = redact(document, values)

    if run_id is None or bucket is None:
        # A human at a keyboard (FR-7), or a deployment with no evidence store.
        # Either way there is no run for this transcript to be evidence of, and
        # `transcript_ref` stays null rather than naming an object nobody wrote.
        return Evidence(raw_ref=None, redacted=redacted)

    if writer is None:
        raise EvidenceNotConfigured(
            "an evidence bucket is set but no writer was supplied. The writer is "
            "built once at startup from the deployment's project; constructing "
            "one here would hide a missing configuration behind a lazy default."
        )

    import json

    name = object_name(run_id, session_id, sequence)
    writer.write(bucket, name, json.dumps(document, indent=2, sort_keys=True, default=str))
    return Evidence(raw_ref=f"gs://{bucket}/{name}", redacted=redacted)


def audit_object_name(run_id: str, session_id: str, sequence: int, *, redacted: bool) -> str:
    """`audit/<run_id>/<session_id>/t<NNN>.json`, or `.redacted.json`.

    Per turn and never overwritten, for the reason `object_name` gives.

    Separate from the transcript objects so an analyst investigating a defeat
    can fetch one without the other, and so the IAM story stays one bucket
    rather than two.
    """
    suffix = ".redacted.json" if redacted else ".json"
    return f"audit/{run_id}/{session_id}/t{sequence:03d}{suffix}"


def persist_audit(
    records: list[Any],
    values: dict[str, str],
    *,
    run_id: str | None,
    session_id: str,
    sequence: int,
    bucket: str | None,
    writer: ObjectWriter | None = None,
) -> str | None:
    """Write this session's audit records, raw and redacted. TD-25, TD-31.

    **Both copies go to the bucket and neither is returned inline.** That is the
    difference from a transcript, and it is deliberate. The suite needs the
    *ids* — `schemas.md` §5's `tool_call_ids` is what lets SC-4 look up the
    records for a defeating case — and it needs nothing else. §6's `parameters`
    carry attacker-supplied injections, and while a repository full of attack
    cases is hardly protecting them, there is no requirement that puts them in
    git and so nothing here does.

    The redacted copy exists because ADR 0008 brought audit records under VER-6:
    a record's `parameters` can carry a flag value an attacker echoed back, so
    "redaction runs on every run" has to mean these too.

    Returns the raw object's ref, or `None` when there is no run to be evidence
    of — the same rule the transcript follows.
    """
    if not records:
        return None

    documents = [record.to_dict() for record in records]
    redacted = redact(documents, values)

    if run_id is None or bucket is None:
        return None
    if writer is None:
        raise EvidenceNotConfigured("an evidence bucket is set but no writer was supplied")

    import json

    raw_name = audit_object_name(run_id, session_id, sequence, redacted=False)
    writer.write(bucket, raw_name, json.dumps(documents, indent=2, sort_keys=True, default=str))
    writer.write(
        bucket,
        audit_object_name(run_id, session_id, sequence, redacted=True),
        json.dumps(redacted, indent=2, sort_keys=True, default=str),
    )
    return f"gs://{bucket}/{raw_name}"


__all__ = [
    "Evidence",
    "EvidenceNotConfigured",
    "ObjectWriter",
    "StorageObjectWriter",
    "audit_object_name",
    "object_name",
    "persist",
    "persist_audit",
]
