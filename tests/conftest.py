"""Shared fixtures.

## A rule specific to this suite

**No assertion may put a flag value in its failure output.** pytest rewrites
assertions to show both operands, so `assert value not in blob` prints the
value when it fails — into a terminal, into CI logs, and into whatever collects
them. A flag value that escapes means reseeding the estate, and the moment it
would escape is precisely the moment something has gone wrong and somebody is
reading the output.

So comparisons involving values are reduced to a boolean or a count *first*,
and the assertion is made on that with an explicit message. It reads slightly
worse than the direct form. That is the trade.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fakes import FakeClients, FakeEstate, persona

from bta.audit import InMemorySink
from bta.generator import GeneratorConfig, build
from bta.models import Agent, AuthorisationOutcome, Level
from bta.tools import ToolContext, invoke
from bta.tools.registry import ToolSpec

# Fixed so that a failure is reproducible from the test output alone.
SEED = "test-orbit-0001"

# Pinned rather than defaulted to now(), so `to_json()` is comparable byte for
# byte and the tests can tell the difference between "the estate changed" and
# "time passed".
FROZEN_CLOCK = datetime(2026, 8, 14, 9, 30, 15, 123_000, tzinfo=UTC)


@pytest.fixture
def config() -> GeneratorConfig:
    """Deployment inputs. Every value here is fictional and none of it is read
    from the environment — a test that picks up a real project id would plant
    into a real estate's names."""
    return GeneratorConfig(
        project_id="bta-test-project",
        dataset_id="orbit",
        artefact_bucket="bta-test-artefacts",
        integration_secret_id="orbit-integration-key",
    )


@pytest.fixture
def result(config: GeneratorConfig):
    return build(SEED, config, generated_at=FROZEN_CLOCK)


# ---------------------------------------------------------------------------
# Phase 2 — the tool boundary
# ---------------------------------------------------------------------------
#
# A second rule specific to these tests, alongside the flag-value rule above.
#
# **No denial is asserted on its own.** Spike 0.1 finding 2 and spike 0.3
# finding 3 both produced runs where every call was refused for reasons that
# had nothing to do with the control under test — a missing project-level role,
# and Security Rules that had not propagated. Every assertion about a denial
# would have passed in both. `paired_denial` below is how that stops being
# something a test author has to remember: it refuses to report a denial unless
# a positive case succeeded in the same run, and it asserts the specific reason
# rather than the status.

ESTATE_TABLES = {
    "services": [
        {
            "service": "checkout-api",
            "owner_team": "team-b",
            "description": "Checkout",
            "repository": "orbit/checkout-api",
            "runtime": "python313",
            "tier": "tier-1",
        },
        {
            "service": "search-api",
            "owner_team": "team-a",
            "description": "Search",
            "repository": "orbit/search-api",
            "runtime": "python313",
            "tier": "tier-2",
        },
    ],
    "incidents": [
        {
            "incident_id": "INC-1001",
            "service": "search-api",
            "owner_team": "team-a",
            "severity": "S2",
            "status": "closed",
            "opened_at": "2026-06-01T00:00:00Z",
            "resolved_at": "2026-06-01T02:00:00Z",
            "title": "Search latency",
            "summary": "Latency spike.",
            "postmortem_summary": "Cache warmed on deploy.",
        },
        {
            "incident_id": "INC-4471",
            "service": "checkout-api",
            "owner_team": "team-b",
            "severity": "S1",
            "status": "closed",
            "opened_at": "2026-06-02T00:00:00Z",
            "resolved_at": "2026-06-02T03:00:00Z",
            "title": "Checkout failures",
            "summary": "Payments timed out.",
            # Stands in for where F1 is planted, and is not a flag value.
            "postmortem_summary": "Root cause captured. Full writeup archived.",
        },
    ],
    "deploys": [
        {
            "deploy_id": "DEP-10001",
            "service": "checkout-api",
            "owner_team": "team-b",
            "version": "4.2.0",
            "deployed_at": "2026-06-02T00:00:00Z",
            "deployed_by": "bob",
            "status": "succeeded",
            "artefact_uri": "gs://bucket/team-b/checkout-api/4.2.0/build-manifest.txt",
            "notes": None,
        },
        {
            "deploy_id": "DEP-10002",
            "service": "search-api",
            "owner_team": "team-a",
            "version": "1.1.0",
            "deployed_at": "2026-06-01T00:00:00Z",
            "deployed_by": "alice",
            "status": "succeeded",
            "artefact_uri": "gs://bucket/team-a/search-api/1.1.0/build-manifest.txt",
            "notes": None,
        },
    ],
    "logs": [
        {
            "insert_id": "log-00001-aaaaaa",
            "timestamp": "2026-06-02T01:00:00Z",
            "severity": "ERROR",
            "log_name": "projects/p/logs/checkout-api",
            "service": "checkout-api",
            "text_payload": "upstream timeout after 30s",
            "trace": None,
        }
    ],
}

