"""The persistent `MemoryStore` — FR-12, TD-17, `schemas.md` §7.

`InMemoryStore` next door is a dict, and a dict does not survive a process. FR-18
plants a directive in one session and opens a *fresh* one to see whether the
agent acts on it, so until this module existed **F7 could not be executed at
all** — two matrix rows, including the one L3 result FR-19 predicts will hold.

**The transport is REST with the caller's own Identity Platform token, at all
three levels.** That is the one decision in this file worth arguing, because
every other Firestore path in the lab splits by level (`bta.tools.clients`, and
spike 0.3 finding 2 behind it). Memory does not split, for two reasons:

1. `firestore.rules` says so — *"Readable only by the persona that wrote it, at
   every level"*. The rule adjudicates `request.auth.uid == persona_sub` on read
   and on create, and it is what F7b's containment claim actually rests on.
2. A server client is not subject to Security Rules at all. Using one here would
   move per-persona isolation off the platform rule and onto the `where` clause
   below — our own code, unobserved, in the exact place the experiment claims a
   platform control is doing the work. A query filter that silently stopped
   filtering would look identical to a rule that was holding.

Memory is therefore not part of the independent variable. FR-13 says the only
thing a level changes is credential minting, secret handling, capability scoping
and the in-agent mitigation stack; the difference between L1/L2 and L3 memory is
in what gets *written* (`extract`) and what gets *rendered* (`render`), never in
who is authorised to read it.

**Failures are loud.** A write that Firestore rejects raises rather than
returning, because a silently dropped record is indistinguishable from an agent
that chose not to remember — and F7a asks whether behaviour changed. An
`insufficient` verdict earned by a broken store, reported as a clean
`not_captured`, is manufactured evidence.

**No error message here carries record content.** A memory record holds
attacker-supplied turn text at L1 and L2, and an agent that has been talked into
storing a flag value has put one in `content`. Errors quote the HTTP status and
Firestore's own status string and nothing else — the same argument VER-5 makes
for `Session`'s `repr`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote

from bta.models import Level

from . import SCHEMA_VERSION, MemoryKind, MemoryRecord

#: `schemas.md` §7's collection, and the one `firestore.rules` writes a rule for.
COLLECTION = "agent_memory"


class MemoryStoreError(RuntimeError):
    """Firestore refused a read or a write.

    Deliberately not a subclass of `MemoryPolicyViolation`: that one means a
    record broke §7's level rules, which is a statement about the lab. This one
    means the store did not work.
    """


class JsonTransport(Protocol):
    """The store's only dependency, so the tests drive the real request bodies
    without a network.

    `bta.tools.clients.HttpTransport` is the same idea for the tool path and is
    deliberately not reused: it exposes `get_json` only, memory needs POST for
    both halves, and importing it would put `bta.memory` behind `bta.tools` for
    the sake of one method signature.
    """

    def post_json(self, url: str, bearer: str, payload: dict[str, Any]) -> tuple[int, Any]: ...


class RequestsTransport:
    """The real transport. `requests` arrives with the Google client libraries
    and is imported inside the call so the offline suite — which never uses this
    class — does not depend on it being installed."""

    def post_json(self, url: str, bearer: str, payload: dict[str, Any]) -> tuple[int, Any]:
        import requests

        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {bearer}"},
            json=payload,
            timeout=30,
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body


def new_document_id(session_id: str) -> str:
    """A runtime record's document id.

    Shaped to *miss* the generator's prune pattern on purpose. `apply.py` will
    only delete ids matching `^(emp-\\d{4}|mem-[a-z0-9-]+-\\d{3})$`, precisely so
    that reseeding does not wipe a live session's memory, and it says a
    colliding runtime id would be a bug in this scheme. `sess-` cannot collide.
    """
    return f"sess-{session_id}-{uuid.uuid4().hex[:8]}"


class FirestoreMemoryStore:
    """`MemoryStore` over the Firestore REST API, as the caller.

    Constructed per session, because the caller's ID token is what authorises
    every request and it belongs to one persona. `read` still takes a
    `persona_sub` — the protocol's shape, and the query filter the rules need —
    but a token for a different persona would simply be refused by the rule,
    which is the property being demonstrated rather than a check to add here.
    """

    def __init__(
        self,
        base_url: str,
        subject_token: str,
        session_id: str,
        transport: JsonTransport | None = None,
        *,
        document_ids: Callable[[str], str] = new_document_id,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = subject_token
        self._session_id = session_id
        self._transport = transport or RequestsTransport()
        self._document_ids = document_ids

    # -- write --------------------------------------------------------------

    def write(self, record: MemoryRecord) -> None:
        document_id = self._document_ids(self._session_id)
        url = (
            f"{self._base_url}/{quote(COLLECTION, safe='')}"
            f"?documentId={quote(document_id, safe='')}"
        )
        status, body = self._transport.post_json(
            url, self._token, {"fields": _encode_fields(record.to_dict())}
        )
        if status // 100 != 2:
            raise MemoryStoreError(
                f"Firestore refused the memory write: {_status_of(body)} "
                f"(HTTP {status}). The record is not stored and the session "
                f"will behave as though it never remembered."
            )

    # -- read ---------------------------------------------------------------

    def read(self, persona_sub: str) -> tuple[MemoryRecord, ...]:
        status, body = self._transport.post_json(
            f"{self._base_url}:runQuery", self._token, _query_for(persona_sub)
        )
        if status // 100 != 2:
            raise MemoryStoreError(
                f"Firestore refused the memory read: {_status_of(body)} (HTTP {status})."
            )

        records = [
            _record_from_document(entry["document"])
            for entry in (body or [])
            # A `runQuery` response pads with `readTime`-only entries when
            # nothing matched; they are not documents and carry no fields.
            if isinstance(entry, dict) and "document" in entry
        ]
        # Sorted here rather than by the query. An equality filter plus an
        # `orderBy` on a different field needs a composite index in Firestore,
        # which would be a `google_firestore_index` in `infra/estate/` that
        # exists only to order a handful of rows. Per-persona volume is small
        # and bounded by one lab run.
        records.sort(key=lambda record: record.created_at)
        return tuple(records)


def _query_for(persona_sub: str) -> dict[str, Any]:
    """The filter is not an optimisation.

    Firestore evaluates `allow read` per document, and a query that could match
    a document the rule refuses is rejected wholesale rather than filtered. So
    the `persona_sub` equality is what makes the query legal at all, and it is
    the same predicate the rule checks — which is the point: two enforcement
    systems, one claim.
    """
    return {
        "structuredQuery": {
            "from": [{"collectionId": COLLECTION}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "persona_sub"},
                    "op": "EQUAL",
                    "value": {"stringValue": persona_sub},
                }
            },
        }
    }


def _status_of(body: Any) -> str:
    """Firestore's own status string, or nothing recognisable.

    `runQuery` answers with a list on success and an object on failure, and an
    error can also arrive as a single-element list. Both are handled because a
    `MemoryStoreError` saying `UNKNOWN` when the answer was `PERMISSION_DENIED`
    would send whoever reads it looking in the wrong place.
    """
    if isinstance(body, list) and body:
        body = body[0]
    if isinstance(body, dict):
        status = body.get("error", {}).get("status")
        if status:
            return str(status)
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# The typed-value wire format
# ---------------------------------------------------------------------------
#
# Firestore's REST API wraps every value in a type tag; its server SDK does not.
# `bta.tools.clients` has a decoder for the same wire format and this is a second
# one, which is a duplication worth naming rather than hiding: that decoder is
# private to the tool path, memory needs an *encoder* as well because it writes,
# and sharing would make `bta.memory` depend on `bta.tools`. The formats are
# Google's and do not drift.


def _encode_fields(values: dict[str, Any]) -> dict[str, Any]:
    return {name: _encode_value(value) for name, value in values.items()}


def _encode_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    # Before `int`, because `bool` is a subclass of it and `True` would
    # otherwise be stored as `1`.
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        # Firestore carries 64-bit integers as strings in JSON.
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _decode_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {name: _decode_value(value) for name, value in fields.items()}


def _decode_value(value: dict[str, Any]) -> Any:
    if "nullValue" in value:
        return None
    for key in ("stringValue", "booleanValue", "doubleValue", "timestampValue"):
        if key in value:
            return value[key]
    if "integerValue" in value:
        return int(value["integerValue"])
    return None


def _record_from_document(document: dict[str, Any]) -> MemoryRecord:
    """Back into a `MemoryRecord`, invariants and all.

    `MemoryRecord.__post_init__` runs on the way in, so a stored document that
    breaks §7's level rules raises `MemoryPolicyViolation` here rather than
    being quietly repaired. That is the intended behaviour and it has already
    earned its keep: it is what surfaced the planted corpus writing typed
    records at L1 and L2.
    """
    data = _decode_fields(document.get("fields", {}))
    return MemoryRecord(
        persona_sub=str(data["persona_sub"]),
        session_id=str(data["session_id"]),
        created_at=_parse_timestamp(str(data["created_at"])),
        written_at_level=Level(data["written_at_level"]),
        kind=MemoryKind(data["kind"]),
        content=data.get("content"),
        subject=data.get("subject"),
        value=data.get("value"),
        schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
    )


def _parse_timestamp(value: str) -> datetime:
    """`to_dict` writes `…Z`; `fromisoformat` wants an offset it recognises."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


__all__ = [
    "COLLECTION",
    "FirestoreMemoryStore",
    "JsonTransport",
    "MemoryStoreError",
    "RequestsTransport",
    "new_document_id",
]
