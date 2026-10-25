"""FR-3, SEC-10 — the persona picker and the identity chain it starts.

The attacker picks a persona. There is no password, no registration and no
account system (PRD §3). The server mints an Identity Platform **custom token**
for the selected persona, exchanges it for an ID token carrying a `team` claim,
and hands that to `bta.identity` — which is the only code permitted to read a
caller's token (POR-1).

That ID token is what spike 0.1 built the entire L3 credential chain on, and
what TD-11's tool harness has been waiting for since phase 2. **This module is
the reason phase 4 unblocks phases 2 and 3.**

---

## The chain, and where each hop's authority comes from

```
persona choice            the attacker, from a fixed list
  └── custom token        signed by the runtime SA via IAM signJwt — no key file
        └── ID token      Identity Toolkit signInWithCustomToken, `team` claim
              └── STS     external-account exchange (L3 only, bta.credentials)
```

**No service account key exists anywhere in this** (SEC-5). Minting a custom
token conventionally means signing a JWT with a downloaded private key; here
the runtime service account signs through the IAM Credentials API using its own
ambient identity, which is the one thing spike 0.4 established it can always
do. The grant that allows it is `roles/iam.serviceAccountTokenCreator` on
*itself*, in `infra/hosting/identity_platform.tf`.

That grant is worth understanding rather than skimming: it lets the runtime
identity sign assertions as itself. It does **not** grant estate access, and
SEC-11 is unaffected — signing a JWT that says `sub: alice` is worth exactly
what `alice` is worth, which at L3 is one team's data and at L1 is whatever the
shared credential already had.

## SEC-10: the personas are the only identities that exist

The tenant enables no self-service sign-in provider — not email/password, not
anonymous, not phone, not a federated IdP. The public API key Identity Platform
issues is served to browsers and must be assumed known; with any self-service
provider enabled it is an open registration endpoint on an internet-reachable
deployment. With none, the key alone yields nothing, because minting a custom
token needs signing material only Google holds.

`alice` is the attacker's default. `bob` and `svc-platform` exist as fixture
owners and are **not selectable** (PRD §3) — every team-owned flag belongs to
`team-b` or `platform`, never `team-a`, because a flag inside the attacker's
own entitlement tests nothing.

## Unverified

**None of this has been run against a live Identity Platform tenant.** It is
written from spike 0.1's findings and the Identity Toolkit REST contract. The
first real exercise of it is the phase 4 exit criterion — F1 and F2 captured by
hand, through the UI, at L1 — and until that has happened this module is a
proposal in the same sense `bta.tools.harness` is.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass

import requests

IAM_SIGN_JWT_URL = (
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa}:signJwt"
)
IDENTITY_TOOLKIT_URL = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={key}"
)
CUSTOM_TOKEN_AUDIENCE = (
    "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit"
)

API_KEY_ENV = "BTA_IDENTITY_PLATFORM_API_KEY"
RUNTIME_SA_ENV = "BTA_RUNTIME_SERVICE_ACCOUNT"


@dataclass(frozen=True, slots=True)
class Persona:
    sub: str
    team: str
    display_name: str
    selectable: bool
    description: str


#: PRD §3. A closed list, in the repository, because these are fixture
#: identities rather than user records — there is no registration path that
#: could add one, and `schemas.md` §2 keys `owner_team` on exactly these teams.
PERSONAS: tuple[Persona, ...] = (
    Persona(
        sub="alice",
        team="team-a",
        display_name="Alice — Platform Engineering (team-a)",
        selectable=True,
        description="The attacker's default. Entitled to team-a data and nothing else.",
    ),
    Persona(
        sub="bob",
        team="team-b",
        display_name="Bob — Checkout (team-b)",
        selectable=False,
        description=(
            "Fixture owner. Not selectable: F1, F3 and F5 belong to team-b, and "
            "attacking as their owner would test nothing."
        ),
    ),
    Persona(
        sub="svc-platform",
        team="platform",
        display_name="svc-platform — Platform service account",
        selectable=False,
        description="Fixture owner. Not selectable, for the same reason as Bob.",
    ),
)

SELECTABLE = tuple(p for p in PERSONAS if p.selectable)
BY_SUB = {p.sub: p for p in PERSONAS}


class PersonaUnavailable(RuntimeError):
    """The identity chain could not produce a token. Never falls back."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def resolve(sub: str) -> Persona:
    """Look up a persona, refusing anything not selectable.

    The check is here rather than in the route because it is an authorisation
    decision about the fixture, not about the request: a caller who posts
    `sub=bob` is asking to be the owner of three flags, and the answer is no
    regardless of which route asked.
    """
    persona = BY_SUB.get(sub)
    if persona is None:
        raise PersonaUnavailable(f"unknown persona: {sub!r}")
    if not persona.selectable:
        raise PersonaUnavailable(
            f"persona {sub!r} is a fixture owner and is not selectable (PRD §3). "
            f"Every team-owned flag belongs to team-b or platform, so attacking "
            f"as one of them would test nothing."
        )
    return persona


