"""The claim the entire L3 column rests on.

`infra/estate/identity.tf` states the residual plainly: the process serving an
L3 session can mint the shared estate credential, because the grant is static
and FR-1 puts all three levels behind one Cloud Run service. One service per
level would close it, and was declined on 2026-08-14 because it would turn
FR-13's constant-agent guarantee into something three revisions have to be kept
in agreement about.

What was taken instead is this file. The claim is about code, so the code is
asserted — `build-plan.md` §Phase 2, and the project's own standard that a
control nothing tests is a control nobody has.
"""

from __future__ import annotations

import pytest
from fakes import FakeMinter, LyingMinter, persona

from bta.credentials import (
    UNCREDENTIALED,
    CredentialFactory,
    CredentialPolicyViolation,
    mode_for,
)
from bta.models import CredentialMode, Level, Surface

CREDENTIALED = tuple(surface for surface in Surface if surface not in UNCREDENTIALED)


@pytest.mark.parametrize("surface", CREDENTIALED)
def test_the_factory_never_returns_the_shared_credential_at_l3(surface: Surface) -> None:
    """Every credentialed surface, one persona, one assertion.

    Parametrised over the enum rather than over a list of the surfaces that
    exist today: a surface added later is covered by this test on the commit
    that adds it, which is the only version of this check worth having.
    """
    factory = CredentialFactory(FakeMinter())

    minted = factory.for_surface(surface, persona(), Level.L3)

    assert minted.mode is not CredentialMode.SHARED, (
        f"L3/{surface.value} minted the shared estate credential. That account "
        f"has estate-wide read and is bound to no caller, so every L3 cell "
        f"touching this surface would report a control that never ran."
    )


@pytest.mark.parametrize("surface", CREDENTIALED)
def test_a_minter_that_returns_shared_at_l3_is_refused(surface: Surface) -> None:
    """The failure mode the invariant is actually for.

    `LyingMinter` is what a plausible fallback looks like: a minter that could
    not complete a token exchange and returned an ambient credential instead.
    The policy table would still say `federated`, every tool would still work,
    and the L3 column would fill with `held`.
    """
    factory = CredentialFactory(LyingMinter())

    with pytest.raises(CredentialPolicyViolation):
        factory.for_surface(surface, persona(), Level.L3)


def test_l1_and_l2_use_the_same_credentials_as_each_other() -> None:
    """FR-14 gives L2 the mitigation stack and L1's credentials.

    If these rows ever diverge, the L2 column stops measuring the mitigation
    stack and starts measuring an unregistered credential change — and the
    matrix would attribute the difference to the wrong thing.
    """
    for surface in CREDENTIALED:
        assert mode_for(Level.L1, surface) is mode_for(Level.L2, surface), surface


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        (Surface.BIGQUERY, CredentialMode.FEDERATED),
        (Surface.CLOUD_STORAGE, CredentialMode.FEDERATED),
        (Surface.FIRESTORE, CredentialMode.FEDERATED),
        (Surface.SECRET_MANAGER, CredentialMode.SERVICE),
    ],
)
def test_the_l3_table_is_what_fr14_describes(surface: Surface, expected: CredentialMode) -> None:
    """Written out rather than derived, so that a change to `POLICY` has to
    change a test that names the requirement it came from."""
    assert mode_for(Level.L3, surface) is expected


@pytest.mark.parametrize("level", list(Level))
@pytest.mark.parametrize("surface", sorted(UNCREDENTIALED, key=lambda s: s.value))
def test_uncredentialed_surfaces_refuse_to_mint(level: Level, surface: Surface) -> None:
    """F6 is an outcome and C1 is text in a description. Minting for either
    would mean something had confused a code surface for a cloud one."""
    with pytest.raises(CredentialPolicyViolation):
        mode_for(level, surface)


def test_the_minter_is_asked_for_exactly_the_mode_the_policy_chose() -> None:
    """Guards the seam between the two halves. A factory that resolved
    `federated` and then called `shared()` would pass every test above that
    only inspects the returned mode."""
    minter = FakeMinter()
    factory = CredentialFactory(minter)

    factory.for_surface(Surface.BIGQUERY, persona(), Level.L1)
    factory.for_surface(Surface.BIGQUERY, persona(), Level.L3)
    factory.for_surface(Surface.SECRET_MANAGER, persona(), Level.L3)

    assert minter.calls == [
        CredentialMode.SHARED,
        CredentialMode.FEDERATED,
        CredentialMode.SERVICE,
    ]


def test_the_federated_credential_is_built_for_the_caller_who_asked() -> None:
    """Spike 0.1 finding 3 relocated the trust boundary onto the team claim, so
    a credential minted for one caller and handed to another is the whole of
    L3 undone. `FakeMinter` records the caller on the credential; the real
    minter puts the same caller's token behind the subject token supplier."""
    factory = CredentialFactory(FakeMinter())

    alice = factory.for_surface(Surface.BIGQUERY, persona("alice", "team-a"), Level.L3)
    bob = factory.for_surface(Surface.BIGQUERY, persona("bob", "team-b"), Level.L3)

    assert alice.principal.endswith("team-a")
    assert bob.principal.endswith("team-b")
