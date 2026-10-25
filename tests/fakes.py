"""Offline stand-ins for the four cloud surfaces.

Every one of these mirrors a control that exists in `infra/estate/`, and the
mirroring is the point: a fake that allows everything would let a tool test
pass while the tool reached data it should not, and a fake that denies
everything would reproduce spikes 0.1 and 0.3 — all-denied runs from causes
unrelated to the control, every assertion passing.

So each fake enforces the same rule its Terraform counterpart does, and the
comment says which:

- `FakeRows` filters `incidents` by `owner_team` for a federated caller, the
  way `google_bigquery_row_access_policy.incidents_by_team` does, and leaves
  `services`, `deploys` and `logs` unfiltered, the way the estate does.
- `FakeObjects` refuses an object outside the caller's `<team>/` prefix, the
  way the conditional binding in `storage.tf` does.
- `FakeFirestoreTransport` evaluates `firestore.rules` — `owner_team` against
  the token's team for `employees`, `persona_sub` against its uid for
  `agent_memory`.

What they do **not** mirror is the shape of a real client library, and the
tests say where that limit is. The Google glue in `clients.py` — building a
`bigquery.Client`, a `storage.Client`, a server-SDK Firestore client — is
exercised by the live harness and by nothing here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bta.credentials import CredentialFactory, MintedCredential
from bta.identity import CallerIdentity, SubjectToken
from bta.models import CredentialMode, Level, Surface, require_level
from bta.tools.clients import (
    CapabilityScopedProvisioning,
    FirestoreRestDocuments,
    IntegrationOutcome,
    PermissiveProvisioning,
)

SHARED_ACCOUNT = "bta-estate-shared@example-project.iam.gserviceaccount.com"
PROVISIONING_ACCOUNT = "bta-provisioning@example-project.iam.gserviceaccount.com"
AUDIENCE = (
    "//iam.googleapis.com/projects/000000000000/locations/global"
    "/workloadIdentityPools/arena-personas/providers/identity-platform"
)

#: A sentinel that must never appear in an audit record. Distinctive enough to
#: grep a whole serialised record for, which is how VER-5 is asserted.
#:
#: Deliberately not JWT-shaped. A realistic-looking token would be a truer
#: fixture and would also give the secret scanners something to find on every
#: commit, and SEC-7 already spends that budget on the flag values themselves.
SENTINEL_TOKEN = "SENTINEL-SUBJECT-TOKEN-DO-NOT-LOG"

#: Likewise for the integration key. Not a flag value and not credential-shaped
#: by accident — F4's real payload is minted credential-shaped (`values.py`),
#: and a test that used a plausible key would be a test that could not tell a
#: leak from a coincidence.
SENTINEL_KEY = "SENTINEL-INTEGRATION-KEY-DO-NOT-DISCLOSE"


def persona(sub: str = "alice", team: str = "team-a") -> CallerIdentity:
    """A caller, with a token that is distinct per persona.

    The per-persona suffix is not cosmetic. With one token string shared by
    every persona, `FakeFirestoreTransport` resolves whichever claims were
    registered last, and `alice` reads as `bob` — which showed up as two L3
    denials that were correct outcomes reached for entirely the wrong reason.
    The paired positive control is what caught it, which is the argument for
    the rule in miniature.

    `SENTINEL_TOKEN` stays a substring of every token so the VER-5 scan in
    `test_tool_boundary.py` still catches any of them.
    """
    return CallerIdentity(sub=sub, team=team, subject_token=SubjectToken(f"{SENTINEL_TOKEN}.{sub}"))


# ---------------------------------------------------------------------------
# Provider exceptions, shaped the way the real ones carry a reason
# ---------------------------------------------------------------------------


class FakeGoogleError(Exception):
    """`errors` is the list google-api-core exposes on a JSON-API failure, and
    the only field `errors.classify` reads from it."""

    def __init__(self, reason: str, message: str = "fake google error") -> None:
        super().__init__(message)
        self.errors = [{"domain": "global", "reason": reason, "message": message}]


def access_denied() -> FakeGoogleError:
    return FakeGoogleError("accessDenied", "Access Denied: no permission on the resource")


def quota_exceeded() -> FakeGoogleError:
    """Spike 0.1 finding 2, in a constructor.

    A federated principal missing `roles/serviceusage.serviceUsageConsumer` or
    `roles/bigquery.jobUser` fails like this — a 403 that looks exactly like a
    working control and means the deployment is broken.
    """
    return FakeGoogleError("quotaExceeded", "Quota exceeded: serviceusage consumer")


# ---------------------------------------------------------------------------
# Minting
# ---------------------------------------------------------------------------


@dataclass
class FakeMinter:
    """Records what it was asked for, and hands back a credential whose
    `material` is the caller it was minted for — which is what lets the fake
    surfaces below enforce a per-caller rule without a network."""

    calls: list[CredentialMode] = field(default_factory=list)

    def shared(self) -> MintedCredential:
        self.calls.append(CredentialMode.SHARED)
        return MintedCredential(CredentialMode.SHARED, SHARED_ACCOUNT, material=None)

    def service(self) -> MintedCredential:
        self.calls.append(CredentialMode.SERVICE)
        return MintedCredential(CredentialMode.SERVICE, PROVISIONING_ACCOUNT, material=None)

    def federated(self, caller: CallerIdentity) -> MintedCredential:
        self.calls.append(CredentialMode.FEDERATED)
        return MintedCredential(
            CredentialMode.FEDERATED,
            f"principalSet://.../attribute.team/{caller.team}",
            material=caller,
        )


class LyingMinter:
    """Returns the shared estate credential whatever it is asked for.

    This is not a strawman. A minter that fell back to an ambient credential
    when a token exchange failed would behave exactly like this, would look
    like sensible error handling in review, and would leave every L3 cell
    reporting `held` over a control that never ran.
    """

    def shared(self) -> MintedCredential:
        return MintedCredential(CredentialMode.SHARED, SHARED_ACCOUNT, material=None)

    service = shared
    federated = staticmethod(
        lambda caller: MintedCredential(CredentialMode.SHARED, SHARED_ACCOUNT, material=None)
    )


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------

#: Tables the estate protects with a row access policy. Everything else is
#: readable by every persona at every level, deliberately (`bigquery.tf`).
POLICY_PROTECTED_TABLES = frozenset({"incidents"})


@dataclass
class FakeRows:
    tables: dict[str, list[dict[str, Any]]]
    credentials: MintedCredential
    queries: list[str] = field(default_factory=list)

    def rows(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        self.queries.append(sql)
        table = _table_of(sql)
        rows = [dict(row) for row in self.tables.get(table, [])]

        for column in (
            "service",
            "severity",
            "status",
            "deploy_id",
            "incident_id",
            # FR-9's amendment. Without this the cross-team enumeration test
            # would filter on nothing and pass against any implementation.
            "owner_team",
        ):
            if column in parameters:
                rows = [row for row in rows if row.get(column) == parameters[column]]

        if table in POLICY_PROTECTED_TABLES:
            rows = self._apply_row_access_policy(rows)

        return rows[: parameters.get("limit", len(rows))]

    def _apply_row_access_policy(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The shape of this control is the reason `get_incident` records
        `allowed` on an L3 denial: rows are absent, not refused. There is no
        error to classify and no 403 to attribute — `rows_returned: 0` is the
        whole of the signal."""
        caller = self.credentials.material
        if self.credentials.mode is not CredentialMode.FEDERATED:
            return rows  # `incidents_shared_credential`, filter_predicate TRUE
        return [row for row in rows if row.get("owner_team") == caller.team]


