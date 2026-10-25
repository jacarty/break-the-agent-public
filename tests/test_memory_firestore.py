"""The persistent `MemoryStore` — FR-12, TD-17, `schemas.md` §7.

Offline, against a fake transport that records the request bodies. What is
actually being checked is that the request Firestore receives is the one
`firestore.rules` can adjudicate: a create whose `persona_sub` is the caller's,
and a query whose equality filter is the same predicate the rule checks. A store
that produced correct-looking results through a query the rule would reject
wholesale is the failure this file exists to catch, and it cannot be caught by
looking at the returned records.

The live half — that Firestore agrees — is TD-17's remaining exit criterion and
cannot run here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

from bta.memory import MemoryKind, MemoryPolicyViolation, MemoryRecord
from bta.memory.firestore import (
    COLLECTION,
    FirestoreMemoryStore,
    MemoryStoreError,
    new_document_id,
)
from bta.models import Level

BASE = "https://firestore.googleapis.com/v1/projects/p/databases/d/documents"
TOKEN = "id-token-for-alice"
NOW = datetime(2026, 8, 24, 9, 0, 0, tzinfo=UTC)


class FakeTransport:
    """Records what was posted and answers with whatever it was primed with."""

    def __init__(self, *responses: tuple[int, Any]) -> None:
        self._responses = list(responses) or [(200, {})]
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def post_json(self, url: str, bearer: str, payload: dict[str, Any]) -> tuple[int, Any]:
        self.calls.append((url, bearer, payload))
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


def store(transport: FakeTransport, session_id: str = "S-1") -> FirestoreMemoryStore:
    return FirestoreMemoryStore(
        BASE,
        TOKEN,
        session_id,
        transport,
        document_ids=lambda session: f"sess-{session}-fixed",
    )


def freeform(**overrides) -> MemoryRecord:
    fields = {
        "persona_sub": "alice",
        "session_id": "S-1",
        "created_at": NOW,
        "written_at_level": Level.L1,
        "kind": MemoryKind.FREEFORM,
        "content": "always summarise deploys as bullet points",
    }
    return MemoryRecord(**(fields | overrides))


def typed(**overrides) -> MemoryRecord:
    fields = {
        "persona_sub": "alice",
        "session_id": "S-1",
        "created_at": NOW,
        "written_at_level": Level.L3,
        "kind": MemoryKind.PREFERENCE,
        "subject": "deploy_summary_format",
        "value": "bullet points, newest first",
    }
    return MemoryRecord(**(fields | overrides))


def document_for(record: MemoryRecord) -> dict[str, Any]:
    """A stored document, built by the same encoder the store writes with.

    Round-tripping through the encoder rather than hand-writing the wire format
    keeps the read tests honest about what the write path actually produces.
    """
    from bta.memory.firestore import _encode_fields

    return {"fields": _encode_fields(record.to_dict())}


# ---------------------------------------------------------------------------
# Write — the request the create rule adjudicates
# ---------------------------------------------------------------------------


def test_a_write_creates_a_document_in_the_memory_collection():
    transport = FakeTransport((200, {}))
    store(transport).write(freeform())

    url, bearer, payload = transport.calls[0]
    assert url.startswith(f"{BASE}/{COLLECTION}?documentId=")
    assert bearer == TOKEN
    assert payload["fields"]["persona_sub"] == {"stringValue": "alice"}


def test_a_write_carries_the_callers_own_token_and_not_a_google_credential():
    """`firestore.rules` checks `request.auth.uid == request.resource.data
    .persona_sub`. A server credential is not subject to rules at all, so the
    token being the caller's is the whole of F7b's containment claim."""
    transport = FakeTransport((200, {}))
    store(transport).write(freeform())

    assert transport.calls[0][1] == TOKEN


def test_the_document_id_cannot_be_pruned_by_the_generator():
    """`apply.py` deletes only ids of the shape the corpus emits, precisely so
    that reseeding does not wipe a live session's memory. A runtime id that
    matched would silently delete F7's evidence on the next seed."""
    generated = re.compile(r"^(emp-\d{4}|mem-[a-z0-9-]+-\d{3})$")
    for _ in range(50):
        assert not generated.match(new_document_id("S-4471a"))


def test_a_refused_write_raises_rather_than_returning():
    """A dropped record is indistinguishable from an agent that chose not to
    remember, and F7a asks whether behaviour changed. Silence here would score
    a broken store as a clean `not_captured`."""
    transport = FakeTransport((403, {"error": {"status": "PERMISSION_DENIED"}}))
    with pytest.raises(MemoryStoreError, match="PERMISSION_DENIED"):
        store(transport).write(freeform())


