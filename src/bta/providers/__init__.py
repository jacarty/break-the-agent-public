"""One protocol, three SDKs — FR-22, POR-2.

POR-2 asks for a **provider** abstraction rather than a model abstraction:
swapping provider swaps the SDK behind one interface, and nothing above this
package knows which one it is talking to. FR-22 requires three providers from
three vendors, with Google served by Vertex AI under IAM rather than an API
key, so the lab holds key material for two providers instead of three (SEC-9).

There is no routing gateway. Each vendor's own SDK is used directly, because a
gateway would put a fourth party between the lab and the claim it publishes
about which model produced which result.

## The normalisation this package exists to do

Every SDK returns a different shape, and two of the differences are the kind
that fail quietly:

**Tool-call arguments.** OpenAI returns them as a **JSON string**; Anthropic
and Vertex return a mapping. A loop that passed either straight to
`boundary.invoke` would hand a tool a string where it expected keyword
arguments — or, worse, would `dict()` a string of the right shape and get
something plausible. Every adapter here parses to a `dict[str, Any]` and
`ToolCall.__post_init__` refuses anything else.

**Which model actually served the request.** A provider may resolve an alias to
a dated version. `Completion` carries both `model_requested` — what the pin in
configuration said — and `model_served`, what the response reported. FR-22a
requires the identifier be recorded per run, and recording the request when the
two differ would put an unreproducible matrix in the repository while looking
correct.

## SEC-9

Provider keys are read from the environment by `Provider` implementations and
are never placed in agent context, never reachable by a tool, and never
returned in an error. `bta.agent` never sees a key: it holds a `Provider`,
which holds a client, which holds the key. `tests/test_providers.py` asserts
that no key material reaches a system prompt, a message, a tool schema or a
transcript.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

#: Coarse failure classes, for the operator's instrument and not for a caller.
#: Two buckets on purpose: enough for NFR-2 to publish *which* limit bound a
#: run, and not enough to describe the deployment. A 429 body naming a base
#: model, a region and a quota metric is deployment detail; "throttled" is not.
FAILURE_RATE_LIMITED = "provider_rate_limited"
FAILURE_OTHER = "provider_error"

#: Every vendor spells the same 429 differently, and `ProviderError` deliberately
#: does not carry the SDK's message — so the chain is walked rather than the
#: string matched. `google.genai` raises `ClientError(429, ...)`, Anthropic and
#: OpenAI both raise a `RateLimitError`; the status attribute catches the rest.
_RATE_LIMIT_MARKERS = ("RESOURCE_EXHAUSTED", "rate limit", "quota exceeded", "too many requests")


def classify_failure(exc: BaseException) -> str:
    """Which coarse class a provider failure belongs to.

    Walks `__cause__`, because the loop wraps every SDK exception in a
    `ProviderError` whose message is written here rather than taken from the
    vendor (SEC-9, VER-5). The cause is the only thing that knows it was a 429,
    and without this a throttled run reports "binding provider limit: none hit"
    while a third of its executions die on quota — which is NFR-2 publishing
    the opposite of what happened. Observed on the first live L1 run.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if "ratelimit" in type(current).__name__.replace("_", "").lower():
            return FAILURE_RATE_LIMITED
        for attribute in ("status_code", "code", "status"):
            if getattr(current, attribute, None) in (429, "429"):
                return FAILURE_RATE_LIMITED
        rendered = str(current).lower()
        if any(marker.lower() in rendered for marker in _RATE_LIMIT_MARKERS):
            return FAILURE_RATE_LIMITED
        current = current.__cause__ or current.__context__
    return FAILURE_OTHER


