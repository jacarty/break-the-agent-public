"""FR-22, POR-2, SEC-9 — one protocol, three SDKs, no key in agent context.

**No test here constructs a real SDK client or makes a network call.** The
adapters are exercised against fakes shaped like each SDK's response. What that
does establish is the normalisation this package exists to do; what it does not
establish is that the SDK call shape is right, which is a claim only a live run
can make. That line is the same one `src/bta/tools/README.md` draws for the
estate clients, and it is drawn here for the same reason.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from bta.providers import (
    Message,
    ProviderError,
    Role,
    SchemaParameter,
    ToolCall,
    ToolSchema,
    json_arguments,
)
from bta.providers.anthropic_provider import AnthropicProvider
from bta.providers.vertex_provider import VertexProvider
from bta.providers.xai_provider import XAIProvider

#: A required string *and* an optional boolean, deliberately.
#:
#: The original fixture had one required string, which is why no test noticed
#: that all three adapters declared every parameter required and string-typed —
#: `search_deploys`'s `include_artefact: bool = False` then arrived as the
#: string "false", which is truthy, and F2's carrier fired on every call.
TOOLS = (
    ToolSchema(
        name="get_incident",
        description="Read an incident.",
        parameters=(
            SchemaParameter(name="incident_id", json_type="string", required=True),
            SchemaParameter(name="include_artefact", json_type="boolean", required=False),
        ),
    ),
)
SENTINEL_KEY = "sk-test-do-not-use-0000000000"


# ---------------------------------------------------------------------------
# The shape that fails quietly
# ---------------------------------------------------------------------------


def test_a_tool_call_refuses_string_arguments():
    """The OpenAI wire format returns arguments as a JSON string and the other two return a
    mapping. A string passed on would reach `boundary.invoke` as one positional
    value — see `bta.providers`."""
    with pytest.raises(ProviderError, match="not a dict"):
        ToolCall(id="1", name="get_incident", arguments='{"incident_id": "INC-1"}')


def test_json_arguments_parses_the_openai_wire_shape():
    assert json_arguments('{"incident_id": "INC-1"}', tool="get_incident") == {
        "incident_id": "INC-1"
    }
    assert json_arguments({"incident_id": "INC-1"}, tool="get_incident") == {"incident_id": "INC-1"}
    assert json_arguments("", tool="get_incident") == {}


def test_malformed_arguments_raise_a_provider_error_not_a_json_error():
    """A model producing malformed arguments is ordinary behaviour to be
    recorded and fed back, not a defect in the lab — so the loop must see one
    error type rather than an SDK's internals."""
    with pytest.raises(ProviderError):
        json_arguments("{not json", tool="get_incident")
    with pytest.raises(ProviderError):
        json_arguments("[1, 2]", tool="get_incident")


# ---------------------------------------------------------------------------
# Each adapter normalises to the same Completion
# ---------------------------------------------------------------------------


class FakeAnthropic:
    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)
        self.seen = {}

    async def _create(self, **kwargs):
        self.seen = kwargs
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Looking that up."),
                SimpleNamespace(
                    type="tool_use", id="tu-1", name="get_incident", input={"incident_id": "INC-1"}
                ),
            ],
            usage=SimpleNamespace(input_tokens=11, output_tokens=5),
            model="anthropic-model-dated",
        )


class FakeXAI:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.seen = {}

    async def _create(self, **kwargs):
        self.seen = kwargs
        call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(
                name="get_incident", arguments=json.dumps({"incident_id": "INC-1"})
            ),
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Looking that up.", tool_calls=[call])
                )
            ],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5),
            model="xai-model-dated",
        )


class FakeVertex:
    def __init__(self):
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._create))
        self.seen = {}

    async def _create(self, **kwargs):
        self.seen = kwargs
        parts = [
            SimpleNamespace(text="Looking that up.", function_call=None),
            SimpleNamespace(
                text=None,
                function_call=SimpleNamespace(name="get_incident", args={"incident_id": "INC-1"}),
                # Gemini attaches this to the *part*, and requires it echoed on
                # the next request or it rejects the conversation outright.
                thought_signature="sig-abc",
            ),
        ]
        return SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))],
            usage_metadata=SimpleNamespace(prompt_token_count=11, candidates_token_count=5),
            model_version="google-model-dated",
        )


PROVIDERS = {
    "anthropic": (AnthropicProvider, FakeAnthropic),
    "xai": (XAIProvider, FakeXAI),
    "google": (VertexProvider, FakeVertex),
}


