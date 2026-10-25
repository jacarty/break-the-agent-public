"""The fixture manifest (`schemas.md` §3).

The only channel between the generator and the verifier, and the only artefact
in the project that holds live flag values. Written to `.fixtures/`, uploaded to
Secret Manager or the state bucket, never committed, never served, never read
by a tool — and never read into a model context, which is why it is denied in
both `.claude/settings.json` and `.claudeignore`.

## On `generated_at` and byte-identity

Phase 1 exits on "two applies from the same seed produce byte-identical
manifests". `schemas.md` §3 mandates `generated_at`, which is wall-clock time
and therefore differs between those two applies. The two requirements collide
head-on and one of them has to give.

Dropping the timestamp would edit a frozen contract to make a test pass.
Freezing it to a constant would put a false statement in the artefact. So both
stand as written and the criterion is checked on `fingerprint()`, which hashes
the manifest with `generated_at` removed and nothing else. What that check
proves is precisely what the criterion is for: the seed determines the values
and where they were planted. When the manifest was written is metadata about
the run, not about the estate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import blake2b
from typing import Any, Final

SCHEMA_VERSION: Final = 1

# schemas.md §3. `resource` and `field` mean different things per value, so the
# set is closed and checked rather than trusted.
PLANTED_VIA: Final[frozenset[str]] = frozenset(
    {
        "row_field",
        "object_content",
        "document_field",
        "secret_version",
        "tool_description",
        "write_path_outcome",
    }
)

# The three that name a code surface rather than a cloud one, and so carry no
# `field`. C1 and F6 are planted by phase 3, not by this generator; they still
# get entries, because the manifest is the only channel the verifier reads.
_FIELDLESS: Final[frozenset[str]] = frozenset(
    {"object_content", "secret_version", "tool_description", "write_path_outcome"}
)


@dataclass(frozen=True, slots=True)
class FlagFixture:
    id: str
    value: str
    service: str
    resource: str
    owner_team: str | None
    planted_via: str
    field: str | None

    def __post_init__(self) -> None:
        if self.planted_via not in PLANTED_VIA:
            raise ValueError(f"{self.id}: planted_via {self.planted_via!r} is outside schemas §3")
        if self.planted_via in _FIELDLESS and self.field is not None:
            raise ValueError(f"{self.id}: planted_via {self.planted_via!r} takes no field")
        if self.planted_via not in _FIELDLESS and not self.field:
            raise ValueError(f"{self.id}: planted_via {self.planted_via!r} requires a field")
        if not self.value.startswith("BTA{") or not self.value.endswith("}"):
            raise ValueError(f"{self.id}: value is not in the FR-17 flag format")

    def to_dict(self) -> dict[str, Any]:
        # Key order is fixed to match the example in schemas.md §3. Byte
        # identity is a property of the serialisation as much as the content.
        return {
            "id": self.id,
            "value": self.value,
            "service": self.service,
            "resource": self.resource,
            "owner_team": self.owner_team,
            "planted_via": self.planted_via,
            "field": self.field,
        }


@dataclass(frozen=True, slots=True)
class FixtureManifest:
    seed: str
    project_id: str
    flags: tuple[FlagFixture, ...]
    generated_at: datetime
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "seed": self.seed,
            "generated_at": _iso_millis(self.generated_at),
            "project_id": self.project_id,
            "flags": [flag.to_dict() for flag in self.flags],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    def fingerprint(self) -> str:
        """Hash of the manifest with `generated_at` removed. See the module
        docstring — this, not the raw bytes, is what the exit criterion checks.

        Safe to print, log and commit: it is a hash of values, not the values.
        """
        payload = self.to_dict()
        del payload["generated_at"]
        canonical = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        return blake2b(canonical.encode("utf-8"), digest_size=16).hexdigest()


def _iso_millis(moment: datetime) -> str:
    """ISO 8601, UTC, millisecond precision (`schemas.md` §Conventions)."""
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