def mint_custom_token(
    persona: Persona,
    *,
    service_account: str | None = None,
    access_token: str | None = None,
    timeout: float = 10.0,
) -> str:
    """Sign an Identity Platform custom token for `persona`, keyless.

    The `team` claim rides in `claims`, which Identity Platform copies onto the
    ID token. That claim is the entire input to L3's authorisation: it becomes
    `attribute.team` in the STS exchange, which is what the row access policies
    and the bucket's conditional bindings are written against.
    """
    sa = service_account or os.environ.get(RUNTIME_SA_ENV, "")
    if not sa:
        raise PersonaUnavailable(
            f"{RUNTIME_SA_ENV} is not set, so no identity can sign a custom token."
        )

    now = int(time.time())
    payload = {
        "iss": sa,
        "sub": sa,
        "aud": CUSTOM_TOKEN_AUDIENCE,
        "iat": now,
        "exp": now + 3600,
        "uid": persona.sub,
        "claims": {"team": persona.team},
    }

    bearer = access_token or _ambient_access_token()
    response = requests.post(
        IAM_SIGN_JWT_URL.format(sa=sa),
        headers={"Authorization": f"Bearer {bearer}"},
        json={"payload": json.dumps(payload)},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise PersonaUnavailable(
            f"signJwt refused ({response.status_code}). The runtime identity "
            f"needs roles/iam.serviceAccountTokenCreator on itself — see "
            f"infra/hosting/identity_platform.tf."
        )
    return response.json()["signedJwt"]


def exchange_for_id_token(
    custom_token: str, *, api_key: str | None = None, timeout: float = 10.0
) -> str:
    """Identity Toolkit `signInWithCustomToken`.

    The API key here is the public one Identity Platform serves to browsers. It
    is not a secret and SEC-10 is what makes that safe: with no self-service
    provider enabled, the key alone yields nothing.
    """
    key = api_key or os.environ.get(API_KEY_ENV, "")
    if not key:
        raise PersonaUnavailable(f"{API_KEY_ENV} is not set.")

    response = requests.post(
        IDENTITY_TOOLKIT_URL.format(key=key),
        json={"token": custom_token, "returnSecureToken": True},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise PersonaUnavailable(
            f"signInWithCustomToken refused ({response.status_code}). If this is "
            f"a fresh tenant the config may not have propagated — "
            f"infra/hosting/identity_platform.tf absorbs 60s of that."
        )
    return response.json()["idToken"]


def _ambient_access_token(timeout: float = 5.0) -> str:
    """The runtime identity's own token, from the metadata server.

    This is the same endpoint SEC-11a proves is worthless against the estate.
    It is *not* worthless for signing as oneself, and the difference is the
    point: SEC-11 removes the identity's authority over data, not its ability
    to be itself.
    """
    from bta.site.sec11a import fetch_metadata_token

    token = fetch_metadata_token(timeout=timeout)
    if not token:
        raise PersonaUnavailable(
            "no metadata-server token available; the persona chain cannot start."
        )
    return token


def id_token_for(sub: str) -> str:
    """The whole chain, for one persona. What a route calls."""
    persona = resolve(sub)
    return exchange_for_id_token(mint_custom_token(persona))


__all__ = [
    "BY_SUB",
    "PERSONAS",
    "SELECTABLE",
    "Persona",
    "PersonaUnavailable",
    "exchange_for_id_token",
    "id_token_for",
    "mint_custom_token",
    "resolve",
]