def complete(provider):
    return asyncio.run(
        provider.complete(
            system="You are a test.",
            messages=[Message(role=Role.USER, content="What happened in INC-1?")],
            tools=TOOLS,
            model="pinned-model",
            max_tokens=256,
        )
    )


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_every_adapter_normalises_to_the_same_completion(name):
    cls, fake_cls = PROVIDERS[name]
    fake = fake_cls()
    provider = cls(client=fake)
    result = complete(provider)

    assert provider.name == name
    assert result.text == "Looking that up."
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.name == "get_incident"
    assert call.arguments == {"incident_id": "INC-1"}
    assert isinstance(call.arguments, dict)
    assert result.usage.input == 11
    assert result.usage.output == 5


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_model_drift_is_recorded_and_not_reconciled(name):
    """FR-22a. An alias resolving to a dated version is ordinary provider
    behaviour, and the matrix has to say which one actually ran."""
    cls, fake_cls = PROVIDERS[name]
    result = complete(cls(client=fake_cls()))
    assert result.model_requested == "pinned-model"
    assert result.model_served.endswith("-dated")
    assert result.model_drifted


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_a_vertex_tool_call_gets_an_id_like_every_other_provider(name):
    """Vertex issues no call id. One is synthesised so a tool result can be
    matched back on the same terms as the other two."""
    cls, fake_cls = PROVIDERS[name]
    result = complete(cls(client=fake_cls()))
    assert result.tool_calls[0].id


# ---------------------------------------------------------------------------
# SEC-9
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_no_key_material_reaches_the_request_payload(name, monkeypatch):
    """SEC-9: credential material is never placed in agent context, never
    reachable by a tool, never returned in an error.

    There are no provider API keys any more (FR-22 amended 2026-08-28), so the
    sentinel is the deployment identifier the adapters *do* read. The property
    is unchanged and still worth pinning: nothing an adapter reads from the
    environment reaches the system prompt, a message, or a tool schema — the
    three things that become a transcript.
    """
    monkeypatch.setenv("BTA_VERTEX_PROJECT", SENTINEL_KEY)

    cls, fake_cls = PROVIDERS[name]
    fake = fake_cls()
    complete(cls(client=fake))

    rendered = json.dumps(fake.seen, default=str)
    assert SENTINEL_KEY not in rendered, (
        "provider key material reached the request payload, which is what a "
        "transcript is built from (SEC-9, VER-5)."
    )


def test_xai_refuses_to_construct_without_a_project_and_location(monkeypatch):
    """No default, and no silent fallback to an unauthenticated client. Grok is
    a Vertex partner model now, so what is missing is a project and a region
    rather than a key."""
    for variable in ("BTA_VERTEX_PROJECT", "BTA_XAI_VERTEX_LOCATION", "BTA_VERTEX_LOCATION"):
        monkeypatch.delenv(variable, raising=False)
    with pytest.raises(ProviderError, match="BTA_VERTEX_PROJECT"):
        XAIProvider()


def test_no_adapter_holds_provider_key_material(monkeypatch):
    """FR-22/SEC-9 as amended: the lab holds a provider API key for *no*
    provider. This is the test that would fail if one came back — and two of
    them were here until this migration."""
    import inspect

    from bta.providers import anthropic_provider, vertex_provider, xai_provider

    for module in (anthropic_provider, vertex_provider, xai_provider):
        source = inspect.getsource(module)
        assert "API_KEY_VARIABLE" not in source, module.__name__
        assert "ANTHROPIC_API_KEY" not in source, module.__name__
        assert "OPENAI_API_KEY" not in source, module.__name__


def test_the_xai_bearer_token_is_refreshed_rather_than_read_once(monkeypatch):
    """The OpenAI SDK calls it `api_key`, but on Vertex it is an OAuth access
    token with an expiry. An adapter that read it once would work for an hour
    and then fail in a way that reads as a misconfigured credential rather than
    an expired one."""

    class Credentials:
        def __init__(self) -> None:
            self.valid = False
            self.token = "first-token"
            self.refreshes = 0

        def refresh(self, _request) -> None:
            self.refreshes += 1
            self.token = f"token-{self.refreshes}"
            self.valid = True

    fake, credentials = FakeXAI(), Credentials()
    provider = XAIProvider(client=fake, credentials=credentials)

    complete(provider)
    assert credentials.refreshes == 1
    assert fake.api_key == "token-1"

    credentials.valid = False  # as an expiry presents
    complete(provider)
    assert credentials.refreshes == 2
    assert fake.api_key == "token-2"

    # And the token is still not in anything a transcript is built from.
    assert "token-" not in json.dumps(fake.seen, default=str)


def test_vertex_needs_no_key_at_all():
    """FR-22: Google inference is IAM-authenticated in the deployment project,
    which is why the lab holds key material for two providers and not three."""
    import inspect

    from bta.providers import vertex_provider

    assert "API_KEY" not in inspect.getsource(vertex_provider).upper().replace(
        "API_KEY_VARIABLE", ""
    )


def test_the_credential_reaches_the_client_and_nothing_else(monkeypatch):
    """SEC-9, through the construction path the deployment actually uses.

    The other SEC-9 test injects a client, so `_client()` never runs. Here the
    real one does, so there is a live credential to leak. What it hands the SDK
    is an OAuth token rather than an API key now, and the assertion is the
    same: it reaches the client and nothing a transcript is built from.
    """
    import openai

    monkeypatch.setenv("BTA_VERTEX_PROJECT", "a-project")
    monkeypatch.setenv("BTA_VERTEX_LOCATION", "a-region")
    received: dict = {}

    class Credentials:
        valid = True
        token = SENTINEL_KEY

        def refresh(self, _request) -> None:
            pass

    monkeypatch.setattr("google.auth.default", lambda scopes=None: (Credentials(), "a-project"))
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: received.update(kw) or FakeXAI())

    provider = XAIProvider()
    assert received.get("api_key") == SENTINEL_KEY, (
        "the credential never reached the client, so this test has nothing to prove"
    )
    assert "aiplatform.googleapis.com" in received.get("base_url", ""), (
        "the client was not pointed at Vertex, so this is not the GCP-only path"
    )

    complete(provider)
    rendered = json.dumps(provider._client.seen, default=str)
    assert SENTINEL_KEY not in rendered, (
        "credential material reached the request payload, which is what a "
        "transcript is built from (SEC-9, VER-5)"
    )


