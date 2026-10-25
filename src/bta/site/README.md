# bta.site — the Cloud Run app

FR-1 through FR-8. Server-rendered HTML, SSE for streaming, no client build
step — `docs/decisions/0007-site-is-server-rendered-with-sse.md`.

Read `src/bta/tools/README.md` and `src/bta/agent/README.md` first. This
package sits on top of both and changes neither: the level reaches
`bta.agent.Session` and stops mattering here.

---

## The one thing to understand first

**This process contains the verifier, and the verifier holds live flag values.**

FR-6 requires captures to happen automatically, as the flag appears. That needs
something in the request path able to recognise a flag value, and `schemas.md`
§3 permits exactly one component to hold them. So the site calls the verifier
and never reads the fixture manifest itself.

An import contract in `pyproject.toml` — *"The site reaches flag values only
through the verifier"* — forbids `bta.site → bta.generator` and
`bta.site → bta.screener`, and was run against a deliberately violating import
before it was believed. The screener half is `allow_indirect_imports`, because
the site reaches it legitimately through `bta.agent → bta.levels.mitigations`,
which is FR-15's stack doing its job.

The residual is named rather than left to be worked out: a code-execution path
in this process reaches the flag values, exactly as it reaches the shared
estate credential. ASI05 is out of scope (PRD §11) and this is the same
residual on a second asset.

---

## Where a request goes

```
GET  /            gate.py       FR-1 shared secret — not authentication (FR-2)
POST /gate                      constant-time compare, secure httponly cookie
GET  /arena       render.picker FR-3 persona · FR-4 agent and level, labelled
POST /session     personas.py   custom token → ID token → CallerIdentity
GET  /chat        render.chat   FR-5 transcript: parameters, row counts, payloads
POST /message     app.py        NFR-3 quota → Session.send → capture.scan
GET  /matrix                    FR-8, or an honest absence
GET  /healthz                   ungated — the startup probe has no cookie
```

## Four properties a test asserts

**Every route is gated except `/healthz`.** A matrix page reachable without the
code is a deployment detail handed to a scanner.

**A fixture owner cannot be started by posting the form.** The picker offers
only `alice`, but `personas.resolve` refuses `bob` and `svc-platform`
server-side — posting `persona=bob` is the first thing anyone tries, and
succeeding would produce a session whose every "capture" is the owner reading
their own data.

**Attacker text is escaped everywhere it renders.** Model output, tool
parameters and estate content are all attacker-influenced and all three appear
on the transcript page. An XSS here would be the lab exploiting its own
operator, which is worse than any flag.

**The bypass lifts rate limiting and never the token ceiling.** NFR-3a is
explicit, and the ceiling is what stands between a runaway suite and the SEC-3
budget cap.

## What refuses to start

`__main__.py` runs every refusable check before the socket is bound: the access
code (FR-1), C1's marker (TD-13, ADR 0003), and SEC-11a. A deliberately
vulnerable service that starts in a subtly wrong configuration is worse than
one that does not start, because the wrong configuration is invisible in a
passing run.

SEC-11a is the sharpest of the three. If the runtime identity can reach the
estate, every L3 verdict this deployment produces is unsound — and nothing else
in a passing run would reveal it.

---

## What the live run established

**The live path has run.** A deployed revision minted custom tokens, an
Identity Platform tenant accepted them, the marker secrets mounted, and the site
served both the phase 4 manual captures (F1 and F2 by hand at L1, 2026-09-15)
and the reference run. The offline suite still substitutes the session builder
at the `app.state` seam and scripts the agent, so it does not re-prove any of
that for a new deployment.

Two known gaps remain.

**Streaming is turn-level, not token-level.** `bta.providers` exposes
`complete`, not a streaming call, so the model's text arrives in one piece and
the SSE channel carries progress and result frames. NFR-1 asks for streaming to
*begin* within 5s at p95 and an acknowledgement frame is not what it means.
TD-23.

**Server-side state is bounded, and the gate is rate limited.** `Arena.gated`
and `Arena.sessions` are `bta.site.state.TTLMap`s — an age limit and a size
limit, built by `__post_init__` rather than passed in, so no caller can hand
the arena a plain dict. Gate tokens expire at the same age as the cookie that
carries them; browser sessions expire on idleness and are refreshed on access.
`POST /gate` — the one unauthenticated endpoint here — is limited per client
address and by a global hourly ceiling, in `bta.site.quota`'s own buckets so a
gate attempt never spends an interactive allowance. TD-27, ADR 0011.

What that does *not* fix is TD-24: state is still per instance, so a browser
session is only coherent if it lands on the same one, and NFR-3's counters
multiply by `max_instance_count`. ADR 0011 says why the shared store was not
built alongside TD-25; it still has not been.
