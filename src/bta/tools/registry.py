"""Tool declarations, as data.

A `ToolSpec` is what the agent loop will render into a provider's tool schema
in phase 3, and what the boundary needs in order to audit a call. It exists in
phase 2 because the boundary cannot be written without it, not because the loop
is being started early.

**Descriptions are templates.** C1 is planted in a tool description
(`schemas.md` §3, `planted_via: tool_description`) and is the control flag: it
must leak at all three levels, and a `held` verdict on it means the fixture or
the verifier is broken (SC-5). But §3 also says no tool may reach the fixture
manifest. So the value cannot be read here, and the description carries a
`{marker}` placeholder that deployment configuration fills at construction
time. Where that value comes from on the wire is a phase 3 decision and is
called out in the PR — what phase 2 owes it is a description that can hold one
without a tool going looking.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from bta.models import Agent, Surface, require_agent

from .context import ToolContext, ToolResult

ToolFunction = Callable[..., ToolResult]


#: How a Python annotation becomes a JSON schema type. Closed: an annotation
#: outside this set is a `ToolDefinitionError` rather than a silent "string",
#: because a wrong type here is not a schema nicety — see `ParameterSpec`.
_JSON_TYPES: dict[type, str] = {str: "string", bool: "boolean", int: "integer", float: "number"}


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """One tool parameter, as a provider needs to see it.

    Phase 3 originally rendered every parameter as `{"type": "string"}` and
    listed all of them in `required`, which is wrong in two ways that both
    change what the matrix measures.

    **A required boolean becomes a truthy string.** `search_deploys` takes
    `include_artefact: bool = False`. A model obeying `required` sends
    `"false"`, and `if include_artefact:` is true for any non-empty string — so
    the artefact body was read on *every* call. That is F2's Cloud Storage
    carrier firing unprompted, at L1 and L2, regardless of what the model
    asked for.

    **Optional filters become mandatory ones.** A compliant model would filter
    `search_deploys` on `service`, `status` and `deploy_id` at once and match
    nothing, so Deploy Bot would return zero rows for reasons unrelated to
    authorisation — confounding the F1 and F2 rows.

    Both were found by review, not by a test: `tests/test_providers.py` used a
    single-required-string tool, so no adapter was ever asked to render an
    optional or a non-string.
    """

    name: str
    json_type: str
    required: bool


class ToolDefinitionError(TypeError):
    """A tool that does not meet the boundary's expectations. Raised at import
    time — a malformed tool should never survive to a run."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    agent: Agent
    description: str
    surfaces: tuple[Surface, ...]
    fn: ToolFunction
    parameters: tuple[ParameterSpec, ...]
    redacted_parameters: frozenset[str] = field(default_factory=frozenset)

    @property
    def parameter_names(self) -> tuple[str, ...]:
        return tuple(parameter.name for parameter in self.parameters)

    def render_description(self, marker: str | None = None) -> str:
        """`marker` is C1's value, supplied by deployment configuration.

        Absent one, the placeholder is dropped and the description reads
        normally — which is what the offline tests and the local harness see.
        A deployment that renders descriptions with no marker has silently
        removed the control flag, so the site asserts on it rather than
        allowing the default through (phase 3).
        """
        return self.description.format(marker=marker or "").rstrip()

    def audited_parameters(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Parameters as they will be written down (`schemas.md` §6).

        `redacted_parameters` is empty for all eight tools, and that is a
        property worth keeping rather than a gap: no tool in this lab accepts
        credential material as an argument. At L1 the Onboarding agent holds
        the integration key in its system prompt, and if a tool took a key
        parameter the model could hand F4 straight into an audit record, which
        VER-5 and `schemas.md` §6 both forbid. The mechanism exists so that
        adding such a parameter is a visible act with a test to answer to.
        """
        from bta.audit import REDACTED

        return {
            name: (REDACTED if name in self.redacted_parameters else value)
            for name, value in arguments.items()
        }


class ToolRegistry:
    """Name to spec, with the agent split kept explicit.

    Registration is by decorator at import time, so the set of tools is the set
    of functions that said so — there is no scan of a module namespace that
    could pick up a helper.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ToolDefinitionError(f"two tools named {spec.name!r}")
        self._specs[spec.name] = spec

    def __getitem__(self, name: str) -> ToolSpec:
        return self._specs[name]

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(sorted(self._specs.values(), key=lambda spec: spec.name))

    def __len__(self) -> int:
        return len(self._specs)

    def for_agent(self, agent: Agent) -> tuple[ToolSpec, ...]:
        """The tools one agent may call (FR-9, FR-10).

        `require_agent` first, because the selection below is by identity and a
        plain string would match nothing — returning an empty tuple rather than
        raising, which is the failure described in `bta.models.require_agent`.
        """
        require_agent(agent)
        return tuple(spec for spec in self if spec.agent is agent or spec.agent is Agent.BOTH)


REGISTRY = ToolRegistry()


def tool(
    *,
    name: str,
    agent: Agent,
    description: str,
    surfaces: tuple[Surface, ...] = (),
    redacted_parameters: frozenset[str] = frozenset(),
    registry: ToolRegistry = REGISTRY,
) -> Callable[[ToolFunction], ToolFunction]:
    """Declare a plain function as a tool (FR-11, POR-3).

    The decorator registers and returns the function untouched. It wraps
    nothing: `bta.tools` imports nothing from `bta.agent`, and a decorator that
    added behaviour would be the framework creeping in through the door POR-3
    holds shut. The behaviour lives in `boundary.invoke`, which is called *by*
    the loop rather than *from* the tool.
    """

    def decorate(fn: ToolFunction) -> ToolFunction:
        parameters = _check_signature(fn, name)
        unknown = redacted_parameters - {parameter.name for parameter in parameters}
        if unknown:
            raise ToolDefinitionError(
                f"{name} redacts parameters it does not take: {sorted(unknown)}"
            )
        registry.register(
            ToolSpec(
                name=name,
                agent=agent,
                description=inspect.cleandoc(description),
                surfaces=surfaces,
                fn=fn,
                parameters=parameters,
                redacted_parameters=redacted_parameters,
            )
        )
        return fn

    return decorate


def _check_signature(fn: ToolFunction, name: str) -> tuple[ParameterSpec, ...]:
    """Context positional and first; everything else keyword-only.

    Enforced rather than conventional. The keyword-only rule is what lets the
    boundary record parameters by name in an audit record without inferring
    names from position, and the positional-context rule is the seam described
    in `context.py`.
    """
    # `eval_str=True` because every tool module uses `from __future__ import
    # annotations`, so without it each annotation arrives as the *string*
    # "str | None" and every parameter would resolve to the same unknown type.
    signature = inspect.signature(fn, eval_str=True)
    parameters = list(signature.parameters.values())

    if not parameters:
        raise ToolDefinitionError(f"{name} takes no context argument")

    first, *rest = parameters
    if first.name != "ctx" or first.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
        raise ToolDefinitionError(
            f"{name} must take the tool context as its first positional argument, named `ctx`"
        )
    if first.annotation not in (ToolContext, "ToolContext"):
        raise ToolDefinitionError(f"{name}'s first argument must be annotated `ToolContext`")

    for parameter in rest:
        if parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
            raise ToolDefinitionError(
                f"{name}'s parameter `{parameter.name}` must be keyword-only, so that an "
                f"audit record can name it (schemas.md §6)"
            )

    return tuple(_parameter_spec(parameter, name) for parameter in rest)


def _parameter_spec(parameter: inspect.Parameter, tool: str) -> ParameterSpec:
    """Annotation and default in, JSON schema facts out.

    `required` is "has no default", which is the same rule the function body
    already lives by. Optional-ness is not read from `| None`: a parameter may
    legitimately be a required nullable, and a default is the unambiguous
    signal.
    """
    annotation = parameter.annotation
    if annotation is inspect.Parameter.empty:
        raise ToolDefinitionError(
            f"{tool}'s parameter `{parameter.name}` has no annotation, so no "
            f"provider can be told its type"
        )

    # `str | None` is a union; take the one non-None member. A union of two
    # real types is refused rather than guessed at.
    concrete = [
        member
        for member in getattr(annotation, "__args__", (annotation,))
        if member is not type(None)
    ]
    if len(concrete) != 1 or concrete[0] not in _JSON_TYPES:
        raise ToolDefinitionError(
            f"{tool}'s parameter `{parameter.name}` is annotated {annotation!r}, "
            f"which has no unambiguous JSON schema type. Supported: "
            f"{sorted(kind.__name__ for kind in _JSON_TYPES)}."
        )

    return ParameterSpec(
        name=parameter.name,
        json_type=_JSON_TYPES[concrete[0]],
        required=parameter.default is inspect.Parameter.empty,
    )


__all__ = [
    "REGISTRY",
    "ParameterSpec",
    "ToolDefinitionError",
    "ToolRegistry",
    "ToolSpec",
    "tool",
]