def _table_of(sql: str) -> str:
    _, _, after = sql.partition("{dataset}.")
    return after.split()[0] if after else ""


@dataclass
class FakeObjects:
    objects: dict[str, str]
    credentials: MintedCredential

    def read(self, object_name: str) -> str:
        caller = self.credentials.material
        if self.credentials.mode is CredentialMode.FEDERATED and not object_name.startswith(
            f"{caller.team}/"
        ):
            raise access_denied()
        if object_name not in self.objects:
            raise FakeGoogleError("notFound", "No such object")
        return self.objects[object_name]


@dataclass
class FakeServerDocuments:
    """L1 and L2. Reads anything, because Firestore's server SDKs are not
    subject to Security Rules at all (spike 0.3 finding 2)."""

    documents: dict[str, dict[str, Any]]

    def get(self, collection: str, document_id: str) -> dict[str, Any] | None:
        return self.documents.get(f"{collection}/{document_id}")


@dataclass
class FakeFirestoreTransport:
    """`firestore.rules`, evaluated in Python.

    Returns the REST API's own wire shape — typed value wrappers and an
    `error.status` — so that `FirestoreRestDocuments` is exercised for real,
    decoding included. That class is the only implementation of the L3
    document path, and spike 0.3 finding 2 is that the failure mode when it is
    bypassed is silent.
    """

    documents: dict[str, dict[str, Any]]
    token_claims: dict[str, dict[str, str]] = field(default_factory=dict)

    def get_json(self, url: str, bearer: str) -> tuple[int, dict[str, Any]]:
        claims = self.token_claims.get(bearer)
        if claims is None:
            return 401, {"error": {"status": "UNAUTHENTICATED"}}

        _, _, path = url.partition("/documents/")
        collection, _, document_id = path.partition("/")
        document = self.documents.get(f"{collection}/{document_id}")
        if document is None:
            return 404, {"error": {"status": "NOT_FOUND"}}

        if not _rules_allow(collection, document, claims):
            return 403, {"error": {"status": "PERMISSION_DENIED"}}

        return 200, {"fields": _encode(document)}


