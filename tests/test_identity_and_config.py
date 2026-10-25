"""POR-1's adapter and POR-6's inputs.

Two small surfaces that decide large things. Spike 0.1 finding 3 established
that IAM grants on `attribute.team`, so whatever picks that claim is the root
of trust for the whole L3 column — an unknown subject presenting `team-a` was
granted exactly what `alice` was. And POR-6 is a portability requirement before
it is a security one: one hardcoded name and the repository is single-tenant.
"""

from __future__ import annotations

import json

import pytest

from bta.config import DeploymentConfig, MissingConfiguration, from_env
from bta.identity import IdentityError, SubjectToken, get_caller_identity, identity_from_claims

ENVIRONMENT = {
    "BTA_PROJECT_ID": "example-project",
    "BTA_PROJECT_NUMBER": "000000000000",
    "BTA_DATASET_ID": "orbit",
    "BTA_ARTEFACT_BUCKET": "example-artefacts",
    "BTA_INTEGRATION_SECRET_ID": "orbit-integration-key",
    "BTA_FIRESTORE_DATABASE_ID": "(default)",
    "BTA_SHARED_SERVICE_ACCOUNT": "shared@example-project.iam.gserviceaccount.com",
    "BTA_PROVISIONING_SERVICE_ACCOUNT": "prov@example-project.iam.gserviceaccount.com",
    "BTA_ARENA_POOL_ID": "arena-personas",
    "BTA_ARENA_PROVIDER_ID": "identity-platform",
}


class StubVerifier:
    def __init__(self, claims: dict | Exception) -> None:
        self._claims = claims

    def verify(self, bearer_token: str) -> dict:
        del bearer_token
        if isinstance(self._claims, Exception):
            raise self._claims
        return self._claims


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_the_subject_token_does_not_print() -> None:
    """It travels further than any other credential in the process — the STS
    exchange, and the L3 Firestore bearer — so every one of those call sites is
    one f-string from a log line. `reveal()` is greppable; `repr` is not a
    leak."""
    token = SubjectToken("header.payload.signature")

    assert "payload" not in repr(token)
    assert "payload" not in str(token)
    assert "payload" not in f"{token}"
    assert "payload" not in json.dumps({"t": repr(token)})
    assert token.reveal() == "header.payload.signature"


def test_a_caller_without_a_team_claim_is_refused() -> None:
    """The arena pool's attribute condition refuses `assertion.team == ''`, so
    such a token would fail at the STS exchange with a far less legible
    error."""
    with pytest.raises(IdentityError, match="team"):
        identity_from_claims({"sub": "alice"}, "token")


def test_a_caller_without_a_subject_is_refused() -> None:
    with pytest.raises(IdentityError, match="subject"):
        identity_from_claims({"team": "team-a"}, "token")


def test_the_team_comes_from_verified_claims() -> None:
    """Spike 0.1 finding 3, as the assertion it deserves.

    The grant is on the attribute, not the person: an unseen subject presenting
    `team-a` receives `alice`'s access. So the claim must come from a verified
    token and never from anything the caller supplies alongside it. This is the
    only path that produces a `CallerIdentity`.
    """
    caller = get_caller_identity("bearer", StubVerifier({"sub": "mallory", "team": "team-b"}))

    assert (caller.sub, caller.team) == ("mallory", "team-b")


def test_a_failed_verification_is_not_a_denial() -> None:
    """A denial requires a caller to have been identified first
    (`schemas.md` §6). An unidentifiable caller never reaches a tool, so no
    audit record is written and no authorisation outcome is recorded."""
    with pytest.raises(IdentityError):
        get_caller_identity("bearer", StubVerifier(ValueError("bad signature")))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_every_input_comes_from_the_environment() -> None:
    config = from_env(ENVIRONMENT)

    assert isinstance(config, DeploymentConfig)
    assert config.project_id == "example-project"


def test_missing_inputs_are_reported_together() -> None:
    """An operator setting up a shell fixes one round of errors rather than
    ten."""
    with pytest.raises(MissingConfiguration) as raised:
        from_env({"BTA_PROJECT_ID": "example-project"})

    message = str(raised.value)
    assert "BTA_DATASET_ID" in message
    assert "BTA_ARENA_POOL_ID" in message
    assert "BTA_PROJECT_ID" not in message


def test_nothing_is_defaulted() -> None:
    """POR-6: no deployment-specific value is hardcoded anywhere.

    A default here would be a real project's name sitting in the repository,
    which is exactly how a lab quietly becomes single-tenant. Asserted over the
    dataclass rather than by reading the code, so a field added later with a
    convenient default fails this.
    """
    import dataclasses

    for field in dataclasses.fields(DeploymentConfig):
        assert field.default is dataclasses.MISSING, field.name
        assert field.default_factory is dataclasses.MISSING, field.name


def test_an_empty_variable_counts_as_missing() -> None:
    """`export BTA_DATASET_ID=` is a common shell accident and would otherwise
    produce a query against a dataset named empty string."""
    environment = dict(ENVIRONMENT, BTA_DATASET_ID="   ")

    with pytest.raises(MissingConfiguration, match="BTA_DATASET_ID"):
        from_env(environment)
