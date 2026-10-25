"""xAI Grok, via Vertex AI's OpenAI-compatible endpoint — FR-22, SEC-9.

**Still the OpenAI SDK, pointed somewhere else.** Vertex serves partner models
through an OpenAI-compatible Chat Completions path, so the request and response
shapes below are unchanged from when this adapter talked to OpenAI directly —
what changed is `base_url` and where the credential comes from. That is also
why this file kept its shape through the GCP-only migration rather than being
rewritten: the wire format is the same, the vendor is not.

**The credential is an OAuth token, not an API key.** `api_key` on the client is
the OpenAI SDK's name for the bearer token, and what goes in it is the runtime
service account's access token. It expires, unlike a key, so `complete` refreshes
before each call — an adapter that read the token once would work for an hour and
then fail in a way that looks like an auth misconfiguration rather than an expiry.

This is also the adapter where the arguments-shape trap lives: this wire format
returns tool call arguments as a **JSON string**, and the other two return a
mapping. `json_arguments` is what makes the three agree.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from . import Completion, Message, ProviderError, Role, ToolSchema, Usage, parse_tool_call

PROJECT_VARIABLE = "BTA_VERTEX_PROJECT"
LOCATION_VARIABLE = "BTA_XAI_VERTEX_LOCATION"
LOCATION_FALLBACK = "BTA_VERTEX_LOCATION"
SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


class XAIProvider:
    name = "xai"

    def __init__(self, client: Any | None = None, credentials: Any | None = None) -> None:
        self._credentials = credentials
        if client is not None:
            self._client = client
        else:
            self._client, self._credentials = _client()

    def _refresh(self) -> None:
        """Keep the bearer token current. A no-op when a client was injected,
        which is how the offline tests reach `complete` without a project."""
        if self._credentials is None:
            return
        if not getattr(self._credentials, "valid", False):
            import google.auth.transport.requests

            self._credentials.refresh(google.auth.transport.requests.Request())
        self._client.api_key = self._credentials.token

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        model: str,
        max_tokens: int,
    ) -> Completion:
        self._refresh()
        payload: list[dict[str, Any]] = [{"role": "system", "content": system}]
        payload.extend(_message(message) for message in messages)

        response = await self._client.chat.completions.create(
            model=model,
            max_completion_tokens=max_tokens,
            messages=payload,
            tools=[_tool(tool) for tool in tools],
        )

        choice = response.choices[0].message
        calls = tuple(
            parse_tool_call(id=call.id, name=call.function.name, raw=call.function.arguments)
            for call in (getattr(choice, "tool_calls", None) or ())
        )
        usage = getattr(response, "usage", None)
        return Completion(
            text=choice.content or "",
            tool_calls=calls,
            usage=Usage(
                input=getattr(usage, "prompt_tokens", 0) or 0,
                output=getattr(usage, "completion_tokens", 0) or 0,
            ),
            model_requested=model,
            model_served=getattr(response, "model", model) or model,
        )


def _client() -> tuple[Any, Any]:
    project = os.environ.get(PROJECT_VARIABLE, "").strip()
    location = (
        os.environ.get(LOCATION_VARIABLE, "").strip()
        or os.environ.get(LOCATION_FALLBACK, "").strip()
    )
    if not project or not location:
        raise ProviderError(
            f"{PROJECT_VARIABLE} and {LOCATION_VARIABLE} (or {LOCATION_FALLBACK}) "
            f"must be set. Grok is served as a Vertex partner model in the "
            f"deployment project (FR-22); there is no API key and no default "
            f"region, because a default region is how a repository becomes "
            f"single-tenant (POR-6)."
        )

    import google.auth
    import google.auth.transport.requests
    from openai import AsyncOpenAI

    credentials, _ = google.auth.default(scopes=list(SCOPES))
    credentials.refresh(google.auth.transport.requests.Request())

    return AsyncOpenAI(
        base_url=_base_url(project, location), api_key=credentials.token
    ), credentials


def _base_url(project: str, location: str) -> str:
    """Vertex's global endpoint has no regional host prefix.

    **`global` is a location, not a region, and it is where Grok's quota
    lives.** Every partner-model quota row for xAI on this project is on the
    `Global*GenerateContent*` metrics, with no regional row anywhere — so a
    deployment pinning a region reaches a publisher endpoint that does not
    serve Grok and gets a 404 that reads as *model not found*, pointing the
    reader at the model pin rather than at the location.

    Interpolating `global` into the host produced `global-aiplatform.
    googleapis.com`, which resolves to Google's front end and returns an HTML
    404 — not a JSON API error — so the SDK surfaced it as `NotFoundError`
    with a page of markup attached. Confirmed against the live project: the
    same call on `https://aiplatform.googleapis.com` serves.
    """
    host = "aiplatform" if location == "global" else f"{location}-aiplatform"
    return f"https://{host}.googleapis.com/v1/projects/{project}/locations/{location}/endpoints/openapi"


def _tool(tool: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": {
                    parameter.name: {"type": parameter.json_type} for parameter in tool.parameters
                },
                "required": tool.required,
            },
        },
    }


def _message(message: Message) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    if message.tool_calls:
        import json

        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ],
        }
    return {"role": message.role.value, "content": message.content}


__all__ = ["LOCATION_FALLBACK", "LOCATION_VARIABLE", "PROJECT_VARIABLE", "XAIProvider"]
