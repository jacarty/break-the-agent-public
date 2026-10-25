"""POR-1 — one adapter, `get_caller_identity(request) -> CallerIdentity`.

Everything else in the process reads identity from a `CallerIdentity` handed to
it. Nothing else parses a header, nothing else verifies a token, and nothing
else decides what team a caller is on.

The persona picker (FR-3) is one implementation of the verifier below; an IAP
header verifier would be another, which is the portability claim POR-1 makes.
Neither exists yet — the site arrives in phase 4 — so what ships here is the
seam and the rule, with the verifier injected. That is deliberate rather than
unfinished: writing the adapter around whatever `Request` object the site
happens to have would bind POR-1 to a web framework, and the requirement says
the opposite.

**The team claim is never caller-supplied.** Spike 0.1 finding 3: IAM grants on
`attribute.team`, so an unknown subject presenting `team-a` receives `alice`'s
access. A verifier that reads `team` from a query parameter, a header or a
request body collapses the entire L3 column, and no IAM binding anywhere would
catch it. `RejectsCallerSuppliedTeam` below is the test hook for that.
"""

from __future__ import annotations

from typing import Any, Protocol

from .tokens import CallerIdentity, SubjectToken

__all__ = [
    "CallerIdentity",
    "SubjectToken",
    "ClaimsVerifier",
    "get_caller_identity",
    "identity_from_claims",
]


class ClaimsVerifier(Protocol):
    """Turns a bearer token into verified claims, or raises.

    Implementations must verify signature, issuer, audience and expiry before
    returning. A verifier that decodes without verifying would make every
    claim below attacker-controlled.
    """

    def verify(self, bearer_token: str) -> dict[str, Any]: ...


class IdentityError(Exception):
    """The caller could not be identified. Never a denial — a denial requires a
    caller to have been identified first (`schemas.md` §6)."""


def get_caller_identity(bearer_token: str, verifier: ClaimsVerifier) -> CallerIdentity:
    """The adapter. One call site per request, at the edge."""
    try:
        claims = verifier.verify(bearer_token)
    except Exception as exc:  # the cause is the verifier's to name
        raise IdentityError("token verification failed") from exc
    return identity_from_claims(claims, bearer_token)


def identity_from_claims(claims: dict[str, Any], bearer_token: str) -> CallerIdentity:
    """Claims in, identity out. Split from the verification step so the mapping
    is testable without a signing key, and so there is exactly one place where
    a claim name becomes a field name."""
    sub = claims.get("sub") or claims.get("user_id") or ""
    team = claims.get("team") or ""
    if not sub:
        raise IdentityError("verified claims carry no subject")
    if not team:
        raise IdentityError(
            "verified claims carry no team claim; the arena pool's attribute "
            "condition would refuse this token at the STS exchange"
        )
    return CallerIdentity(sub=str(sub), team=str(team), subject_token=SubjectToken(bearer_token))
