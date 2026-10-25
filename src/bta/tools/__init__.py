"""The eight tools, the boundary that calls them, and nothing else.

POR-3 and FR-11: tools are plain functions and the framework wraps them. The
import contract in `pyproject.toml` makes that a build failure rather than a
convention — `bta.tools` may not import `bta.agent`, in either the loop's
direction or a helper's.

Importing this package registers all eight (FR-9, FR-10). Registration is by
decorator at import time, so `REGISTRY` holds the functions that declared
themselves and never whatever else happened to be in a module.
"""

from __future__ import annotations

from .boundary import Invocation, UnknownParameter, invoke
from .clients import EstateClients, IntegrationOutcome
from .context import ClientProvider, ToolContext, ToolResult
from .deploy_bot import get_incident, list_services, query_logs, search_deploys
from .errors import Classification, ToolDenied, classify
from .onboarding import check_policy, get_onboarding_status, provision_account, test_integration
from .registry import REGISTRY, ToolRegistry, ToolSpec, tool

#: FR-9 and FR-10, as an assertion rather than a comment. A tool that stops
#: registering — a decorator dropped in a refactor, a module no longer imported
#: — would otherwise show up as an attack case that never fires and a cell that
#: goes `insufficient` for reasons nobody traces back to here.
EXPECTED_TOOL_COUNT = 9

if len(REGISTRY) != EXPECTED_TOOL_COUNT:  # pragma: no cover - import-time guard
    raise RuntimeError(
        f"{len(REGISTRY)} tools registered, expected {EXPECTED_TOOL_COUNT} "
        f"(FR-9 lists five for deploy_bot as amended 2026-08-29, "
        f"FR-10 four for onboarding_assistant)"
    )

__all__ = [
    "REGISTRY",
    "Classification",
    "ClientProvider",
    "EstateClients",
    "IntegrationOutcome",
    "Invocation",
    "ToolContext",
    "ToolDenied",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "UnknownParameter",
    "check_policy",
    "classify",
    "get_incident",
    "get_onboarding_status",
    "invoke",
    "list_services",
    "provision_account",
    "query_logs",
    "search_deploys",
    "test_integration",
    "tool",
]
