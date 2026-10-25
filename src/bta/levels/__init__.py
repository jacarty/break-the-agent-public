"""Levels as configuration over one agent — FR-13, FR-14, FR-15.

FR-14 makes the three levels **alternatives, not layers**: L3 does not include
L2's mitigations. That isolates the variable at the cost of realism, and the
documentation says so rather than implying L3 is a deployment recommendation.

What a level is allowed to change is closed:

| Changes with level | Where |
| -- | -- |
| credential minting | `bta.credentials.policy` — phase 2 |
| capability scoping and secret handling | `bta.tools.clients` — phase 2 |
| the in-agent mitigation stack (L2 only) | `mitigations.py` |
| the level fragment of the system prompt | `prompts/level.<level>.<agent>.md` |

Everything else is byte-identical across levels, which is FR-13's claim and is
checked by a test rather than trusted — an accidental wording drift between L1
and L3 confounds the only variable the lab varies.
"""

from __future__ import annotations

from .composition import (
    COMPOSED_DIR,
    PROMPTS_DIR,
    SEPARATOR,
    PromptError,
    base_text,
    compose,
    composed_path,
    level_text,
    render,
)
from .mitigations import Mitigations, mitigations_for

__all__ = [
    "COMPOSED_DIR",
    "PROMPTS_DIR",
    "SEPARATOR",
    "Mitigations",
    "PromptError",
    "base_text",
    "compose",
    "composed_path",
    "level_text",
    "mitigations_for",
    "render",
]
