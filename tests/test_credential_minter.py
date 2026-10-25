"""The real minter, offline.

`GoogleCredentialMinter` is the only implementation that ships, and until now
every test used a fake one. That gap is not the same as the harness gap in
TD-11: constructing a credential performs no I/O — `google.auth` defers every
network call to the moment a credential is *used* — and `source_credentials` is
injectable precisely so the construction can be inspected without one.

What is still not covered here, and cannot be: whether STS accepts the audience,
whether the pool maps the claim, whether the impersonation grants exist. Those
are the estate's answers and the harness's job.
"""

from __future__ import annotations

import pytest
from fakes import AUDIENCE, PROVISIONING_ACCOUNT, SENTINEL_TOKEN, SHARED_ACCOUNT, persona

from bta.credentials import GoogleCredentialMinter
from bta.models import CredentialMode


class StubSourceCredentials:
    """The runtime identity, as much of it as `impersonated_credentials` reads.

    Two attributes, both inspected during construction. Standing one up by hand
    is the price of not calling `google.auth.default()` in a test — which is a
    price worth paying, because a test that picked up an ambient credential
    would be a test whose result said nothing about the code.
    """

    universe_domain = "googleapis.com"
    requires_scopes = False


@pytest.fixture
def minter() -> GoogleCredentialMinter:
    return GoogleCredentialMinter(
        shared_service_account=SHARED_ACCOUNT,
        provisioning_service_account=PROVISIONING_ACCOUNT,
        workload_identity_audience=AUDIENCE,
        # Stands in for the runtime identity the metadata server issues. Passed
        # explicitly so `google.auth.default()` is never called: spike 0.1's
        # method was that a positive result must not be an artefact of an SDK
        # falling back to ambient credentials, and that applies to this test.
        source_credentials=StubSourceCredentials(),
    )


def test_the_three_modes_are_three_different_principals(minter) -> None:
    """The failure this catches is a copy-paste: two modes targeting one
    account. Both would work, both would look right, and the `service` mode
    would silently be the shared estate credential — which is the L3 claim
    inverted."""
    shared = minter.shared()
    service = minter.service()
    federated = minter.federated(persona())

    assert shared.mode is CredentialMode.SHARED
    assert service.mode is CredentialMode.SERVICE
    assert federated.mode is CredentialMode.FEDERATED

    principals = {shared.principal, service.principal, federated.principal}
    assert len(principals) == 3, principals
    assert shared.principal == SHARED_ACCOUNT
    assert service.principal == PROVISIONING_ACCOUNT


def test_the_federated_principal_is_the_team_attribute_not_the_person(minter) -> None:
    """Spike 0.1 finding 3: the grant is on `attribute.team`, so this string is
    what IAM actually evaluates. It must match the form
    `infra/estate/identity.tf` binds — the pool, not the provider.

    A principalSet carrying the `/providers/<id>` segment names nothing IAM has
    ever granted, so recording one would put a principal in a harness report
    that no binding in the estate mentions.
    """
    minted = minter.federated(persona("alice", "team-a"))

    assert minted.principal == (
        "principalSet://iam.googleapis.com/projects/000000000000/locations/global"
        "/workloadIdentityPools/arena-personas/attribute.team/team-a"
    )
    assert "/providers/" not in minted.principal


def test_two_callers_on_different_teams_get_different_principals(minter) -> None:
    alice = minter.federated(persona("alice", "team-a"))
    bob = minter.federated(persona("bob", "team-b"))

    assert alice.principal != bob.principal


def test_the_supplier_returns_the_caller_s_own_token(minter) -> None:
    """The seam FR-14 needs (spike 0.1 finding 7). It is called per credential
    refresh rather than once, so a long-lived process holds no cached caller
    token beyond the credential's own lifetime."""
    caller = persona("alice", "team-a")

    supplier = minter.federated(caller).material._subject_token_supplier

    assert supplier.get_subject_token(None, None) == caller.subject_token.reveal()


def test_the_supplier_does_not_print_the_token(minter) -> None:
    """It sits inside a `google.auth` credential object, which is the sort of
    thing that ends up in a debugger, a log line or an exception's repr."""
    supplier = minter.federated(persona()).material._subject_token_supplier

    assert SENTINEL_TOKEN not in repr(supplier)


def test_a_minted_credential_does_not_print_its_material(minter) -> None:
    """VER-5 at the one object that holds the real thing. `mode` and
    `principal` are safe to write down; `material` never is."""
    assert "material" not in repr(minter.shared())
    assert repr(minter.shared()) == f"<MintedCredential shared principal={SHARED_ACCOUNT}>"
