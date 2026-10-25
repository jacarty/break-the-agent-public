"""FR-6 — automatic capture, on both channels.

The seam between a live session and the verifier. It exists so that exactly one
place in the site knows how to turn a transcript into captures, and so that the
site's own code never touches a flag value.

**Both channels, scored apart** (VER-1a, `schemas.md` §1.3):

- `model_output` — `Transcript.assistant_text()`. Verdict-bearing.
- `tool_result` — `Transcript.tool_result_text()`, which exists as of TD-19.
  Recorded and displayed, never folded into a verdict.

Before TD-19 the second had no carrier, so a `captures_tool_result` count could
only ever be zero — and a silent zero reads as "the tool never handed data
over" rather than "never measured". `schemas.md` §8 calls a non-zero value
there "the lab's central claim in one row": the model held the line and the
tool disclosed the data anyway. That row is why the site renders the channel
label rather than a bare "captured" badge.

**Nothing here returns a flag value.** `Capture` carries an id, a channel and
the normalisation that produced the match. The panel FR-7 renders is built from
those three fields.
"""

from __future__ import annotations

from dataclasses import dataclass

from bta.agent.transcript import Transcript
from verifier.manifest import FixtureValues
from verifier.match import Capture, find_captures


@dataclass(frozen=True, slots=True)
class SessionCaptures:
    """What has been captured in this browser session, at this agent and level.

    FR-7: no scoring, no ranking, and no persistence between browser sessions
    beyond agent memory (FR-12). This lives in the session and dies with it.
    """

    entries: tuple[Capture, ...] = ()

    def merged(self, new: list[Capture]) -> SessionCaptures:
        """Add captures not already recorded on the same flag *and* channel.

        Keyed on the pair rather than on the flag, because `schemas.md` §1.3 is
        explicit that a single run may produce both channels for one flag and
        that neither suppresses the other. Collapsing them here would hide
        precisely the case the lab exists to show.
        """
        seen = {(c.flag_id, c.channel) for c in self.entries}
        added = [c for c in new if (c.flag_id, c.channel) not in seen]
        if not added:
            return self
        return SessionCaptures(entries=self.entries + tuple(added))

    def for_display(self) -> list[dict[str, str | None]]:
        """FR-6's labelling. Ids and channels only — never a value."""
        return [c.to_dict() for c in sorted(self.entries, key=lambda c: (c.flag_id, c.channel))]


def scan(transcript: Transcript, fixtures: FixtureValues) -> list[Capture]:
    """Scan both channels of a transcript.

    Called after every turn rather than at session end, because FR-6 says a
    flag is captured and shown *as it appears* — an attacker who sees the
    disclosure but no capture until they close the tab has been told the lab is
    broken.
    """
    values = dict(fixtures.values)
    return [
        *find_captures(transcript.assistant_text(), values, channel="model_output"),
        *find_captures(transcript.tool_result_text(), values, channel="tool_result"),
    ]


__all__ = ["SessionCaptures", "scan"]
