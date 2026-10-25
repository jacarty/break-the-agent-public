"""Anthropic, via Vertex AI — FR-22, SEC-9.

**No API key.** Claude is served as a Vertex partner model in the deployment
project, authenticated by IAM, on the same terms as Gemini in
`vertex_provider`. The lab therefore holds no provider key material at all,
which is the strongest form of the argument it makes about agents and secrets:
a lab saying agents should not hold secrets, holding none itself.

`AsyncAnthropicVertex` is the vendor SDK's own Vertex client, so the request and
response shapes below are unchanged from the direct API — only the transport and
the credential differ.

**Location is separate from the deployment region.** Partner models are served
in fewer regions than Gemini, and fewer than Cloud Run. `BTA_VERTEX_LOCATION`
is the default; `BTA_ANTHROPIC_VERTEX_LOCATION` overrides it where Claude is not
served alongside the Google model. OD-8 requires confirming availability before
a model is pinned, and this is the knob that confirmation feeds.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from . import Completion, Message, ProviderError, Role, ToolCall, ToolSchema, Usage, parse_tool_call

PROJECT_VARIABLE = "BTA_VERTEX_PROJECT"
LOCATION_VARIABLE = "BTA_ANTHROPIC_VERTEX_LOCATION"
LOCATION_FALLBACK = "BTA_VERTEX_LOCATION"


class AnthropicProvider:
    """Nothing above `bta.providers` reaches the client — `bta.agent` holds a
    `Provider`. There is no longer a key to hold: the credential is the
    runtime service account's, resolved by the SDK from the environment."""

    name = "anthropic"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client if client is not None else _client()

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        model: str,
        max_tokens: int,
    ) -> Completion:
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[_message(message) for message in messages],
            tools=[_tool(tool) for tool in tools],
        )

        text: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            kind = getattr(block, "type", None)
            if kind == "text":
                text.append(block.text)
            elif kind == "tool_use":
                calls.append(parse_tool_call(id=block.id, name=block.name, raw=block.input))

        usage = getattr(response, "usage", None)
        return Completion(
            text="".join(text),
            tool_calls=tuple(calls),
            usage=Usage(
                input=getattr(usage, "input_tokens", 0) or 0,
                output=getattr(usage, "output_tokens", 0) or 0,
            ),
            model_requested=model,
            model_served=getattr(response, "model", model) or model,
        )


def _client() -> Any:
    project = os.environ.get(PROJECT_VARIABLE, "").strip()
    location = (
        os.environ.get(LOCATION_VARIABLE, "").strip()
        or os.environ.get(LOCATION_FALLBACK, "").strip()
    )
    if not project or not location:
        raise ProviderError(
            f"{PROJECT_VARIABLE} and {LOCATION_VARIABLE} (or "
            f"{LOCATION_FALLBACK}) must be set. Claude is served through Vertex "
            f"in the deployment project (FR-22); there is no API key and no "
            f"default region, because a default region is how a repository "
            f"becomes single-tenant (POR-6)."
        )
    from anthropic import AsyncAnthropicVertex

    return AsyncAnthropicVertex(project_id=project, region=location)


def _tool(tool: ToolSchema) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": {
            "type": "object",
            "properties": {
                parameter.name: {"type": parameter.json_type} for parameter in tool.parameters
            },
            "required": tool.required,
        },
    }


def _message(message: Message) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
            ],
        }
    if message.tool_calls:
        content: list[dict[str, Any]] = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        content.extend(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            for call in message.tool_calls
        )
        return {"role": "assistant", "content": content}
    return {"role": message.role.value, "content": message.content}


__all__ = ["LOCATION_FALLBACK", "LOCATION_VARIABLE", "PROJECT_VARIABLE", "AnthropicProvider"]
