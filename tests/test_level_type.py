"""`Level` is a `StrEnum`, and that is one type slip from removing every L3 control.

`"L3" == Level.L3` is true; `"L3" is Level.L3` is false. The credential policy
looks the level up in a dict, so a string resolves correctly and the audit
record reads `credential_mode: federated`. The client dispatch in `clients.py`
compares with `is`, so the same string takes the L1/L2 branch and hands back
the server-SDK Firestore client, the permissive write path, and the tester that
quotes the integration key into a failure.

Correct-looking data, a correct-looking audit trail, and no control — spike 0.3
finding 2 reached by a type slip rather than by a wiring mistake. A string is
exactly how a level arrives from a request parameter or a config file, which is
what phases 3 and 4 will be handing to these objects.
"""

from __future__ import annotations

import pytest
from fakes import FakeMinter, persona
from test_estate_clients import CONFIG

from bta.credentials import CredentialFactory, mode_for
from bta.models import Agent, CredentialMode, Level, Surface
from bta.tools import ToolContext
from bta.tools.clients import EstateClients, FirestoreRestDocuments


@pytest.fixture
def clients() -> EstateClients:
    return EstateClients(CONFIG, CredentialFactory(FakeMinter()), transport=None)


def test_a_string_level_is_refused_by_the_context(clients) -> None:
    with pytest.raises(TypeError, match="must be a Level"):
        ToolContext(
            caller=persona(),
            level="L3",  # type: ignore[arg-type]
            agent=Agent.DEPLOY_BOT,
            session_id="S-test",
            clients=clients,
        )


def test_a_string_level_is_refused_by_the_client_provider(clients) -> None:
    """Checked here as well as on the context because this is reachable without
    one — `bta.tools.harness` and any future caller construct it directly."""
    with pytest.raises(TypeError, match="must be a Level"):
        clients.for_surface(Surface.FIRESTORE, persona(), "L3")  # type: ignore[arg-type]


def test_the_enum_still_gets_the_l3_client(clients) -> None:
    """The positive control. A guard that refused everything would pass the two
    tests above while breaking the lab."""
    reader = clients.for_surface(Surface.FIRESTORE, persona(), Level.L3)

    assert isinstance(reader, FirestoreRestDocuments)


@pytest.mark.parametrize("level", list(Level))
def test_every_level_member_is_accepted(level: Level, clients) -> None:
    assert clients.for_surface(Surface.BIGQUERY, persona(), level) is not None


def test_a_string_level_is_refused_by_the_credential_policy() -> None:
    """The third guarded entry point, and the one whose absence was subtlest.

    `POLICY` is a dict, so a string level resolves by hash and returns the
    right mode. Both L3 invariants — the policy's and the factory's — compare
    with `is`, so a string takes them both out at once. They were written as
    belt and braces that "fail differently"; a shared blind spot is the one
    thing that arrangement is supposed not to have.
    """
    with pytest.raises(TypeError, match="must be a Level"):
        mode_for("L3", Surface.BIGQUERY)  # type: ignore[arg-type]


def test_the_policy_still_resolves_every_real_level() -> None:
    """Positive control for the guard above."""
    assert {mode_for(level, Surface.BIGQUERY) for level in Level} == {
        CredentialMode.SHARED,
        CredentialMode.FEDERATED,
    }
