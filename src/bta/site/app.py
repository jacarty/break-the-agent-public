"""Routes — FR-1 to FR-8.

Server-rendered, SSE for the streaming path, no client build step (ADR 0007).
Starlette is a router and an ASGI server here and nothing more: there is no
middleware stack, no dependency injection and no ORM, because a deployer
auditing this should be able to follow a request from the socket to the model
in one file.

**Session state lives in memory, keyed by a cookie, and is bounded.** FR-7 says
no persistence between browser sessions beyond agent memory (FR-12), so there
is nothing to store. Both maps are `bta.site.state.TTLMap`s with an age limit
and a size limit (TD-27, ADR 0011) — until then they were plain dicts that
nothing ever evicted, fed by the one unauthenticated endpoint on the service.

The consequence that bounding them does *not* remove, stated rather than
discovered: with more than one Cloud Run instance a browser session is only
coherent if it lands on the same instance, and NFR-3's counters are per
instance, so the effective limit is the configured one multiplied by
`max_instance_count`. `min_instance_count = 1` and low traffic make coherence
true in practice and not by construction — that half of TD-24 stays open.

**The startup check is not optional.** `startup_checks()` runs SEC-11a — there
is no `lifespan` handler here; `bta.site.__main__.main()` calls it explicitly,
before `uvicorn.run`, so the probe completes before the socket is bound rather
than racing the first request. A deployment that got the service account wrong
would otherwise serve an L3 column that is silently meaningless.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route

from bta.agent.loop import Session
from bta.models import Agent, Level
from bta.providers import classify_failure
from bta.site import capture, evidence, gate, personas, quota, render, sec11a
from bta.site.state import TTLMap

SESSION_COOKIE = "bta_session"

# TD-27. Defaults rather than deployment inputs: these are properties of one
# container's memory, not of a deployment (POR-6), and an operator tuning them
# would be tuning the wrong thing — the limits that protect the budget are the
# NFR-3 three, which are Terraform variables.
GATE_TOKEN_TTL_SECONDS = 86_400.0  # the cookie's `max_age`; see `submit_gate`
MAX_GATE_TOKENS = 512
SESSION_TTL_SECONDS = 3_600.0  # idle, refreshed on access
MAX_SESSIONS = 64  # each holds a full transcript in a 2 GiB container


@dataclass
class BrowserSession:
    """One tab. Dies with the process, as FR-7 intends."""

    persona: str
    agent: str
    level: str
    session: Session
    captures: capture.SessionCaptures = field(default_factory=capture.SessionCaptures)
    #: Set only when the suite opened this session. It is what separates a run
    #: from a person browsing, and `bta.site.evidence` writes nothing without
    #: one — FR-7 says a browser session leaves nothing behind.
    run_id: str | None = None


@dataclass
class Arena:
    """Everything the routes need, built once at startup.

    Assembled here rather than reached for through module globals so that a
    test can build one without a deployment — and so that the flag values the
    verifier holds have exactly one owner in the process.
    """

    config: Any
    markers: Any
    fixtures: Any
    sink: Any
    limits: quota.Limits = field(default_factory=quota.Limits)
    quota: quota.Quota = field(default_factory=quota.Quota)
    #: TD-34. `None` on a deployment with no evidence store, in which case
    #: transcripts are still redacted and simply not persisted.
    evidence_bucket: str | None = None
    evidence_writer: Any = None
    gate_code: str = ""
    gate_token_ttl_seconds: float = GATE_TOKEN_TTL_SECONDS
    max_gate_tokens: int = MAX_GATE_TOKENS
    session_ttl_seconds: float = SESSION_TTL_SECONDS
    max_sessions: int = MAX_SESSIONS
    # `init=False`, so `dataclasses.replace` would rebuild both and silently
    # drop every live session and gate token. Nothing calls it; this is here so
    # that stays a decision rather than a discovery.
    gated: TTLMap[bool] = field(init=False)
    sessions: TTLMap[BrowserSession] = field(init=False)

    def __post_init__(self) -> None:
        """Build the bounded maps, and hand `limits` to the counter that
        actually enforces them.

        The maps are built here rather than taken as constructor arguments so
        that there is no way to construct an `Arena` holding an unbounded one —
        the defect TD-27 recorded was a plain dict, and a `default_factory`
        would leave the plain dict a caller could still pass.

        `Quota` builds its own `Limits()` from the dataclass defaults, so
        without this the `limits` passed by `main()` — the ones NFR-3 says are
        set as Terraform variables and enforced server-side — were held on a
        field nothing ever read. An operator lowering
        `BTA_RATE_LIMIT_PER_MINUTE` to protect a small budget got the default
        20 regardless, and the three `BTA_*` quota variables were decorative.
        """
        self.quota.limits = self.limits
        self.gated = TTLMap(
            ttl_seconds=self.gate_token_ttl_seconds, max_entries=self.max_gate_tokens
        )
        self.sessions = TTLMap(
            ttl_seconds=self.session_ttl_seconds,
            max_entries=self.max_sessions,
            # Idle, not absolute: a person reading a long transcript between
            # turns must not lose the session underneath them.
            sliding=True,
        )


async def _form(request: Request) -> dict[str, str]:
    """Parse an `application/x-www-form-urlencoded` body.

    Starlette's `request.form()` pulls in `python-multipart`, and this site has
    no file upload and wants none: accepting multipart on a deliberately
    vulnerable, internet-reachable service would add a parser and an upload
    surface to serve a feature that does not exist. Anything that is not
    urlencoded is treated as an empty form rather than an error, so a probe
    posting nonsense gets the ordinary "no text" path.
    """
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/x-www-form-urlencoded"):
        return {}
    body = (await request.body()).decode("utf-8", errors="replace")
    return dict(parse_qsl(body, keep_blank_values=True))


def _gate_ok(request: Request) -> bool:
    arena: Arena = request.app.state.arena
    return bool(arena.gated.get(request.cookies.get(gate.COOKIE_NAME, "")))


def _client_key(request: Request) -> str:
    """The address the gate limit counts against.

    **The rightmost `X-Forwarded-For` entry, not the leftmost.** Ingress is
    `INGRESS_TRAFFIC_ALL` straight to Cloud Run with no load balancer in front
    (`infra/hosting/cloudrun.tf`), and Cloud Run *appends* the address it
    observed to whatever the client sent. So the leftmost entry is
    attacker-controlled — a caller supplying a fresh `X-Forwarded-For` per
    request would get a fresh bucket per request and no limit at all — and the
    rightmost is the one the platform wrote.

    If a load balancer is ever put in front of this service, the rightmost
    entry becomes the balancer and every caller collapses into one bucket. That
    fails restrictive rather than open, which is the right direction, but it
    would need this function changed rather than left to be discovered.

    **`getlist`, not `get`.** Repeated header fields are semantically one
    comma-joined list, but `Headers.get` returns the *first* field line only.
    If Cloud Run appends its observation as a separate `X-Forwarded-For` line
    rather than joining it onto the client's, `get` hands back the attacker's
    line and the last token in it is attacker-chosen — a fresh bucket per
    request, and no per-address limit at all. Joining every field line first is
    correct under both behaviours, and this repository cannot currently observe
    which one the platform does, because the module has never been applied.

    **The token has to parse as an address.** Otherwise the bucket key is
    arbitrary header content of arbitrary length, retained for as long as the
    window holds it. Scanning right to left for the first value that parses
    also steps over a trailing `unknown`, an obfuscated token, or a port
    suffix, all of which appear in real `X-Forwarded-For` chains.
    """
    joined = ",".join(request.headers.getlist("x-forwarded-for"))
    for candidate in reversed([part.strip() for part in joined.split(",") if part.strip()]):
        if (address := _as_address(candidate)) is not None:
            return address
    return request.client.host if request.client else "unknown"


def _as_address(token: str) -> str | None:
    """One `X-Forwarded-For` element as a canonical address, or `None`.

    Normalised rather than passed through, so that two spellings of one address
    cannot buy two buckets. The forms handled are the ones that appear in real
    chains: `[::1]:443`, `192.0.2.1:443`, and a link-local IPv6 zone suffix,
    which is not part of identity.
    """
    if token.startswith("["):
        token = token[1:].partition("]")[0]
    elif token.count(":") == 1:
        token = token.partition(":")[0]
    try:
        return str(ipaddress.ip_address(token.rsplit("%", 1)[0]))
    except ValueError:
        return None


def _require_gate(request: Request) -> Response | None:
    return None if _gate_ok(request) else RedirectResponse("/", status_code=303)


async def index(request: Request) -> Response:
    if _gate_ok(request):
        return RedirectResponse("/arena", status_code=303)
    return HTMLResponse(render.gate())


async def submit_gate(request: Request) -> Response:
    """FR-1. A wrong code re-renders the form and says nothing else — there is
    no account to enumerate, but a gate that distinguishes 'wrong code' from
    'no code' teaches an attacker the shape of the check for no benefit.

    Rate limited by client address since TD-27. FR-2 is right that the code is
    not a security boundary; it is still the only thing between the open
    internet and a token budget, and until this it could be guessed at network
    speed. The limit is checked before the comparison, so it applies to a
    correct code too — see `quota.Quota.check_gate`.
    """
    arena: Arena = request.app.state.arena
    try:
        arena.quota.check_gate(
            _client_key(request),
            bypass=quota.bypass_granted(request.headers.get(quota.BYPASS_HEADER)),
        )
    except quota.QuotaExceeded as exc:
        # The wording says rate-limited rather than reusing the refusal text:
        # it tells an attacker nothing about the code, and an operator locked
        # out by their own limit needs to know which thing happened.
        return HTMLResponse(
            render.gate("Too many attempts. Try again shortly."),
            status_code=429,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    form = await _form(request)
    if not gate.matches(str(form.get("code", "")), arena.gate_code):
        return HTMLResponse(render.gate("That code was not accepted."), status_code=403)

    token = secrets.token_urlsafe(32)
    arena.gated.put(token, True)
    response = RedirectResponse("/arena", status_code=303)
    response.set_cookie(
        gate.COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=True,
        # One source for the two lifetimes. The server-side entry expiring at a
        # different age from the cookie is either residue nothing can present
        # or a cookie that bounces, depending on which way they drift.
        max_age=int(arena.gate_token_ttl_seconds),
    )
    return response


async def arena_page(request: Request) -> Response:
    return _require_gate(request) or HTMLResponse(render.picker())


async def start_session(request: Request) -> Response:
    """FR-3, FR-4 — persona, agent and level become a `bta.agent.Session`.

    `Agent` and `Level` are constructed from the form here, at the edge, which
    is exactly the arrival point `bta.agent.loop.Session` documents
    `require_level`/`require_agent` as existing for: a plain string compares
    equal to a StrEnum member but fails `is`, and the credential dispatch and
    the client dispatch use `is`.
    """
    if redirect := _require_gate(request):
        return redirect

    arena: Arena = request.app.state.arena
    form = await _form(request)

    try:
        persona = personas.resolve(str(form.get("persona", "")))
        agent = Agent(str(form.get("agent", "")))
        level = Level(str(form.get("level", "")))
    except (personas.PersonaUnavailable, ValueError) as exc:
        return HTMLResponse(render.picker(str(exc)), status_code=400)

    try:
        # Off the event loop. `_default_build_session` mints a persona token
        # through two synchronous HTTP calls with 10s timeouts each; run inline
        # in this coroutine they block every open SSE stream on the instance
        # for as long as they take. `bta.agent.loop` puts provider calls in a
        # thread for the same reason.
        session = await asyncio.to_thread(
            request.app.state.build_session, arena, persona, agent, level
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the operator, not swallowed
        return HTMLResponse(render.picker(f"Could not start a session: {exc}"), status_code=502)

    key = secrets.token_urlsafe(24)
    arena.sessions.put(
        key,
        BrowserSession(
            persona=persona.sub,
            agent=agent.value,
            level=level.value,
            session=session,
            run_id=(str(form.get("run_id", "")).strip() or None),
        ),
    )
    response = RedirectResponse("/chat", status_code=303)
    response.set_cookie(SESSION_COOKIE, key, httponly=True, samesite="lax", secure=True)
    return response


def _browser_session(request: Request) -> BrowserSession | None:
    arena: Arena = request.app.state.arena
    return arena.sessions.get(request.cookies.get(SESSION_COOKIE, ""))


async def chat_page(request: Request) -> Response:
    if redirect := _require_gate(request):
        return redirect
    browser = _browser_session(request)
    if browser is None:
        return RedirectResponse("/arena", status_code=303)
    return HTMLResponse(
        render.chat(
            persona=browser.persona,
            agent=browser.agent,
            level=browser.level,
            events=[e.to_dict() for e in browser.session.transcript.events],
            captures=browser.captures.for_display(),
        )
    )


def _rescan(arena: Arena, browser: BrowserSession) -> None:
    """FR-6 — after every turn, both channels."""
    found = capture.scan(browser.session.transcript, arena.fixtures)
    browser.captures = browser.captures.merged(found)


async def message(request: Request) -> Response:
    """One user turn. Streams when asked to, re-renders when not.

    The non-streaming branch is not a fallback nobody runs — it is what a
    client with JavaScript disabled gets (ADR 0007's progressive enhancement),
    and it exercises the same code path.
    """
    if redirect := _require_gate(request):
        return redirect
    browser = _browser_session(request)
    if browser is None:
        return RedirectResponse("/arena", status_code=303)

    arena: Arena = request.app.state.arena
    form = await _form(request)
    text = str(form.get("text", "")).strip()
    if not text:
        return RedirectResponse("/chat", status_code=303)

    bypass = quota.bypass_granted(request.headers.get(quota.BYPASS_HEADER))
    try:
        arena.quota.check_and_record(browser.persona, bypass=bypass)
    except quota.QuotaExceeded as exc:
        return JSONResponse(
            {"error": exc.limit, "retry_after_seconds": exc.retry_after_seconds},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    if str(form.get("stream", "")) != "1":
        before = _tokens_used(browser.session)
        try:
            await browser.session.send(text)
        except Exception:
            logging.exception("provider call failed during a non-streamed turn")
            raise
        finally:
            # In `finally`, so a turn that failed part-way still charges what
            # it spent. Tokens are consumed before the error, not after it.
            arena.quota.record_tokens(_tokens_used(browser.session) - before)
            # And in `finally` for the same reason, which is the stronger case
            # of the two: a tool that ran before the failure has already
            # disclosed, and the evidence is already in the transcript.
            _rescan(arena, browser)
        return RedirectResponse("/chat", status_code=303)

    return StreamingResponse(
        _stream(arena, browser, text, bypass=bypass),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _tokens_used(session: Any) -> int:
    """The session's running totals, for taking a difference against.

    `Transcript.input_tokens`/`output_tokens` accumulate across the session
    (`loop.py` uses `+=`), so recording them directly charges turn 1 once, turn
    2 the sum of turns 1 and 2, and so on — a twenty-turn session bills roughly
    ten times what it used. Conservative, so never a budget risk, but it
    reaches the daily ceiling far below the configured value.
    """
    transcript = session.transcript
    return transcript.input_tokens + transcript.output_tokens


async def _stream(
    arena: Arena, browser: BrowserSession, text: str, *, bypass: bool = False
) -> AsyncIterator[str]:
    """SSE.

    **This streams turn-level events, not tokens** — `bta.providers` exposes
    `complete`, not a streaming call, so the model's text arrives in one piece.
    NFR-1 asks for streaming to *begin* within 5s at p95, and an acknowledgement
    frame is not what it means. TD-23 records the gap; closing it is a change to
    the provider protocol, which is phase 3 code and out of this phase's tree.

    What this does buy today is that tool calls and captures render without a
    page reload, which is what makes the `tool_result` channel visible to a
    human while they work (FR-5).
    """

    def frame(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    yield frame({"status": "working"})
    before = _tokens_used(browser.session)
    try:
        await browser.session.send(text)
    except Exception as exc:  # noqa: BLE001 — the attacker is told, the run continues
        arena.quota.record_tokens(_tokens_used(browser.session) - before)
        # **Logged server-side, class name to the browser.** The attacker gets
        # no provider internals — a 429 body naming a base model and a quota
        # metric is deployment detail. But an operator needs the reason, and
        # before this nothing recorded it anywhere: a provider failure rendered
        # as a turn that silently did not happen, and diagnosing it needed the
        # call reproduced by hand outside the service.
        logging.exception("provider call failed during a streamed turn")
        # Scanned before the error frame, for the same reason the token charge
        # above is in a `finally`: a turn that failed part-way still disclosed
        # what it disclosed. The failing call is the model's *follow-up* — by
        # the time it happens the tool has already run and its payload is in
        # the transcript, so discarding the scan throws away a `tool_result`
        # capture that `schemas.md` §8 calls the lab's central claim in one
        # row. Observed on 2026-08-29: Gemini 400s on the follow-up call and
        # every genuine L1 disclosure scored `not_captured`.
        _rescan(arena, browser)
        # **The coarse class goes only to the NFR-3a bypass holder.** What an
        # attacker sees is unchanged: a class name and nothing about the
        # deployment. But the suite is the operator's instrument rather than a
        # caller, and NFR-2 requires a run to publish the provider limit it
        # actually hit — the first live L1 run lost 29 executions to Vertex
        # 429s and reported "none hit", because `ProviderError` is all that
        # crossed this boundary and a site-side 429 is the only limit the
        # runner could count. Two buckets is the whole vocabulary; a quota
        # metric and a base model stay on this side.
        payload = {
            "status": "error",
            "detail": type(exc).__name__,
            "captures": render.captures_panel(browser.captures.for_display()),
            "captured": browser.captures.for_display(),
        }
        if bypass:
            payload["reason"] = classify_failure(exc)
        yield frame(payload)
        return

    _rescan(arena, browser)
    arena.quota.record_tokens(_tokens_used(browser.session) - before)
    # VER-6, §5. Redaction runs on every turn, not once before publication, and
    # the raw copy is written only for a run — see `bta.site.evidence`.
    kept = evidence.persist(
        browser.session.transcript.to_dict(),
        dict(arena.fixtures.values),
        run_id=browser.run_id,
        session_id=browser.session.transcript.session_id,
        # The turn this snapshots. Objects are never overwritten — see
        # `evidence.object_name` — so each turn needs its own sequence.
        sequence=len(browser.session.transcript.events),
        bucket=arena.evidence_bucket,
        writer=arena.evidence_writer,
    )
    # TD-25, TD-31. The records this session produced, by session id — the
    # sink is shared across the process, and a run result must name only the
    # calls its own case made.
    session_records = [
        r
        for r in getattr(arena.sink, "records", [])
        if r.session_id == browser.session.transcript.session_id
    ]
    audit_ref = evidence.persist_audit(
        session_records,
        dict(arena.fixtures.values),
        run_id=browser.run_id,
        session_id=browser.session.transcript.session_id,
        sequence=len(browser.session.transcript.events),
        bucket=arena.evidence_bucket,
        writer=arena.evidence_writer,
    )
    yield frame(
        {
            "status": "done",
            # §5's `tool_call_ids`. Sent alongside `audit_ref` and not instead
            # of it: TD-31 records that an id resolving to nothing is worse
            # than an empty list, so the ids travel only now that the records
            # they name are durable.
            "tool_call_ids": [r.id for r in session_records],
            "audit_ref": audit_ref,
            # §5's two refs. `transcript_ref` names an object the caller must
            # record and must not fetch — it holds live values. The redacted
            # document travels inline because the caller is the suite, which
            # may not hold a value and therefore cannot redact for itself.
            "transcript_ref": kept.raw_ref,
            "redacted_transcript": kept.redacted,
            "transcript": "".join(
                render.turn(e.to_dict()) for e in browser.session.transcript.events
            ),
            "captures": render.captures_panel(browser.captures.for_display()),
            # The same information as `captures`, unrendered. The browser uses
            # the HTML; the attack suite uses this, because scraping a panel
            # for evidence makes a run result hostage to a template change.
            #
            # It is the identical `for_display()` list — flag ids, channels and
            # the normalisation that matched, and **never a value**. So this
            # adds no disclosure to a surface that is already showing the
            # attacker the same facts, and it keeps `schemas.md` §3's rule that
            # only the generator and the verifier hold values: the suite is
            # told *that* a flag came out, by the process that detected it.
            "captured": browser.captures.for_display(),
            # NFR-2 publishes the achieved runtime and the provider limit that
            # bounded it. A per-turn token count is what lets the runner
            # attribute a slow run to spend rather than to concurrency.
            "tokens": {
                "input": browser.session.transcript.input_tokens,
                "output": browser.session.transcript.output_tokens,
            },
        }
    )


async def matrix_page(request: Request) -> Response:
    """FR-8. Reads the committed reference run, which does not exist until
    phase 6 — and says so rather than rendering an empty table."""
    if redirect := _require_gate(request):
        return redirect
    path = os.environ.get("BTA_MATRIX_PATH", "")
    document = None
    if path and os.path.isfile(path):
        with contextlib.suppress(json.JSONDecodeError, OSError):
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
    return HTMLResponse(render.matrix(document))


async def healthz(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


SITE_PROVIDER_VARIABLE = "BTA_SITE_PROVIDER"

#: The interactive path runs one model at a time; FR-22's three are the
#: *suite's* dimension, not a browser session's. Which one is a deployment
#: input rather than a constant, because "which models this project may call"
#: is a property of the deployment: Vertex partner models start at zero tokens
#: per minute on a new project and stay there until a quota request is granted,
#: so the model that works today is not the one that works next week. Before
#: this it was hardcoded to Anthropic, and a project without Claude quota had
#: no working site at all.
_INTERACTIVE_PROVIDERS = {
    "anthropic": ("bta.providers.anthropic_provider", "AnthropicProvider", "BTA_MODEL_ANTHROPIC"),
    "google": ("bta.providers.vertex_provider", "VertexProvider", "BTA_MODEL_GOOGLE"),
    "xai": ("bta.providers.xai_provider", "XAIProvider", "BTA_MODEL_XAI"),
}


def _resolve_interactive_provider():
    """Which provider class and pinned model the configuration selects.

    Separate from constructing them, because construction resolves Application
    Default Credentials and this repository's site tests are offline by
    contract — see `tests/test_site.py`'s docstring. Selection is the half that
    can be wrong in a way a test can observe; a constructor needing a live
    credential can only be exercised by a deployment, and a test that
    constructed one passed locally and could never pass in CI.

    Fails loudly on an unknown name or a missing model identifier rather than
    falling back to a default: a site that silently ran a different model from
    the one configured would record the wrong identifier on every capture, and
    FR-22a exists because comparisons across model versions are meaningless.
    """
    import importlib

    name = os.environ.get(SITE_PROVIDER_VARIABLE, "anthropic").strip() or "anthropic"
    if name not in _INTERACTIVE_PROVIDERS:
        raise RuntimeError(
            f"{SITE_PROVIDER_VARIABLE}={name!r} is not one of {sorted(_INTERACTIVE_PROVIDERS)}."
        )
    module_name, attribute, model_variable = _INTERACTIVE_PROVIDERS[name]
    model = os.environ.get(model_variable, "").strip()
    if not model:
        raise RuntimeError(
            f"{SITE_PROVIDER_VARIABLE}={name!r} but {model_variable} is unset. "
            f"Model identifiers are pinned in configuration and recorded per "
            f"run (FR-22a)."
        )
    return getattr(importlib.import_module(module_name), attribute), model


def _interactive_provider():
    """The selected provider, constructed. Needs credentials — see above."""
    provider_cls, model = _resolve_interactive_provider()
    return provider_cls(), model


def _default_build_session(arena: Arena, persona: personas.Persona, agent, level) -> Session:
    """Wire a persona through the identity chain into a `Session`.

    Kept as a function on `app.state` so a test can substitute it without a
    live Identity Platform tenant. That is a seam for testing, not a way to run
    the lab with a fake identity — `main()` always installs this one.

    The credential path below is the whole experiment in five lines: the
    `CredentialFactory` is handed a minter that can produce all three modes,
    and which one a given tool call actually gets is
    `bta.credentials.policy.mode_for(level, surface)`'s decision and nothing
    that happens here.
    """
    from bta.credentials.factory import CredentialFactory, GoogleCredentialMinter
    from bta.identity import CallerIdentity
    from bta.identity.tokens import SubjectToken
    from bta.memory.firestore import FirestoreMemoryStore
    from bta.tools.clients import EstateClients

    provider, model = _interactive_provider()

    session_id = f"S-{uuid.uuid4().hex[:8]}"
    caller = CallerIdentity(
        sub=persona.sub,
        team=persona.team,
        subject_token=SubjectToken(personas.id_token_for(persona.sub)),
    )
    minter = GoogleCredentialMinter(
        shared_service_account=arena.config.shared_service_account,
        provisioning_service_account=arena.config.provisioning_service_account,
        workload_identity_audience=arena.config.workload_identity_audience,
    )
    clients = EstateClients(
        config=arena.config,
        credentials=CredentialFactory(minter),
        # F6 is a write-path outcome with no cloud resource behind it — TD-13,
        # ADR 0003. The value arrives from the platform, never from a lookup.
        write_path_marker=arena.markers.f6,
    )

    return Session(
        agent=agent,
        level=level,
        caller=caller,
        provider=provider,
        model=model,
        clients=clients,
        sink=arena.sink,
        session_id=session_id,
        # FR-12, TD-17. Deploy Bot only — `Session._memory_block` and
        # `Session._remember` both no-op for Onboarding, so this is constructed
        # unconditionally and used conditionally. Construction opens nothing;
        # the first request is the first read.
        #
        # The store authorises as the persona at every level, not as the
        # runtime service account, which is what makes `firestore.rules`
        # adjudicate F7b rather than our own query filter.
        memory=FirestoreMemoryStore(
            arena.config.firestore_documents_url(),
            caller.subject_token.reveal(),
            session_id,
        ),
        marker_c1=arena.markers.c1,
        # F4. At L1 and L2 the key is in the Onboarding system prompt by design
        # (FR-14) and `render` raises without it, so before this the Onboarding
        # agent could not be started through the UI at either level — the
        # attacker got a 502 and the whole column was unreachable.
        integration_key=arena.markers.f4,
    )


def build_app(arena: Arena, *, build_session=_default_build_session) -> Starlette:
    routes = [
        Route("/", index),
        Route("/gate", submit_gate, methods=["POST"]),
        Route("/arena", arena_page),
        Route("/session", start_session, methods=["POST"]),
        Route("/chat", chat_page),
        Route("/message", message, methods=["POST"]),
        Route("/matrix", matrix_page),
        Route("/healthz", healthz),
    ]
    app = Starlette(routes=routes)
    app.state.arena = arena
    app.state.build_session = build_session
    return app


def startup_checks(arena: Arena) -> None:
    """SEC-11a, before the first request.

    Fails closed. See `bta.site.sec11a` for why an unverifiable surface counts
    as a failure rather than a pass.
    """
    findings = sec11a.verify(
        project_id=arena.config.project_id,
        dataset_id=arena.config.dataset_id,
        bucket=arena.config.artefact_bucket,
        firestore_database_id=arena.config.firestore_database_id,
    )
    sec11a.assert_worthless(findings)


__all__ = ["Arena", "BrowserSession", "build_app", "startup_checks"]