def _rules_allow(collection: str, document: dict[str, Any], claims: dict[str, str]) -> bool:
    if collection == "employees":
        return document.get("owner_team") == claims.get("team")
    if collection == "agent_memory":
        return document.get("persona_sub") == claims.get("uid")
    return False  # the deny-by-default match in firestore.rules


def _encode(document: dict[str, Any]) -> dict[str, Any]:
    encoded: dict[str, Any] = {}
    for name, value in document.items():
        if value is None:
            encoded[name] = {"nullValue": None}
        elif isinstance(value, bool):
            encoded[name] = {"booleanValue": value}
        elif isinstance(value, int):
            encoded[name] = {"integerValue": str(value)}
        else:
            encoded[name] = {"stringValue": str(value)}
    return encoded


@dataclass
class FakeSecretReader:
    """Stands in for Secret Manager, not for the classes that use it.

    `SecretBackedIntegrationTester` and `DelegatedIntegrationTester` are the
    real ones in every test — the difference between them is F4's L3 claim.
    """

    key: str = SENTINEL_KEY
    reads: list[CredentialMode] = field(default_factory=list)

    def __call__(self, credentials: MintedCredential, project_id: str, secret_id: str) -> str:
        del project_id, secret_id
        self.reads.append(credentials.mode)
        return self.key


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------


@dataclass
class FakeEstate:
    """One deployment's worth of synthetic data, shared by the tool tests."""

    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    objects: dict[str, str] = field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    write_path_marker: str | None = None


class FakeClients:
    """A `ClientProvider` over the fakes above.

    It resolves credentials through the **real** `CredentialFactory` and the
    real policy table, so a tool test that passes has passed under the actual
    L1/L2/L3 credential rules rather than under a test's idea of them. Only the
    surfaces are substituted.

    `EstateClients` — which client class each level gets — is tested directly
    in `test_estate_clients.py`, because that dispatch is where spike 0.3's
    silent failure would live and a fake that reimplemented it would prove
    nothing about it.
    """

    def __init__(
        self,
        estate: FakeEstate,
        *,
        minter: FakeMinter | None = None,
        secrets: FakeSecretReader | None = None,
    ) -> None:
        self.estate = estate
        self.minter = minter or FakeMinter()
        self.secrets = secrets or FakeSecretReader()
        self.credentials = CredentialFactory(self.minter)
        self.transport = FakeFirestoreTransport(
            documents=estate.documents,
            token_claims={},
        )

    def register_token(self, caller: CallerIdentity) -> None:
        """Teach the fake Identity Platform what this token means. A caller
        whose token is not registered gets `UNAUTHENTICATED`, which is what an
        expired or forged one would get."""
        self.transport.token_claims[caller.subject_token.reveal()] = {
            "uid": caller.sub,
            "team": caller.team,
        }

    def for_surface(self, surface: Surface, caller: CallerIdentity, level: Level) -> Any:
        # The real provider guards this, and a double that did not would be a
        # double that keeps passing on the day the guard is removed from the
        # real one. Every `is Level.L3` below is the reason it is there.
        require_level(level)

        if surface is Surface.WRITE_PATH:
            marker = self.estate.write_path_marker
            if level is Level.L3:
                return CapabilityScopedProvisioning(marker=marker)
            return PermissiveProvisioning(marker=marker)

        minted = self.credentials.for_surface(surface, caller, level)

        match surface:
            case Surface.BIGQUERY:
                return FakeRows(self.estate.tables, minted)
            case Surface.CLOUD_STORAGE:
                return FakeObjects(self.estate.objects, minted)
            case Surface.FIRESTORE:
                if level is Level.L3:
                    return FirestoreRestDocuments(
                        "https://firestore.example/v1/projects/p/databases/(default)/documents",
                        caller.subject_token.reveal(),
                        self.transport,
                    )
                return FakeServerDocuments(self.estate.documents)
            case Surface.SECRET_MANAGER:
                from bta.tools.clients import (
                    DelegatedIntegrationTester,
                    SecretBackedIntegrationTester,
                )

                tester = (
                    DelegatedIntegrationTester
                    if level is Level.L3
                    else SecretBackedIntegrationTester
                )
                return tester(minted, "example-project", "orbit-integration-key", self.secrets)

        raise ValueError(surface)


__all__ = [
    "AUDIENCE",
    "PROVISIONING_ACCOUNT",
    "SENTINEL_KEY",
    "SENTINEL_TOKEN",
    "SHARED_ACCOUNT",
    "FakeClients",
    "FakeEstate",
    "FakeGoogleError",
    "FakeMinter",
    "FakeObjects",
    "FakeRows",
    "FakeSecretReader",
    "FakeServerDocuments",
    "IntegrationOutcome",
    "LyingMinter",
    "access_denied",
    "persona",
    "quota_exceeded",
]
