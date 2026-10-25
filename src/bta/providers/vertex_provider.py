"""Google, via Vertex AI — FR-22, SEC-9.

**No API key.** Vertex authenticates by IAM in the deployment project, which is
why the lab holds provider key material for two vendors rather than three. That
is the stronger form of the same argument the lab makes about agents and
secrets, applied to its own plumbing, and it is the reason FR-22 names Vertex
rather than the Gemini developer API.

Project and location are deployment inputs (POR-6). There is no default: a
default region is how a repository becomes single-tenant, and OD-8 requires
regional model availability to be confirmed before a model is pinned.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import Completion, Message, Role, ToolCall, ToolSchema, Usage, parse_tool_call


class VertexProvider:
    name = "google"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        location: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            from google import genai

            self._client = genai.Client(vertexai=True, project=project_id, location=location)

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        model: str,
        max_tokens: int,
    ) -> Completion:
        response = await self._client.aio.models.generate_content(
            model=model,
            contents=_contents(messages),
            config={
                "system_instruction": system,
                "max_output_tokens": max_tokens,
                "tools": [{"function_declarations": [_tool(tool) for tool in tools]}],
            },
        )

        text: list[str] = []
        calls: list[ToolCall] = []
        for index, part in enumerate(_parts(response)):
            if getattr(part, "text", None):
                text.append(part.text)
            call = getattr(part, "function_call", None)
            if call is not None:
                calls.append(
                    parse_tool_call(
                        # Vertex does not issue a call id. One is synthesised so
                        # that a tool result can be matched back to its call on
                        # the same terms as the other two providers — and so an
                        # audit record's `id` is never provider-shaped.
                        id=getattr(call, "id", None) or f"vertex-{index}",
                        name=call.name,
                        raw=call.args,
                        # Gemini returns a `thought_signature` on the part and
                        # requires it echoed on the next request; without it the
                        # API rejects the whole conversation with a 400 rather
                        # than degrading. Read from the *part*, not the
                        # function call — it is a property of the part that
                        # carried the call. `None` when the model did not send
                        # one, which is normal for non-thinking models.
                        provider_state=getattr(part, "thought_signature", None),
                    )
                )

        usage = getattr(response, "usage_metadata", None)
        return Completion(
            text="".join(text),
            tool_calls=tuple(calls),
            usage=Usage(
                input=getattr(usage, "prompt_token_count", 0) or 0,
                output=getattr(usage, "candidates_token_count", 0) or 0,
            ),
            model_requested=model,
            model_served=getattr(response, "model_version", None) or model,
        )


def _parts(response: Any) -> list[Any]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    return list(getattr(candidates[0].content, "parts", None) or [])


def _tool(tool: ToolSchema) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": {
            "type": "OBJECT",
            "properties": {
                parameter.name: {"type": parameter.json_type.upper()}
                for parameter in tool.parameters
            },
            "required": tool.required,
        },
    }


def _contents(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Gemini counts parts, not messages.

    **A turn's function responses must arrive as one `Content` whose part
    count equals the model's function-call count**, or the request is rejected
    with *"Please ensure that the number of function response parts is equal to
    the number of function call parts of the function call turn."* — a 400 that
    names neither the tool nor the turn.

    `bta.agent.loop` appends one `Message(role=TOOL)` per call, on every path
    including the ones where nothing was invoked, so parity holds upstream and
    was broken here: one `Content` per message meant a model turn with three
    calls was answered by three separate user turns of one part each. It fires
    only when the model calls more than one tool at once, which is why it read
    as intermittent — six executions in one two-minute window of the second
    live L1 run, each lost with its evidence.
    """
    contents: list[dict[str, Any]] = []
    for message in messages:
        rendered = _content(message)
        if (
            message.role is Role.TOOL
            and contents
            and contents[-1].get("role") == "user"
            and all("function_response" in part for part in contents[-1]["parts"])
        ):
            contents[-1]["parts"].extend(rendered["parts"])
            continue
        contents.append(rendered)
    return contents


def _content(message: Message) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {
            "role": "user",
            "parts": [
                {
                    "function_response": {
                        "name": message.tool_name or "",
                        "response": {"result": message.content},
                    }
                }
            ],
        }
    if message.tool_calls:
        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"text": message.content})
        parts.extend(_function_call_part(call) for call in message.tool_calls)
        return {"role": "model", "parts": parts}
    role = "model" if message.role is Role.ASSISTANT else "user"
    return {"role": role, "parts": [{"text": message.content}]}


def _function_call_part(call: ToolCall) -> dict[str, Any]:
    """One `functionCall` part, with its thought signature back where Gemini
    put it.

    The signature travels on `ToolCall.provider_state` — opaque to everything
    except this adapter (see the field's note). Omitted entirely when absent
    rather than sent as `None`, because a null there is a malformed part and
    not the same thing as a model that never issued one.
    """
    part: dict[str, Any] = {"function_call": {"name": call.name, "args": call.arguments}}
    if call.provider_state is not None:
        part["thought_signature"] = call.provider_state
    return part


__all__ = ["VertexProvider"]
