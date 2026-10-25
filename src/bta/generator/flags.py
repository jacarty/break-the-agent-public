"""Reading the flag definitions.

`flags/*.json` is the pre-registered description of what is being tested
(`schemas.md` §2). It is the generator's input, not something the generator
restates: a second list of flag ids in Python would drift from it, and the one
that drifted would be the one deciding where values get planted.

These files contain no values and never will. Values are minted in `values.py`
and only ever reach the fixture manifest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

# schemas.md §2. Closed sets, restated here only to reject a definition that
# has wandered outside them — a typo in `service` would otherwise plant a flag
# nowhere and produce a manifest that looks complete.
AGENTS: Final[frozenset[str]] = frozenset({"deploy_bot", "onboarding_assistant", "both"})
SERVICES: Final[frozenset[str]] = frozenset(
    {"bigquery", "cloud_storage", "firestore", "secret_manager", "write_path", "tool_schema"}
)
VERDICTS: Final[frozenset[str]] = frozenset({"defeated", "held", "insufficient"})
LEVELS: Final[tuple[str, ...]] = ("L1", "L2", "L3")


@dataclass(frozen=True, slots=True)
class FlagDefinition:
    id: str
    agent: str
    title: str
    service: str
    owner_team: str | None
    scored_cells: tuple[str, ...]
    schema_version: int

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source: Path) -> FlagDefinition:
        def require(key: str) -> Any:
            if key not in raw:
                raise ValueError(f"{source.name}: missing required field {key!r}")
            return raw[key]

        flag_id = require("id")
        agent = require("agent")
        service = require("service")
        cells = tuple(require("scored_cells"))

        if agent not in AGENTS:
            raise ValueError(f"{source.name}: agent {agent!r} is outside schemas.md §2")
        if service not in SERVICES:
            raise ValueError(f"{source.name}: service {service!r} is outside schemas.md §2")
        if not cells:
            raise ValueError(f"{source.name}: scored_cells must not be empty")
        if source.stem != flag_id:
            raise ValueError(f"{source.name}: filename does not match id {flag_id!r}")

        return cls(
            id=flag_id,
            agent=agent,
            title=require("title"),
            service=service,
            owner_team=raw.get("owner_team"),
            scored_cells=cells,
            schema_version=require("schema_version"),
        )


def find_flags_dir(start: Path | None = None) -> Path:
    """Locate `flags/` by walking up from this file.

    The generator is an operator tool run from the repository, so a walk is
    honest about where the definitions live. `--flags-dir` overrides it for
    anyone running from elsewhere.
    """
    here = (start or Path(__file__).resolve()).parent
    for candidate in [here, *here.parents]:
        flags_dir = candidate / "flags"
        if (flags_dir / "C1.json").is_file():
            return flags_dir
    raise FileNotFoundError("could not locate the flags/ directory; pass --flags-dir")


def load(flags_dir: Path | None = None) -> tuple[FlagDefinition, ...]:
    """Every definition, ordered by id.

    Ordering is by id rather than by directory listing because directory order
    is filesystem-dependent, and every downstream artefact is supposed to be
    byte-identical between two machines.
    """
    directory = flags_dir or find_flags_dir()
    definitions = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        definitions.append(FlagDefinition.from_dict(raw, path))
    if not definitions:
        raise FileNotFoundError(f"no flag definitions found in {directory}")
    return tuple(definitions)
