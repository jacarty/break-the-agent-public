"""The Cloud Run app — FR-1 through FR-8.

Server-rendered HTML with SSE for streaming, per
`docs/decisions/0007-site-is-server-rendered-with-sse.md`. No client build step,
no bundler, no framework beyond an ASGI server and a router: the repository is
meant to be read (DIS-1), and a deployer auditing what this does to their
project should not have to read a bundle first.

---

## The one thing to understand before reading further

**This process contains the verifier, and the verifier holds live flag values.**

FR-6 requires captures to happen automatically, in the session, as the flag
appears. That is only possible if something in the request path can recognise a
flag value, and `schemas.md` §3 permits exactly one component to hold them. So
the site imports `verifier` and calls it; it never reads the fixture manifest
itself and never sees a value.

Two consequences follow and neither is hypothetical:

- **Nothing rendered to the attacker names a value.** The captured-flag panel
  shows flag ids and channels (FR-6, VER-1a) and nothing else. A panel that
  echoed the value back would disclose C1 to every session that loaded the
  page, whether the agent leaked it or not.
- **A code-execution path in this process reaches the values**, exactly as it
  reaches the shared estate credential (`infra/estate/identity.tf`). ASI05 is
  out of scope (PRD §11) for the reason given there, and this is the same
  residual on a second asset rather than a new one. It is named here rather
  than left for a reader to work out.

---

## What the level does and does not change here

Nothing in this package branches on level except the labelling FR-4 requires.
The level reaches `bta.agent.Session` and stops mattering to the site: the
credential is `bta.credentials.policy`'s decision, the mitigation stack is
`bta.levels.mitigations`', and the transcript shape is identical either way.
That is FR-13 holding at the outermost layer, and it is the reason the site can
be read without knowing which level is selected.
"""

from __future__ import annotations

__all__: list[str] = []