class ProviderError(RuntimeError):
    """The provider call failed.

    The message this carries is written here, not taken from the SDK, so
    nothing persisted quotes a provider verbatim — `bta.agent.loop` raises
    `ProviderError(f"{self.provider.name} call failed")`.

    **The chained `__cause__` is a different matter and is deliberate.** It
    keeps the SDK's own exception reachable for local debugging, and it
    survives into any rendered traceback. Nothing persists a traceback today.
    Phase 5 populates `run_result.error` (`schemas.md` §5), and if it does so
    from a formatted traceback rather than from a type name, the provider's
    message reaches a committed artefact and this promise breaks. The decision
    belongs to the phase with the consumer; it is named here so it is a
    decision rather than a discovery.
    """


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model's request to call a tool, normalised across three SDKs."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    #: Set when the provider's arguments could not be parsed. The loop feeds
    #: this back to the model and invokes nothing — a model emitting malformed
    #: JSON is ordinary behaviour, not a defect in the lab, and killing the
    #: session over it would make the run an `error` (`schemas.md` §5), which
    #: contributes nothing to the SC-3 threshold and pushes cells toward
    #: `insufficient` for reasons that have nothing to do with a control.
    parse_error: str | None = None
    #: Opaque provider state that must survive a round trip, set and read by
    #: **one** adapter and never interpreted by the loop or by another provider.
    #:
    #: It exists because Gemini requires it. A `functionCall` part comes back
    #: carrying a `thought_signature`, and the *next* request must echo it on
    #: the same part or the API rejects the whole conversation with
    #: `400 INVALID_ARGUMENT: Function call is missing a thought_signature`.
    #: Dropping it made every tool-using turn on the google provider fail on
    #: the follow-up call — after the tool had already run and disclosed
    #: (observed 2026-08-29).
    #:
    #: Typed `Any` and named for what it is rather than for Gemini, because
    #: POR-2 puts the abstraction at the provider: a `thought_signature` field
    #: on the shared record would put one SDK's vocabulary in all three. An
    #: adapter that did not set it sees `None` and behaves as before.
    provider_state: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.arguments, dict):
            raise ProviderError(
                f"tool call {self.name!r} carries {type(self.arguments).__name__} "
                f"arguments, not a dict. OpenAI returns a JSON string here and "
                f"the adapter is responsible for parsing it; passing a string on "
                f"would reach `boundary.invoke` as a single positional value."
            )


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True, slots=True)
class SchemaParameter:
    """One parameter, with the two facts a provider schema needs.

    Bare names were not enough, and the reason is not cosmetic. Rendering every
    parameter as a required string made `search_deploys`'s
    `include_artefact: bool = False` arrive as the string `"false"` — truthy —
    so F2's Cloud Storage carrier fired on every call. See
    `bta.tools.registry.ParameterSpec`.
    """

    name: str
    json_type: str
    required: bool


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """A tool as a provider sees it.

    Built by `bta.agent` from a `ToolSpec` with C1's marker already rendered
    into the description. This package never touches the registry: it renders
    what it is given.
    """

    name: str
    description: str
    parameters: tuple[SchemaParameter, ...]

    @property
    def required(self) -> list[str]:
        return [parameter.name for parameter in self.parameters if parameter.required]


@dataclass(frozen=True, slots=True)
class Usage:
    input: int = 0
    output: int = 0


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    model_requested: str
    model_served: str

    @property
    def model_drifted(self) -> bool:
        """True when the provider served something other than the pin.

        Recorded rather than raised: an alias resolving to a dated version is
        ordinary provider behaviour, and FR-22a's requirement is that the
        matrix say which one ran.
        """
        return self.model_served != self.model_requested


class Provider(Protocol):
    """The whole interface. Async because the suite runner is `asyncio`
    (OD-1 addendum) and a synchronous provider would serialise a run that
    NFR-2 needs to saturate whatever concurrency the operator's tiers allow."""

    name: str

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        model: str,
        max_tokens: int,
    ) -> Completion: ...


def parse_tool_call(*, id: str, name: str, raw: Any, provider_state: Any = None) -> ToolCall:
    """Build a `ToolCall`, turning a parse failure into a recordable one.

    Every adapter goes through here rather than calling `json_arguments`
    directly, so that "the model produced malformed arguments" has one
    behaviour across three SDKs instead of three.

    `provider_state` is carried on **both** branches deliberately. Gemini
    rejects a conversation whose `functionCall` parts have lost their
    `thought_signature`, and it does not make an exception for the call whose
    arguments the model malformed — so a parse error that dropped the state
    would turn one recordable bad call into a dead session.
    """
    try:
        return ToolCall(
            id=id,
            name=name,
            arguments=json_arguments(raw, tool=name),
            provider_state=provider_state,
        )
    except ProviderError as exc:
        return ToolCall(
            id=id, name=name, arguments={}, parse_error=str(exc), provider_state=provider_state
        )


def json_arguments(raw: Any, *, tool: str) -> dict[str, Any]:
    """Parse whatever an SDK put in the arguments slot into a dict.

    Shared by the adapters because the failure it prevents is the same in all
    three: a model producing malformed arguments is ordinary behaviour to be
    recorded and fed back, not a defect in the lab, so this raises a
    `ProviderError` the loop turns into a tool-call error rather than letting a
    `json.JSONDecodeError` escape from inside an SDK.
    """
    import json

    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str | bytes):
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"{tool} arguments were not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ProviderError(
                f"{tool} arguments parsed to {type(parsed).__name__}, not an object"
            )
        return parsed
    raise ProviderError(f"{tool} arguments arrived as {type(raw).__name__}")


__all__ = [
    "FAILURE_OTHER",
    "FAILURE_RATE_LIMITED",
    "Completion",
    "Message",
    "Provider",
    "ProviderError",
    "Role",
    "SchemaParameter",
    "ToolCall",
    "ToolSchema",
    "Usage",
    "classify_failure",
    "json_arguments",
    "parse_tool_call",
]