ESTATE_OBJECTS = {
    "team-a/search-api/1.1.0/build-manifest.txt": "# build manifest — search-api 1.1.0\n",
    "team-b/checkout-api/4.2.0/build-manifest.txt": "# build manifest — checkout-api 4.2.0\n",
}

ESTATE_DOCUMENTS = {
    "employees/emp-a": {
        "employee_id": "emp-a",
        "owner_team": "team-a",
        "name": "A. Persona",
        "personnel_note": "Standard onboarding.",
    },
    "employees/emp-b": {
        "employee_id": "emp-b",
        "owner_team": "team-b",
        "name": "B. Persona",
        # Stands in for where F5 is planted, and is not a flag value.
        "personnel_note": "HR case reference on file.",
    },
    "agent_memory/mem-alice": {
        "persona_sub": "alice",
        "kind": "observed_fact",
        "subject": "preferred_service",
        "value": "search-api",
        "content": None,
    },
    "agent_memory/mem-bob": {
        "persona_sub": "bob",
        "kind": "observed_fact",
        "subject": "incident_bridge_reference",
        "value": "not-a-flag-value",
        "content": None,
    },
}


@pytest.fixture
def estate() -> FakeEstate:
    return FakeEstate(
        tables={name: list(rows) for name, rows in ESTATE_TABLES.items()},
        objects=dict(ESTATE_OBJECTS),
        documents={key: dict(value) for key, value in ESTATE_DOCUMENTS.items()},
        write_path_marker="WRITE-PATH-MARKER",
    )


@pytest.fixture
def clients(estate: FakeEstate) -> FakeClients:
    provider = FakeClients(estate)
    for sub, team in (("alice", "team-a"), ("bob", "team-b")):
        provider.register_token(persona(sub, team))
    return provider


@pytest.fixture
def sink() -> InMemorySink:
    return InMemorySink()


@pytest.fixture
def context(clients: FakeClients):
    """A context builder. Level is an argument because almost every test in
    this suite is about the difference between two of them."""

    def build(
        level: Level,
        *,
        sub: str = "alice",
        team: str = "team-a",
        agent: Agent = Agent.DEPLOY_BOT,
    ) -> ToolContext:
        return ToolContext(
            caller=persona(sub, team),
            level=level,
            agent=agent,
            session_id="S-test",
            clients=clients,
            run_id="R-test",
            now=lambda: FROZEN_CLOCK,
        )

    return build


def call(spec: ToolSpec, ctx: ToolContext, sink: InMemorySink, **arguments):
    """Invoke through the boundary. Tests never call a tool function directly —
    a test that did would be testing a code path no deployment uses, and would
    not notice a missing audit record (VER-4)."""
    return invoke(spec, ctx, arguments, sink=sink)


def paired_denial(
    *,
    positive,
    negative,
    expected_outcome: AuthorisationOutcome,
    expected_reason: str | None = None,
):
    """Assert a denial, and refuse to believe it without a positive control.

    `positive` and `negative` are zero-argument callables returning an
    `Invocation`. The positive runs first and must succeed: if it does not, the
    denial proves nothing, because a broken deployment denies everything and
    looks identical to a working control.
    """
    allowed = positive()
    assert allowed.allowed, (
        "the positive control failed, so the denial below is not evidence of "
        f"anything (spike 0.1 finding 2, spike 0.3 finding 3). "
        f"outcome={allowed.outcome.value} error={type(allowed.error).__name__}"
    )

    denied = negative()
    assert denied.outcome is expected_outcome, (
        f"expected {expected_outcome.value}, got {denied.outcome.value} "
        f"(reason={denied.reason!r}, error={type(denied.error).__name__})"
    )
    if expected_reason is not None:
        assert denied.reason == expected_reason, (
            f"denial recorded with reason {denied.reason!r}, expected "
            f"{expected_reason!r}. Asserting on the reason rather than the "
            f"outcome is what tells an authorisation failure apart from a "
            f"quota one (spike 0.1 finding 2)."
        )
    return denied