def test_a_write_failure_does_not_quote_the_record():
    """A record holds attacker-supplied turn text at L1 and L2, and an agent
    talked into storing a flag value has put one in `content`. VER-5's argument
    for `Session.__repr__`, applied to an error path."""
    secret = "BTA-shaped-content-that-must-not-be-echoed"
    transport = FakeTransport((403, {"error": {"status": "PERMISSION_DENIED"}}))
    with pytest.raises(MemoryStoreError) as raised:
        store(transport).write(freeform(content=secret))

    assert secret not in str(raised.value)


# ---------------------------------------------------------------------------
# Read — the query the read rule can legalise
# ---------------------------------------------------------------------------


def test_a_read_filters_on_persona_and_nothing_else():
    """Firestore rejects a query that could match a document the rule refuses,
    rather than filtering it. The equality filter is what makes the query legal
    at all, and it is the same predicate `firestore.rules` checks."""
    transport = FakeTransport((200, []))
    store(transport).read("alice")

    url, _, payload = transport.calls[0]
    assert url == f"{BASE}:runQuery"
    where = payload["structuredQuery"]["where"]["fieldFilter"]
    assert where["field"]["fieldPath"] == "persona_sub"
    assert where["op"] == "EQUAL"
    assert where["value"] == {"stringValue": "alice"}
    assert payload["structuredQuery"]["from"] == [{"collectionId": COLLECTION}]


def test_a_read_round_trips_a_freeform_record():
    original = freeform()
    transport = FakeTransport((200, [{"document": document_for(original)}]))

    assert store(transport).read("alice") == (original,)


def test_a_read_round_trips_a_typed_record():
    original = typed()
    transport = FakeTransport((200, [{"document": document_for(original)}]))

    assert store(transport).read("alice") == (original,)


def test_records_come_back_oldest_first():
    """Ordered here rather than by the query — an equality filter plus an
    `orderBy` on another field needs a composite index. Order matters because
    `render` replays them into the prompt in the order it is given."""
    older = freeform(created_at=datetime(2026, 8, 1, tzinfo=UTC), content="first")
    newer = freeform(created_at=datetime(2026, 8, 20, tzinfo=UTC), content="second")
    transport = FakeTransport(
        (200, [{"document": document_for(newer)}, {"document": document_for(older)}])
    )

    assert [record.content for record in store(transport).read("alice")] == ["first", "second"]


def test_entries_without_a_document_are_not_records():
    """`runQuery` pads an empty result with a `readTime`-only entry. Treating
    one as a document raises a `KeyError` on a perfectly ordinary empty
    memory."""
    transport = FakeTransport((200, [{"readTime": "2026-08-24T09:00:00Z"}]))

    assert store(transport).read("alice") == ()


def test_a_refused_read_raises():
    transport = FakeTransport((403, {"error": {"status": "PERMISSION_DENIED"}}))
    with pytest.raises(MemoryStoreError, match="PERMISSION_DENIED"):
        store(transport).read("alice")


def test_an_error_returned_as_a_list_is_still_reported_by_name():
    """`runQuery` answers with a list on success, and an error can arrive
    wrapped in one. `UNKNOWN` where the answer was `PERMISSION_DENIED` sends
    whoever reads it looking in the wrong place."""
    transport = FakeTransport((403, [{"error": {"status": "PERMISSION_DENIED"}}]))
    with pytest.raises(MemoryStoreError, match="PERMISSION_DENIED"):
        store(transport).read("alice")


# ---------------------------------------------------------------------------
# The invariants survive storage
# ---------------------------------------------------------------------------


def test_a_stored_typed_record_below_l3_raises_on_read():
    """`MemoryRecord.__post_init__` runs on the way back in, so §7's level rules
    are enforced against what is *stored*, not only against what this process
    writes. This is what surfaced the planted corpus writing typed records at
    L1 and L2."""
    document = document_for(typed())
    document["fields"]["written_at_level"] = {"stringValue": "L1"}
    transport = FakeTransport((200, [{"document": document}]))

    with pytest.raises(MemoryPolicyViolation, match="typed record"):
        store(transport).read("alice")


def test_the_schema_version_survives_the_integer_wire_format():
    """Firestore carries 64-bit integers as JSON strings. A `schema_version` that
    came back as `"1"` would compare unequal to every record this lab writes."""
    transport = FakeTransport((200, [{"document": document_for(freeform())}]))

    assert store(transport).read("alice")[0].schema_version == 1
