"""The HTTP driver — one deployment, one session at a time.

**The suite attacks through the same surface a person does** (FR-20, and PRD §1:
"the site exists so a person can reproduce by hand what the suite reproduces
automatically"). It gates, picks a persona, starts a session and posts turns.
There is no suite-only endpoint and no in-process shortcut, because an
in-process run would exercise none of the things L3 actually rests on — the STS
exchange, the row access policies, the bucket bindings, the Security Rules.

## Where captures come from

From the deployment, never from here. The site scans both channels after every
turn (FR-6) using the verifier, which is the only component besides the
generator permitted to hold flag values (`schemas.md` §3). The SSE `done` frame
carries the structured result — flag id, channel, and the normalisation that
matched — and this module records it verbatim.

That is why `suite` is forbidden from importing `verifier` and `bta.generator`:
the suite is told *that* a flag came out, by the process that detected it, and
never holds a value it could accidentally write into a result.

## Rate limits

Every request carries the NFR-3a bypass token, which lifts the per-persona rate
limits and **not** the daily token ceiling. A 429 is therefore a real signal
rather than a nuisance, and `RateLimited` carries which limit fired so NFR-2 can
publish the constraint the run actually hit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

#: A wire literal, like the `"done"` and `"error"` statuses below, rather than
#: an import from `bta.providers` where the site's half lives. The suite is
#: *told* things by the deployment over the same HTTP surface a person uses
#: (ADR 0013); sharing a constant would be the first shared symbol on a seam
#: the project keeps deliberately thin. `tests/test_suite_client.py` pins the
#: two spellings together so the decoupling cannot become a drift.
RATE_LIMITED_REASON = "provider_rate_limited"

BYPASS_HEADER = "X-BTA-Suite-Bypass"

#: Mirrors `bta.site.gate.COOKIE_NAME`. Not imported: `suite` drives the
#: deployment over HTTP and does not link against the site it is attacking.
GATE_COOKIE_NAME = "bta_gate"


class SiteError(RuntimeError):
    """The deployment refused or misbehaved. Becomes an `error` run result."""


class RateLimited(SiteError):
    """A 429 from the site, with the limit that fired.

    Recorded rather than merely retried: NFR-2 restates the 90-minute target as
    a target and requires the run to publish "the provider limit actually hit",
    and a limit that was silently backed off from is one nobody can publish.

    **A plain exception, deliberately — not `@dataclass(frozen=True,
    slots=True)`, which is what this was until 2026-09-15.** An exception is
    mutated by the interpreter as it propagates: `__traceback__`, `__context__`
    and `__cause__` are all assigned after construction, and `frozen=True`
    forbids every one of them. `slots=True` compounds it by rebuilding the
    class object, so the `super()` captured in the generated `__setattr__`
    refers to the superseded class and the failure reads
    `super(type, obj): obj (instance of RateLimited) is not an instance or
    subtype of type (RateLimited)` — which names the right class twice and
    explains nothing.

    It stayed latent because `_raise_for_limit` crashed on `ResponseNotRead`
    before this was ever raised inside `send`'s `async with`; fixing that
    exposed it, and it crashed all 265 remaining L2 executions. `docs/decisions/
    0002-schema-records-are-dataclasses.md` governs schema *records*, and an
    exception is not one.
    """

    def __init__(self, *, limit: str, retry_after_seconds: int) -> None:
        super().__init__(limit, retry_after_seconds)
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds

    def __str__(self) -> str:
        return f"rate limited: {self.limit} (retry after {self.retry_after_seconds}s)"


@dataclass
class TurnOutcome:
    """What one posted turn produced."""

    captured: list[dict[str, Any]] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})
    error: str | None = None
    #: `schemas.md` §5. A `gs://` URI naming the raw transcript, which holds
    #: live flag values — **recorded and never fetched.** The suite may not
    #: hold a value (SC-6 commits run results; VER-6 keeps raw transcripts out
    #: of git), so this is a string that travels into the result and nothing
    #: else.
    transcript_ref: str | None = None
    #: The redacted document itself, markers in place of values, so the runner
    #: can write it beside the results without ever holding a value.
    redacted_transcript: Any = None
    #: §5's `tool_call_ids` — the audit records this turn produced. Ids only;
    #: the records themselves stay in the evidence bucket, because §6's
    #: `parameters` carry attacker-supplied injections and the suite has no
    #: reason to hold them.
    tool_call_ids: list[str] = field(default_factory=list)


class ArenaClient:
    """One browser, for the life of one session.

    Holds its own cookie jar. `httpx.AsyncClient` keeps the gate cookie and the
    session cookie across requests exactly as a browser would, which is what
    lets the suite use the unmodified interactive routes.
    """

    def __init__(
        self,
        base_url: str,
        *,
        access_code: str,
        bypass_token: str,
        timeout: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
        gate_cookie: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_code = access_code
        self._headers = {BYPASS_HEADER: bypass_token} if bypass_token else {}
        self._timeout = timeout
        # A seam for driving the real Starlette app in-process over
        # `httpx.ASGITransport`. The suite's offline tests use it to exercise
        # gate → session → turn → capture against the actual routes, which is
        # the only way to catch a protocol drift between the site's SSE frame
        # and what the runner reads out of it. Never set in a real run.
        self._transport = transport
        # A gate token obtained by an earlier client, reused rather than
        # re-earned. `schemas.md` §4 makes each phase a fresh session, so a
        # multi-phase case opens a fresh client per phase — and re-gating each
        # time meant one `POST /gate` per phase, on the order of 2,900 for a
        # full run against a limit of ten a minute. A browser does not re-enter
        # the access code for every tab, and neither should this.
        self._gate_cookie = gate_cookie
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ArenaClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=self._timeout,
            follow_redirects=True,
            transport=self._transport,
        )
        if self._gate_cookie:
            self._client.cookies.set(GATE_COOKIE_NAME, self._gate_cookie)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise SiteError("ArenaClient used outside its context manager")
        return self._client

    @staticmethod
    async def _raise_for_limit(response: httpx.Response) -> None:
        """Turn a 429 into `RateLimited`, which the runner retries.

        **`await response.aread()` before touching the body, and it is not
        optional.** `send` opens `/message` with `http.stream(...)`, so its
        response arrives unread; `response.json()` on it raises
        `httpx.ResponseNotRead`, which is not in the `except` tuple below and
        so escaped as far as the worker. On 2026-09-15 that crashed 263 of 324
        L2 executions — every one of them a *retryable* provider 429 that the
        bounded retry would have absorbed, turned into a dead worker instead.
        The other two call sites are non-streaming and `aread()` is a cached
        no-op for them, so this is one fix rather than a branch.
        """
        if response.status_code != 429:
            return
        limit, retry = "unknown", 60
        try:
            await response.aread()
            body = response.json()
            limit = str(body.get("error", limit))
            retry = int(body.get("retry_after_seconds", retry))
        except (json.JSONDecodeError, ValueError, TypeError, httpx.ResponseNotRead):
            # The gate's 429 is HTML, not JSON — it renders a page rather than
            # returning a body, so the header is the only thing to read.
            retry = int(response.headers.get("Retry-After", retry))
            limit = "gate"
        raise RateLimited(limit=limit, retry_after_seconds=retry)

    @property
    def gate_cookie(self) -> str | None:
        """The token this client holds, for handing to the next one."""
        return self.http.cookies.get(GATE_COOKIE_NAME)

    async def gate(self) -> str:
        """FR-1. Not authentication (FR-2) — a shared secret keeping drive-by
        traffic off an internet-reachable vulnerable service.

        Returns the token so a caller can pass it to later clients. A client
        constructed with one already skips this entirely.
        """
        if self._gate_cookie:
            return self._gate_cookie
        response = await self.http.post("/gate", data={"code": self._access_code})
        await self._raise_for_limit(response)
        if response.status_code >= 400:
            raise SiteError(f"gate refused with {response.status_code}")
        token = self.gate_cookie
        if not token:
            raise SiteError("gate accepted the code but issued no cookie")
        self._gate_cookie = token
        return token

    async def start_session(
        self, *, persona: str, agent: str, level: str, run_id: str | None = None
    ) -> None:
        """FR-3, FR-4. Mints the persona token and builds the agent session.

        `run_id` is what tells the site this session is evidence rather than a
        person browsing: `bta.site.evidence` writes a raw transcript only for a
        session that carries one, because FR-7 says a browser session leaves
        nothing behind.
        """
        data = {"persona": persona, "agent": agent, "level": level}
        if run_id:
            data["run_id"] = run_id
        response = await self.http.post("/session", data=data)
        await self._raise_for_limit(response)
        if response.status_code >= 400:
            # **Status code only, never the body.** `/session`'s 502 path
            # renders `Could not start a session: {exc}` from a builder that
            # constructs a `Session` holding `marker_c1` and the integration
            # key — so an exception message echoing one would put a live flag
            # value into a run result, which SC-6 commits to git and which
            # obliges a reseed of the estate. `schemas.md` §3 exists to keep
            # this path from existing at all.
            raise SiteError(f"session refused with {response.status_code}")

    async def send(self, text: str) -> TurnOutcome:
        """One turn, over SSE, reading the structured `done` frame.

        The streaming branch rather than the redirect branch because it is the
        only one that returns anything machine-readable; the non-streaming path
        answers with a 303 to a rendered page.
        """
        outcome = TurnOutcome()
        saw_terminal = False
        async with self.http.stream(
            "POST", "/message", data={"text": text, "stream": "1"}
        ) as response:
            await self._raise_for_limit(response)
            if response.status_code >= 400:
                raise SiteError(f"turn refused with {response.status_code}")

            # **A redirected turn did not happen, and must never be recorded as
            # a non-capture.** `/message` answers 303 -> /arena when the gate or
            # session cookie is not found on the instance that served it. With
            # `follow_redirects=True` that resolves to a 200 HTML page carrying
            # no SSE frames, so without this check `send` returns cleanly with
            # no error and no captures and the execution scores `not_captured`
            # — a completed non-error execution counting toward §8 rule 2.
            #
            # Session state is per-instance (`bta.site.state`, TD-24). Under
            # concurrency a share of turns land on an instance that has never
            # seen the cookie, so this failure is routine rather than exotic,
            # and it is invisible: the run completes and the column reads held.
            if response.history:
                raise SiteError(
                    f"turn redirected to {response.url.path} — the gate or "
                    f"session cookie was not found on the instance that served "
                    f"it. This is a lost session, not a non-capture."
                )

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                frame = json.loads(line.removeprefix("data: "))
                status = frame.get("status")
                if status == "error":
                    saw_terminal = True
                    # The site returns a class name, never provider internals.
                    # It is an execution failure, not a verdict: `schemas.md`
                    # §5 says an error contributes nothing to the threshold.
                    # `reason` is present only for a bypass-token caller and
                    # only as one of two coarse classes. Rendered into the
                    # `rate limited: ...` shape `RateLimited.__str__` already
                    # uses, because that string is what `RunSummary` counts
                    # into `limits_hit` and NFR-2 publishes — a provider-side
                    # 429 and a site-side one bound a run the same way and
                    # belong in the same tally.
                    detail = str(frame.get("detail", "unknown"))
                    if frame.get("reason") == RATE_LIMITED_REASON:
                        outcome.error = (
                            f"rate limited: provider "
                            f"(the deployment's own model call was throttled; {detail})"
                        )
                    else:
                        outcome.error = detail
                elif status == "done":
                    saw_terminal = True
                    outcome.captured = list(frame.get("captured", []))
                    outcome.tokens = dict(frame.get("tokens", outcome.tokens))
                    outcome.transcript_ref = frame.get("transcript_ref")
                    outcome.redacted_transcript = frame.get("redacted_transcript")
                    outcome.tool_call_ids = list(frame.get("tool_call_ids", []))

        # A stream that ended without `done` or `error` — a dropped connection,
        # a truncated response — is an execution that did not complete. Same
        # argument as the redirect above: silence is not evidence of a hold.
        if not saw_terminal:
            raise SiteError("stream ended with no terminal frame; the turn did not complete")
        return outcome


__all__ = ["ArenaClient", "RateLimited", "SiteError", "TurnOutcome"]
