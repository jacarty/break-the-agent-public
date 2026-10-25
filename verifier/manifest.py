"""Reading the fixture manifest — `schemas.md` §3.

**This module is the reason the verifier tree exists as a separate root
package.** The manifest holds live flag values, and §3 says exactly two
components read it: the generator that writes it, and the verifier. Everything
else — the site, the tools, the agent, the screener, the suite runner — gets
flag ids and never values.

The manifest is never committed and never served. It arrives from Secret
Manager or the state bucket (POR-6a); locally it arrives as a path in
`BTA_FIXTURE_MANIFEST`, read once at the start of a scoring run and held in
memory for the run's duration.

**Nothing here renders a value.** `FixtureValues.__repr__` is overridden
because a dataclass repr would put every flag value into any traceback that
touched one, and a traceback reaches CI logs. The same reasoning governs
`tests/`: no assertion in this repository may print a flag value, so the tests
for this module build their own synthetic values rather than reading a real
manifest.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bta.config import MissingConfiguration

MANIFEST_ENV = "BTA_FIXTURE_MANIFEST"
SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class FixtureValues:
    """Flag id to live value, plus the seed that produced them.

    `values` is the scoring input. `seed` and `generated_at` travel with it so
    a run result can record which seeding it was scored against — a matrix
    scored against a manifest the estate no longer serves is not a finding, it
    is an artefact of a reseed.
    """

    seed: str
    generated_at: str
    project_id: str
    values: Mapping[str, str]

    def __repr__(self) -> str:
        """Ids and a count. Never the values — see the module docstring."""
        return (
            f"FixtureValues(seed={self.seed!r}, "
            f"flags={sorted(self.values)!r}, "
            f"values=<{len(self.values)} redacted>)"
        )

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.values))


def load(path: str | os.PathLike[str] | None = None) -> FixtureValues:
    """Read the manifest from `path`, or from `BTA_FIXTURE_MANIFEST`.

    Raises rather than returning an empty set when the manifest is missing. A
    verifier scoring against zero flag values finds zero captures and reports
    `held` for every cell in the matrix — the single most damaging silent
    failure available to this project, and indistinguishable from a clean run.
    """
    source = Path(path) if path is not None else None
    if source is None:
        configured = os.environ.get(MANIFEST_ENV)
        if not configured:
            raise MissingConfiguration(
                f"{MANIFEST_ENV} is not set. The verifier cannot score a run "
                f"without the fixture manifest: with no values it would find "
                f"no captures and report `held` for every cell."
            )
        source = Path(configured)

    if not source.is_file():
        raise MissingConfiguration(f"fixture manifest not found at {source}")

    document = json.loads(source.read_text(encoding="utf-8"))

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"fixture manifest schema_version is {version!r}, expected "
            f"{SCHEMA_VERSION}. schemas.md §3 increments this on a breaking "
            f"change, so scoring against it would compare unlike records."
        )

    values: dict[str, str] = {}
    for entry in document.get("flags", []):
        flag_id = entry.get("id")
        value = entry.get("value")
        if not flag_id or not value:
            raise ValueError(
                f"fixture manifest entry {flag_id!r} carries no value. Every "
                f"flag in the manifest is scorable by definition; an entry "
                f"without one would silently drop a row from the matrix."
            )
        values[flag_id] = value

    if not values:
        raise ValueError("fixture manifest contains no flags")

    return FixtureValues(
        seed=document.get("seed", ""),
        generated_at=document.get("generated_at", ""),
        project_id=document.get("project_id", ""),
        values=values,
    )


__all__ = ["MANIFEST_ENV", "SCHEMA_VERSION", "FixtureValues", "load"]
