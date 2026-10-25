"""The estate as data, before anything is written to a cloud.

The generator is split in two on purpose. This half is pure: seed and config in,
a complete description of every row, object, document and secret payload out,
with no client library anywhere near it. The half that pushes a plan to GCP is
separate and does no deciding.

Two things fall out of that split. The determinism criterion — two applies from
one seed produce byte-identical manifests — becomes testable on a laptop with
no project, no credentials and no network, which is the difference between a
test that runs on every commit and one that runs when somebody remembers. And a
plan can be diffed before it is applied, which is how you find out that a
corpus change moved F1 to a different row.

**A plan contains live flag values.** It is exactly as sensitive as the fixture
manifest: never committed, never logged, written only under `.fixtures/`
(gitignored, and denied to Claude Code in `.claude/settings.json`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import blake2b
from typing import Any


@dataclass(frozen=True, slots=True)
class StorageObject:
    """One Cloud Storage object. `name` carries the `<team>/` prefix that the
    conditional IAM binding in `infra/estate/storage.tf` matches on."""

    name: str
    content: str
    content_type: str = "text/plain; charset=utf-8"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "content_type": self.content_type, "content": self.content}


@dataclass(frozen=True, slots=True)
class FirestoreDocument:
    collection: str
    document_id: str
    fields: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "document_id": self.document_id,
            "fields": self.fields,
        }


@dataclass(frozen=True, slots=True)
class SecretVersion:
    """A Secret Manager payload.

    Terraform creates the secret and never the version, because a version is a
    value and a value in Terraform is a value in state, in a bucket, and in
    every printed plan. This is the other end of that arrangement.
    """

    secret_id: str
    payload: str

    def to_dict(self) -> dict[str, Any]:
        return {"secret_id": self.secret_id, "payload": self.payload}


@dataclass(frozen=True, slots=True)
class EstatePlan:
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    objects: tuple[StorageObject, ...] = ()
    documents: tuple[FirestoreDocument, ...] = ()
    secrets: tuple[SecretVersion, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": {name: self.tables[name] for name in sorted(self.tables)},
            "objects": [obj.to_dict() for obj in self.objects],
            "documents": [doc.to_dict() for doc in self.documents],
            "secrets": [secret.to_dict() for secret in self.secrets],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    def fingerprint(self) -> str:
        """A hash over the whole estate, safe to print and to commit.

        The manifest fingerprint proves the *flags* landed identically. This
        proves the estate around them did too — a corpus change that moved
        every incident id but happened to leave the flag rows alone would pass
        the first check and fail this one, and it is a real change to what the
        agents read.
        """
        return blake2b(self.to_json().encode("utf-8"), digest_size=16).hexdigest()
