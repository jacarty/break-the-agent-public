# 0001 — The tool context is passed positionally

**Date:** 2026-08-21
**Phase:** 2
**Status:** accepted
**Requirements:** POR-3, FR-11, FR-13
**Supersedes:** the open question in `build-plan.md` §Phase 2, "Discussion still open"

## Decision

Every tool takes the context as its first positional argument, named `ctx` and
annotated `ToolContext`. Every other parameter is keyword-only. Both rules are
enforced by the registry decorator at import time.

```python
def get_incident(ctx: ToolContext, *, incident_id: str) -> ToolResult:
    rows = ctx.client(Surface.BIGQUERY).rows(...)
```

The alternative was a factory binding the context into each tool at
construction, producing callables the loop invokes with arguments only.

## Why

The build plan called this cosmetic and said it is the seam readers look at
first. Both are true, and the second decides it.

**The credential seam stays at the call site.** A factory turns it into a
closure captured somewhere else, so a reader tracing where authority is bound
has to find the binding site before they can answer the question the lab exists
to answer. With the context in the signature, the first line of every tool
names the surface and the level does the rest.

**A tool names a surface and never a mode.** `ctx.client(Surface.BIGQUERY)`
resolves through `bta.credentials.policy` using the level on the context. There
is no argument that selects a credential mode, so there is no argument a model
can influence that selects one. That property is easier to state, and much
easier to test, when the resolution happens through an object the tool was
handed rather than through one it was built with.

**The exit criterion becomes a parametrisation.** "Every tool under all three
credential modes" is one function object and three contexts. Under a factory it
is three constructions, and the thing being varied is the construction rather
than the level.

**Keyword-only parameters are a `schemas.md` §6 requirement in disguise.** An
audit record names its parameters. A positional argument has no name to record.

## Costs

The context appears in every signature and in every call site, including the
tools that never use it — `check_policy` takes a `ctx` it deletes on the first
line. That is visible noise in exchange for a uniform boundary, and the
uniformity is what lets `invoke` be the only path that can call a tool.

## Consequences

`ToolContext` carries no audit sink, deliberately (VER-4): a tool cannot emit
its own record, or forget to, because nothing on the object leads to one. The
same reasoning applies to anything later proposed for the context — if a tool
should not be able to do it, it does not go on the context.
