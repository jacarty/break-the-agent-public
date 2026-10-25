"""SEC-11a — the ambient-credential check, run from inside the service.

SEC-11 says the runtime identity holds no estate access. Spike 0.4 established
that the instance metadata server is link-local infrastructure on Cloud Run:
always reachable, impossible to disable, one unauthenticated HTTP request from
a token for whatever the runtime identity can do. The control is therefore not
that the credential is unobtainable — it is that the credential is **worthless
when obtained**.

That is a claim about IAM, and IAM claims are exactly the kind that stay true
in a plan and stop being true in a deployment. SEC-11a converts it into
something that fails:

> obtain a metadata-server token from inside the running service and assert
> that BigQuery, Firestore and Cloud Storage all refuse it.

**Why this must run in the service rather than in CI.** The token this fetches
is the runtime identity's, and only a process on the instance can get it. Run
from a laptop it would test the operator's own credentials, which prove
nothing. Run against the default compute service account — the deployment
mistake it exists to catch — it would pass everywhere else and fail only here.

**Why a passing suite would otherwise hide the failure.** Every tool would use
federated credentials correctly, every denial would look correct, and no attack
case would exercise the ambient path. L3 would report `held` across the entire
column while remaining one unauthenticated HTTP request from total bypass —
the precise failure SC-4 classifies as `absent`, and the most damaging form of
it, because nothing in a passing run would reveal it.

**It fails closed.** `verify()` returns findings; `assert_worthless()` raises.
The site calls the second at startup, so a deployment that granted the runtime
identity estate access does not serve a lab whose L3 column is silently
meaningless — it does not serve.

A surface that cannot be reached at all (network error, API disabled) is
reported as `unverified`, not as `refused`, and `assert_worthless()` treats
`unverified` as failure. The same principle `scripts/verify-teardown.sh` uses:
a surface the scan could not read fails it rather than counting as clear.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import requests

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)
METADATA_HEADERS = {"Metadata-Flavor": "Google"}

Outcome = Literal["refused", "allowed", "unverified"]


@dataclass(frozen=True, slots=True)
class Finding:
    surface: str
    outcome: Outcome
    detail: str

    @property
    def ok(self) -> bool:
        return self.outcome == "refused"


class AmbientCredentialUsable(RuntimeError):
    """The runtime identity reached the estate. SEC-11 is violated."""


def fetch_metadata_token(timeout: float = 5.0) -> str | None:
    """The unauthenticated request spike 0.4 measured. `None` if unreachable —
    which off Cloud Run is the ordinary case and is why this returns rather
    than raising."""
    try:
        response = requests.get(METADATA_TOKEN_URL, headers=METADATA_HEADERS, timeout=timeout)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    return response.json().get("access_token")


def _probe(surface: str, url: str, token: str, timeout: float, *, method: str = "GET") -> Finding:
    """One read attempt with the ambient token.

    403 and 401 are the outcome SEC-11 predicts. 404 counts as refused only
    when the resource genuinely does not exist, which cannot be told apart from
    a masked permission denial — so it is reported as `unverified` and fails
    the assertion rather than passing it.

    **A probe URL has to be one that answers with an authorisation verdict for
    an authorised caller too.** The Firestore probe originally read
    `/documents`, which returns 404 to *everyone* — verified against a project
    owner, who holds full access and still got 404. A probe that cannot return
    `allowed` cannot return `refused` either: it reported `unverified` on every
    deployment, and since `unverified` fails closed, the service could never
    start. The whole-endpoint check below is the test that catches the class:
    if a probe never returns 200 for a principal that genuinely has access, it
    is measuring nothing.
    """
    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={} if method == "POST" else None,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return Finding(surface, "unverified", f"unreachable: {type(exc).__name__}")

    if response.status_code in (401, 403):
        return Finding(surface, "refused", f"HTTP {response.status_code}")
    if response.status_code == 200:
        return Finding(surface, "allowed", "HTTP 200 — the ambient token read this surface")
    return Finding(surface, "unverified", f"HTTP {response.status_code}")


def verify(
    *,
    project_id: str,
    dataset_id: str,
    bucket: str,
    firestore_database_id: str,
    token: str | None = None,
    timeout: float = 10.0,
) -> list[Finding]:
    """Probe all three surfaces with the ambient token.

    Every URL interpolates deployment inputs (POR-5, POR-6); the hosts are the
    platform's, not the deployment's, which is the distinction POR-5 turns on.
    """
    ambient = token if token is not None else fetch_metadata_token()
    if not ambient:
        return [
            Finding(surface, "unverified", "no metadata token available")
            for surface in ("bigquery", "cloud_storage", "firestore")
        ]

    return [
        _probe(
            "bigquery",
            f"https://bigquery.googleapis.com/bigquery/v2/projects/{project_id}"
            f"/datasets/{dataset_id}/tables",
            ambient,
            timeout,
        ),
        _probe(
            "cloud_storage",
            f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?maxResults=1",
            ambient,
            timeout,
        ),
        _probe(
            "firestore",
            # `:listCollectionIds`, not `/documents`. Listing the root of a
            # database is not a GET on `/documents` — that path 404s for every
            # caller including an owner — and this is the read that answers 200
            # for a principal with access and 403 for one without, which is the
            # only shape that makes the probe mean anything.
            f"https://firestore.googleapis.com/v1/projects/{project_id}"
            f"/databases/{firestore_database_id}/documents:listCollectionIds",
            ambient,
            timeout,
            method="POST",
        ),
    ]


def assert_worthless(findings: list[Finding]) -> None:
    """Raise unless every surface refused.

    `unverified` fails. A check that could not run is not a check that passed,
    and SEC-11a's whole value is that it is the one thing standing between a
    misconfigured runtime identity and an L3 column of meaningless `held`s.
    """
    bad = [f for f in findings if not f.ok]
    if not bad:
        return
    detail = "; ".join(f"{f.surface}: {f.outcome} ({f.detail})" for f in bad)
    raise AmbientCredentialUsable(
        f"SEC-11a failed. The runtime identity must hold no estate access, and "
        f"the metadata-server token was not refused everywhere: {detail}. "
        f"Check that Cloud Run's service_account is the dedicated runtime "
        f"identity from infra/estate and not the default compute account "
        f"(spike 0.4). Until this passes, every L3 verdict is unsound."
    )


__all__ = [
    "METADATA_TOKEN_URL",
    "AmbientCredentialUsable",
    "Finding",
    "assert_worthless",
    "fetch_metadata_token",
    "verify",
]