# ---------------------------------------------------------------------------
# Gemini thought signatures — the follow-up call after a tool result
# ---------------------------------------------------------------------------


def test_a_thought_signature_survives_into_the_next_request():
    """Gemini returns a `thought_signature` on a `functionCall` part and
    **requires it echoed back**. Dropping it does not degrade the answer — the
    API rejects the whole conversation with `400 INVALID_ARGUMENT`, on the
    request *after* the tool has already run.

    That is the worst possible moment for it: the tool has disclosed, the
    payload is in the transcript, and the turn dies before the model produces
    the text a capture would also be scored from. Observed against the live
    deployment on 2026-08-29, where every L1 `get_incident` attack errored on
    the follow-up call and scored `not_captured`.
    """
    from bta.providers.vertex_provider import _function_call_part

    result = complete(VertexProvider(client=FakeVertex()))
    call = result.tool_calls[0]
    assert call.provider_state == "sig-abc", "the signature was dropped at parse time"

    part = _function_call_part(call)
    assert part["thought_signature"] == "sig-abc"
    assert part["function_call"]["name"] == "get_incident"


def test_a_call_without_a_signature_omits_the_field_entirely():
    """A null `thought_signature` is a malformed part, which is not the same
    thing as a model that never issued one — non-thinking models do not."""
    from bta.providers import ToolCall
    from bta.providers.vertex_provider import _function_call_part

    part = _function_call_part(ToolCall(id="c1", name="get_incident", arguments={}))
    assert "thought_signature" not in part


def test_a_malformed_call_keeps_its_signature():
    """A parse error must not cost the signature. Gemini makes no exception for
    the call whose arguments the model malformed, so dropping it would turn one
    recordable bad call into a dead session."""
    from bta.providers import parse_tool_call

    call = parse_tool_call(id="c1", name="get_incident", raw="{not json", provider_state="sig-z")
    assert call.parse_error
    assert call.provider_state == "sig-z"


def test_the_global_location_drops_the_regional_host_prefix():
    """`global` is a location, not a region, and every xAI quota row on Vertex
    is global-only — so this string decides whether the lab has a third
    provider at all. `global-aiplatform.googleapis.com` is Google's front end,
    which answers with an HTML 404 the SDK reports as a missing *model*."""
    from bta.providers.xai_provider import _base_url

    assert _base_url("p", "global") == (
        "https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints/openapi"
    )


def test_a_regional_location_keeps_the_prefix():
    from bta.providers.xai_provider import _base_url

    assert _base_url("p", "europe-west1").startswith(
        "https://europe-west1-aiplatform.googleapis.com/v1/projects/p/locations/europe-west1/"
    )


def test_parallel_tool_results_arrive_as_one_content():
    """Gemini counts parts, not messages: a model turn with three function
    calls must be answered by one `Content` carrying three function responses.

    One `Content` per message produced three separate user turns and a 400 —
    *"the number of function response parts is equal to the number of function
    call parts"* — which named neither tool nor turn and killed six executions
    in a two-minute window of the second live L1 run.
    """
    from bta.providers import ToolCall
    from bta.providers.vertex_provider import _contents

    calls = tuple(ToolCall(id=f"c{n}", name=f"t{n}", arguments={}) for n in range(3))
    contents = _contents(
        [
            Message(role=Role.USER, content="do three things"),
            Message(role=Role.ASSISTANT, content="", tool_calls=calls),
            *[
                Message(role=Role.TOOL, content=f"r{n}", tool_call_id=f"c{n}", tool_name=f"t{n}")
                for n in range(3)
            ],
        ]
    )

    assert [c["role"] for c in contents] == ["user", "model", "user"]
    model_calls = [p for p in contents[1]["parts"] if "function_call" in p]
    responses = [p for p in contents[-1]["parts"] if "function_response" in p]
    assert len(responses) == len(model_calls) == 3


def test_a_later_user_turn_does_not_absorb_the_next_tool_results():
    """Only *consecutive* tool messages merge. Folding a fresh user turn into
    the previous response block would reorder the conversation."""
    from bta.providers.vertex_provider import _contents

    contents = _contents(
        [
            Message(role=Role.TOOL, content="r0", tool_call_id="c0", tool_name="t0"),
            Message(role=Role.USER, content="and now this"),
            Message(role=Role.TOOL, content="r1", tool_call_id="c1", tool_name="t1"),
        ]
    )
    assert len(contents) == 3
    assert "text" in contents[1]["parts"][0]
