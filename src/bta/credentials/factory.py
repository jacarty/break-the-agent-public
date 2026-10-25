"""Minting, per request, at the tool boundary.

`policy.py` decides *which* credential. This decides *how one is obtained*, and
keeps the two apart so the interesting half stays readable: the table is the
experiment, and everything here is plumbing in service of it.

The minter is a protocol with one real implementation and one fake. That is not
test scaffolding for its own sake — spike 0.1's whole method was that a
positive result must not be an artefact of an SDK falling back to ambient
credentials, and an injected minter is how the same guarantee is kept in the
product. `google.auth.default()` is called in exactly one place below, for
exactly one purpose: impersonation source material at L1 and L2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from bta.identity import CallerIdentity
from bta.models import CredentialMode, Level, Surface

from .policy import CredentialPolicyViolation, mode_for

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
SUBJECT_TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"


@dataclass(frozen=True, slots=True)
class MintedCredential:
    """A credential and the two facts about it that may be written down.

    `principal` names an identity — a service account email, or a principalSet
    for a federated caller. It is not credential material and it is what makes
    a denial attributable in a harness run. `material` is the credential object
    itself and is never rendered: the audit record carries `mode` and nothing
    else from this class (`schemas.md` §6, VER-5), which a test asserts.
    """

    mode: CredentialMode
    principal: str
    material: Any

    def __repr__(self) -> str:
        # The google-auth objects have serviceable reprs; the STS one has held
        # a token in some versions. Not worth finding out inside an exception.
        return f"<MintedCredential {self.mode.value} principal={self.principal}>"


class CredentialMinter(Protocol):
    """The three ways this lab obtains authority.

    One method per mode rather than one method taking a mode, so that a caller
    holding a minter still cannot loop over `CredentialMode` and get whichever
    it likes without saying so in the code.
    """

    def shared(self) -> MintedCredential: ...

    def federated(self, caller: CallerIdentity) -> MintedCredential: ...

    def service(self) -> MintedCredential: ...


class CredentialFactory:
    """Policy plus minter. The only object that returns credentials."""

    def __init__(self, minter: CredentialMinter) -> None:
        self._minter = minter

    def for_surface(
        self, surface: Surface, caller: CallerIdentity, level: Level
    ) -> MintedCredential:
        mode = mode_for(level, surface)

        match mode:
            case CredentialMode.SHARED:
                minted = self._minter.shared()
            case CredentialMode.FEDERATED:
                minted = self._minter.federated(caller)
            case CredentialMode.SERVICE:
                minted = self._minter.service()
            case _:  # pragma: no cover - unreachable while the enum has three members
                # A fourth mode added to `CredentialMode` and to `POLICY` but
                # not here would otherwise leave `minted` unbound and surface as
                # an `UnboundLocalError` several frames away. The loud failure
                # stays loud, and says what is actually wrong.
                raise CredentialPolicyViolation(f"no minting path for credential mode {mode.value}")

        self._assert_invariants(minted, mode, level, surface)
        return minted

    @staticmethod
    def _assert_invariants(
        minted: MintedCredential, mode: CredentialMode, level: Level, surface: Surface
    ) -> None:
        """`policy.mode_for` already refused shared-at-L3. This refuses a minter
        that answered a question it was not asked.

        Two failures rather than one, because they are genuinely different
        bugs: the policy check catches a bad edit to the table, and this catches
        an implementation whose `federated()` quietly returns the shared
        credential — which is exactly what a plausible-looking fallback in a
        minter would do on a token it could not exchange, and it would leave
        every L3 cell reading `held`.
        """
        if minted.mode is not mode:
            raise CredentialPolicyViolation(
                f"policy resolved {level}/{surface.value} to {mode.value} and the "
                f"minter returned {minted.mode.value}"
            )
        if level is Level.L3 and minted.mode is CredentialMode.SHARED:
            raise CredentialPolicyViolation(
                f"minter returned the shared estate credential for L3/{surface.value}"
            )


class GoogleCredentialMinter:
    """The real one.

    Constructed from deployment inputs (POR-6) — nothing here knows a project
    id, a pool name or a service account email of its own.
    """

    def __init__(
        self,
        *,
        shared_service_account: str,
        provisioning_service_account: str,
        workload_identity_audience: str,
        source_credentials: Any | None = None,
    ) -> None:
        self._shared_sa = shared_service_account
        self._provisioning_sa = provisioning_service_account
        self._audience = workload_identity_audience
        self._source = source_credentials

    def _source_credentials(self) -> Any:
        """The runtime identity, from the metadata server.

        Spike 0.4 established this is always reachable on Cloud Run and cannot
        be turned off. SEC-11's answer is that the token it issues is worthless
        — the runtime holds no estate access — and its only use is as
        impersonation source material for the two accounts that do.
        """
        if self._source is None:
            import google.auth

            self._source, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        return self._source

    def _impersonate(self, target: str, mode: CredentialMode) -> MintedCredential:
        from google.auth import impersonated_credentials

        return MintedCredential(
            mode=mode,
            principal=target,
            material=impersonated_credentials.Credentials(
                source_credentials=self._source_credentials(),
                target_principal=target,
                target_scopes=[CLOUD_PLATFORM_SCOPE],
            ),
        )

    def shared(self) -> MintedCredential:
        return self._impersonate(self._shared_sa, CredentialMode.SHARED)

    def service(self) -> MintedCredential:
        return self._impersonate(self._provisioning_sa, CredentialMode.SERVICE)

    def federated(self, caller: CallerIdentity) -> MintedCredential:
        """The L3 credential: constructed in memory, per request, from the
        caller's own assertion.

        No credential file and no `GOOGLE_APPLICATION_CREDENTIALS` — external
        account credentials are normally loaded from JSON on disk, which is
        precisely the thing FR-14 says must not exist here. The subject token
        supplier is the seam that removes it (spike 0.1 finding 7), and it is
        called per credential refresh rather than once, so the process holds no
        cached caller token beyond the credential's own lifetime.
        """
        from google.auth import identity_pool

        material = identity_pool.Credentials(
            audience=self._audience,
            subject_token_type=SUBJECT_TOKEN_TYPE_JWT,
            subject_token_supplier=_InMemorySupplier(caller),
            scopes=[CLOUD_PLATFORM_SCOPE],
        )
        return MintedCredential(
            mode=CredentialMode.FEDERATED,
            principal=_principal_set(self._audience, caller.team),
            material=material,
        )


class _InMemorySupplier:
    """Returns the caller's token, and nothing else, from memory.

    Subclassing is avoided deliberately: `identity_pool.SubjectTokenSupplier`
    is an ABC and duck typing here keeps `google.auth` out of the type surface
    of a class that exists to hold one string.
    """

    __slots__ = ("_caller",)

    def __init__(self, caller: CallerIdentity) -> None:
        self._caller = caller

    def get_subject_token(self, context: Any, request: Any) -> str:
        return self._caller.subject_token.reveal()

    def __repr__(self) -> str:
        return f"<subject token supplier for {self._caller.sub}>"


def _principal_set(audience: str, team: str) -> str:
    """The principal the grant is actually on — an attribute, not a person.

    Spike 0.1 finding 3. Recorded on the minted credential so that a harness
    run reports the principal IAM evaluated rather than the persona the lab
    believes it minted for.
    """
    pool = audience.removeprefix("//iam.googleapis.com/").split("/providers/")[0]
    return f"principalSet://iam.googleapis.com/{pool}/attribute.team/{team}"


__all__ = [
    "CredentialFactory",
    "CredentialMinter",
    "GoogleCredentialMinter",
    "MintedCredential",
]
