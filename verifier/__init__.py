"""Capture detection — VER-1, VER-1a, VER-2, VER-6.

A separate top-level tree rather than a package under `src/bta/`, because
VER-3 says the verifier and the L2 screener are the same mechanism pointed in
opposite directions and must not share code or configuration. Sibling packages
under one namespace share an `__init__`, share utilities by gravity, and
eventually share a regex module because it was convenient. The import contract
in `pyproject.toml` names `verifier` as a root package so that both directions
of that rule are expressible; before this tree existed only one of them was.

**This is the only component permitted to hold live flag values**
(`schemas.md` §3). The fixture manifest is read here and nowhere else, which
is also why the redactor lives here (VER-6) rather than beside the thing it
redacts: substituting a flag value requires knowing it.

Three rules travel with everything in this tree:

- No flag value is ever logged, raised in an exception message, or rendered
  into a test's failure output. `pytest` prints both operands of a failed
  assertion and a CI log is a transcript.
- Nothing here imports `bta.screener`, and nothing there imports this. FR-16
  depends on the screener never learning what a flag looks like.
- Redaction substitutes rather than deletes. A transcript with the flag cut
  out no longer shows that the flag was disclosed, and the disclosure is the
  evidence.
"""

from __future__ import annotations

from verifier.manifest import FixtureValues
from verifier.match import Capture, find_captures
from verifier.normalise import (
    TRANSFORMS,
    NormalisedForm,
    normalisation_names,
    normalised_forms,
)
from verifier.redaction import RedactionIncomplete, redact, redact_text

SCHEMA_VERSION = 1

__all__ = [
    "SCHEMA_VERSION",
    "TRANSFORMS",
    "Capture",
    "FixtureValues",
    "RedactionIncomplete",
    "NormalisedForm",
    "find_captures",
    "normalisation_names",
    "normalised_forms",
    "redact",
    "redact_text",
]
