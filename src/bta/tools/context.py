"""What a tool is handed, and the one thing it cannot be handed.

`build-plan.md` left the shape of this open: context passed positionally to
every tool, or bound into each tool by a factory. Positional, and the reason is
legibility rather than taste. A factory turns the credential seam into a
closure captured at construction time, and a reader tracing where authority is
bound has to go and find the binding site. With the context in the signature,
the seam is the first line of every tool:

    def get_incident(ctx: ToolContext, *, incident_id: str) -> ToolResult:
        rows = ctx.client(Surface.BIGQUERY).query(...)

It also makes the phase's exit criterion a parametrised test rather than three
constructions: one function object, three contexts, three credential modes.

**A tool names a surface. It never names a credential mode.** There is no
argument to `client()` that selects one, so there is no argument a model could
influence that selects one either. The level on the context decides, through
`bta.credentials.policy`.

**The context carries no audit sink.** VER-4 says auditing is applied by the
wrapper, and a tool that emitted its own record — or forgot to — would be a
hole in exactly the mechanism that is supposed to make forgetting impossible.
A tool cannot reach the sink because nothing on this object leads to it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from bta.credentials import UNCREDENTIALED, CredentialPolicyViolation, mode_for
from bta.identity import CallerIdentity
from bta.models import Agent, CredentialMode, Level, Surface, require_level


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ClientProvider(Protocol):
    """Builds a client for a surface, already bound to the right credential.

    The tool receives a client it could not have constructed for anybody else
    — spike 0.1 finding 7. Whatever caching an implementation does, the
    credential is per caller and per request.
    """

    def for_surface(self, surface: Surface, caller: CallerIdentity, level: Level) -> Any: ...


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool returns.

    `rows_returned` and `bytes_returned` are the two size fields
    `schemas.md` §6 records. They are on the result rather than computed by the
    wrapper because only the tool knows what a row is on its surface — an
    object body has none, a Firestore document is one.
    """

    payload: Any
    rows_returned: int | None = None
    bytes_returned: int | None = None


@dataclass(frozen=True, slots=True)
class ToolContext:
    """One per tool call, though the boundary does not assume it — see
    `credential_mode_used`."""

    caller: CallerIdentity
    level: Level
    agent: Agent
    session_id: str
    clients: ClientProvider
    run_id: str | None = None
    now: Callable[[], datetime] = _utcnow

    # Which surfaces this call actually reached. Written by `client()` and read
    # by the boundary, because `credential_mode` on an audit record has to be
    # what was used and not what the tool declared it might use. A tool that
    # returned early without touching a surface records `null`, which is a true
    # statement about a call that authenticated to nothing.
    _surfaces_used: list[Surface] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        require_level(self.level)

    def client(self, surface: Surface) -> Any:
        self._surfaces_used.append(surface)
        return self.clients.for_surface(surface, self.caller, self.level)

    @property
    def surfaces_used(self) -> tuple[Surface, ...]:
        return tuple(self._surfaces_used)

    def credential_mode_used(self, since: int = 0) -> CredentialMode | None:
        """The mode to record. `None` when the call reached no surface.

        `since` bounds the answer to one invocation. A context is meant to be
        built per tool call, but nothing can enforce that from here, and the
        agent loop in phase 3 is exactly the kind of caller that would build
        one per session without noticing: the second call would then be
        described by the first call's credential, or would fail this method
        outright once two surfaces disagreed. The boundary passes the mark it
        took before calling the tool, so the record describes the call rather
        than the context's history.

        Surfaces that carry no credential at any level — the write path, a
        tool description — are skipped rather than refused: reaching one is a
        real thing a tool does, and it authenticates to nothing.

        Refuses to guess when a call spanned two modes. No tool in the lab does
        — `search_deploys` reads BigQuery and Cloud Storage, and both resolve
        the same way at every level — and `schemas.md` §6 has one
        `credential_mode` field, so a tool that did span two could not be
        described by one record. Better a loud failure at the boundary than an
        audit trail that quietly names one of the two.
        """
        modes = {
            mode_for(self.level, surface)
            for surface in self._surfaces_used[since:]
            if surface not in UNCREDENTIALED
        }
        if not modes:
            return None
        if len(modes) > 1:
            raise CredentialPolicyViolation(
                f"a single call reached surfaces with different credential modes "
                f"({sorted(mode.value for mode in modes)}); one audit record cannot "
                f"describe it (schemas.md §6)"
            )
        return modes.pop()


__all__ = ["ClientProvider", "ToolContext", "ToolResult"]
