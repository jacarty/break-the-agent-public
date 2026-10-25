"""Which client each level actually receives.

This is a small file guarding the largest silent failure in the lab. Spike 0.3
finding 2: Firestore's server SDKs are not subject to Security Rules at all, so
a tool handed a server client at L3 keeps returning correct-looking data with
the entire document-level control gone. Nothing in the tool's behaviour, its
audit record or its result would say so.

`EstateClients.for_surface` is where that decision is made, and it is tested
directly rather than through a fake provider — a fake that reimplemented the
dispatch would prove things about the fake.

None of this constructs a Google client: `clients.py` imports the client
libraries inside the methods that use them, so the dispatch is fully
exercisable offline. What it does not exercise is whether those libraries then
behave as expected, which is the harness's job.
"""

from __future__ import annotations

import pytest
from fakes import FakeMinter, persona

from bta.config import DeploymentConfig
from bta.credentials import CredentialFactory
from bta.models import Level, Surface
from bta.tools.clients import (
    BigQueryRows,
    CapabilityScopedProvisioning,
    DelegatedIntegrationTester,
    EstateClients,
    FirestoreRestDocuments,
    FirestoreServerDocuments,
    PermissiveProvisioning,
    SecretBackedIntegrationTester,
    StorageObjects,
)

CONFIG = DeploymentConfig(
    project_id="example-project",
    project_number="000000000000",
    dataset_id="orbit",
    artefact_bucket="example-artefacts",
    integration_secret_id="orbit-integration-key",
    firestore_database_id="(default)",
    shared_service_account="bta-estate-shared@example-project.iam.gserviceaccount.com",
    provisioning_service_account="bta-provisioning@example-project.iam.gserviceaccount.com",
    arena_pool_id="arena-personas",
    arena_provider_id="identity-platform",
)


@pytest.fixture
def clients() -> EstateClients:
    return EstateClients(
        CONFIG, CredentialFactory(FakeMinter()), transport=None, write_path_marker="MARKER"
    )


@pytest.mark.parametrize("level", [Level.L1, Level.L2])
def test_firestore_uses_the_server_sdk_below_l3(level: Level, clients: EstateClients) -> None:
    """The vulnerability, as a client choice. Security Rules are bypassed
    wholesale — not because they are weak, but because this credential never
    presents a caller token for them to evaluate."""
    reader = clients.for_surface(Surface.FIRESTORE, persona(), level)

    assert isinstance(reader, FirestoreServerDocuments)


def test_firestore_uses_the_caller_s_own_token_at_l3(clients: EstateClients) -> None:
    """The assertion this file exists for.

    If this ever returns a server client, every L3 Firestore cell reports
    `held` while F5 and F7b are wide open, and no other test in the repository
    would fail.
    """
    reader = clients.for_surface(Surface.FIRESTORE, persona(), Level.L3)

    assert isinstance(reader, FirestoreRestDocuments), (
        "L3 was handed a server-SDK Firestore client. Server SDKs are not "
        "subject to Security Rules (spike 0.3 finding 2), so the document-level "
        "control is gone and the data still looks correct."
    )


@pytest.mark.parametrize("level", [Level.L1, Level.L2])
def test_the_integration_key_is_read_by_this_process_below_l3(
    level: Level, clients: EstateClients
) -> None:
    assert isinstance(
        clients.for_surface(Surface.SECRET_MANAGER, persona(), level),
        SecretBackedIntegrationTester,
    )


def test_the_integration_key_is_delegated_at_l3(clients: EstateClients) -> None:
    """FR-14: at L3 the agent never holds the key."""
    assert isinstance(
        clients.for_surface(Surface.SECRET_MANAGER, persona(), Level.L3),
        DelegatedIntegrationTester,
    )


@pytest.mark.parametrize("level", [Level.L1, Level.L2])
def test_the_write_path_accepts_any_role_below_l3(level: Level, clients: EstateClients) -> None:
    assert isinstance(
        clients.for_surface(Surface.WRITE_PATH, persona(), level), PermissiveProvisioning
    )


def test_the_write_path_is_capability_scoped_at_l3(clients: EstateClients) -> None:
    assert isinstance(
        clients.for_surface(Surface.WRITE_PATH, persona(), Level.L3),
        CapabilityScopedProvisioning,
    )


@pytest.mark.parametrize("level", list(Level))
def test_bigquery_and_storage_use_one_implementation_at_every_level(
    level: Level, clients: EstateClients
) -> None:
    """Only the credential varies. Two implementations here would be two code
    paths for the matrix to be wrong about."""
    assert isinstance(clients.for_surface(Surface.BIGQUERY, persona(), level), BigQueryRows)
    assert isinstance(clients.for_surface(Surface.CLOUD_STORAGE, persona(), level), StorageObjects)


def test_the_write_path_mints_no_credential(clients: EstateClients) -> None:
    """`credentials.policy` refuses to mint for the write path, so a provider
    that asked would raise rather than quietly minting the shared credential
    for a surface that authenticates to nothing."""
    minter = FakeMinter()
    provider = EstateClients(CONFIG, CredentialFactory(minter), transport=None)

    provider.for_surface(Surface.WRITE_PATH, persona(), Level.L3)

    assert minter.calls == []


def test_the_audience_is_derived_from_the_deployment(clients: EstateClients) -> None:
    """An operator supplying the audience separately would eventually supply
    one that disagrees with the project and pool it is built from, and every
    L3 exchange would fail with an audience mismatch that looks nothing like
    its cause."""
    assert CONFIG.workload_identity_audience == (
        "//iam.googleapis.com/projects/000000000000/locations/global"
        "/workloadIdentityPools/arena-personas/providers/identity-platform"
    )
