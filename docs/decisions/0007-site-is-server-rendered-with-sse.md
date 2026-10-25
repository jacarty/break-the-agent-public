# 0007 — The site is server-rendered, with SSE for streaming

**Date:** 2026-08-25
**Phase:** 4
**Status:** accepted
**Requirements:** FR-5, FR-7, NFR-1, POR-5, DIS-1
**Related:** `build-plan.md` §3 "Phase 4", §7

## Decision

The site is server-rendered HTML with progressive enhancement. Streaming uses
server-sent events. There is no client build step, no bundler and no framework.

## Why this is a record rather than a choice

**It was already decided, in the wrong half of the document.**
`build-plan.md` §7's decisions table carries "Site rendering | Server-rendered,
SSE for streaming | The transcript is the only stateful surface and the
repository is meant to be read. A client build step buys little here." §3's
Phase 4 section simultaneously called it "Discussion still open… worth ten
minutes before it is made by accident".

Two sections of one document disagreeing is how a settled decision gets
re-litigated by whoever reads the other one first. This ADR records §7's
answer and the phase-4 branch deletes §3's paragraph. **Nothing was re-decided
here** — a decision reopened at the moment of implementation, with the
implementation's convenience in view, is exactly the drift the pre-registration
discipline exists to prevent, even though the rendering approach is not itself
a pre-registered artefact.

## The reasoning, restated so it survives without §7

**The transcript is the only stateful surface.** FR-5 requires tool calls with
parameters and row counts; FR-7 requires the captured-flag panel for the
current agent and level, with no persistence between browser sessions beyond
agent memory. That is one growing list and one small panel. A client
application's state management earns nothing against it.

**The repository is meant to be read** (DIS-1). A deployer auditing what the
lab does to their project should be able to follow the request path without
first understanding a bundler's output. This is the same argument OD-1 made
against an agent framework, applied to the front end.

**SSE rather than WebSockets.** NFR-1 requires streaming to begin within five
seconds at p95. The channel is one-directional — tokens out, form posts in —
and SSE is one-directional. Cloud Run supports it without session affinity,
which WebSockets would want.

## Costs

The captured-flag panel updates by SSE alongside the token stream rather than
by client-side state, so a capture and the text disclosing it arrive on one
channel and cannot render out of order. Without JavaScript the chat degrades to
a full-page POST per turn: usable, not streaming, and NFR-1 does not apply to a
client that opted out of the mechanism NFR-1 names.

## Revisit if

Never on ergonomic grounds. Only if a requirement arrives that a server-rendered
page genuinely cannot express — and PRD §11's scope gate applies to that
requirement first.
