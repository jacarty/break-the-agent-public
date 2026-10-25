"""The thin loop (OD-1). See `loop.py`.

`bta.tools` imports nothing from this package — POR-3, FR-11, enforced by an
import contract in `pyproject.toml` rather than by remembering.
"""

from __future__ import annotations

from .loop import (
    CONFIRMATION_REQUIRED,
    MAX_TOOL_ITERATIONS,
    REFUSAL,
    WITHHELD_TOOL_RESULT,
    Session,
    SessionError,
)
from .transcript import ToolCallEvent, Transcript, TurnEvent

__all__ = [
    "CONFIRMATION_REQUIRED",
    "MAX_TOOL_ITERATIONS",
    "REFUSAL",
    "WITHHELD_TOOL_RESULT",
    "Session",
    "SessionError",
    "ToolCallEvent",
    "Transcript",
    "TurnEvent",
]
