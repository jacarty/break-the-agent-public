# Spike 0.1 — Federated credentials bound to a caller's team claim

**Status:** answered — **yes**, both halves.
**Date:** 2026-08-12
**Requirements touched:** FR-14, FR-3, SEC-5, SEC-10, POR-1

## Question

Can a caller's identity, carrying a `team` claim, be exchanged for Google credentials **constructed in memory**, such that BigQuery itself — not the application, and not the model — refuses another team's data?

If no, L3's credential story is not implementable as specified and everything downstream changes. This spike is blocking for that reason.

## Method

Five hops, each tested rather than assumed:

1. A custom token is minted for a persona, signed by a dedicated service account through the IAM Credentials `signJwt` API. No key material is downloaded (SEC-5). Claims are asserted at mint time by the server; the caller supplies nothing.
2. `signInWithCustomToken` exchanges it for an Identity Platform ID token.
3. A workload identity pool provider trusts `https://securetoken.google.com/<project>`, restricted to audience `<project>`, mapping `google.subject = assertion.sub` and `attribute.team = assertion.team`, with attribute condition `assertion.team != ''`.
4. STS exchanges the ID token for a federated access token.
5. BigQuery is queried with that token alone, against two datasets — one per team — where each team's `principalSet` holds READER on its own dataset only.

Deliberately raw HTTP, no `google-auth`, no ADC, no key file, so that a positive result cannot be an artefact of an SDK falling back to ambient credentials.

## Result

Symmetric, stable across repeated runs:

```
alice (team-a)   team-a dataset  200 ALLOWED       team-b dataset  403 accessDenied
bob   (team-b)   team-a dataset  403 accessDenied  team-b dataset  200 ALLOWED
```

Same code, same process. Only the claim inside the token differs, and the platform reaches opposite conclusions. Denials are `accessDenied` from the BigQuery API — no branch in the calling process could have produced them.

## Findings

**1. The team claim survives into the ID token as a top-level claim.** Claims placed in a custom token's `claims` object appear at the top level of the resulting ID token, not nested. Attribute mapping can read them directly as `assertion.team`.

**2. A federated principal needs project-level roles before dataset ACLs are ever evaluated.** `roles/serviceusage.serviceUsageConsumer` and `roles/bigquery.jobUser` are both required. Without them the query fails with a *quota* 403, not an authorisation 403.

This is a trap for exactly this project. A missing project-level role produces a denial that looks like a successful control, and a test that only exercises the negative case would record a false pass. Any test asserting an L3 denial must assert on the **error reason** — `accessDenied` — and must be paired with a positive case proving the principal could reach anything at all.

**3. The grant is on the attribute, not the person — and that relocates the trust boundary.** A previously unseen user ID (`mallory`) presenting `team: team-a` was granted the same access as `alice`. This is correct attribute-based behaviour, but it means the guarantee reduces to: *whatever decides which team claim to mint*.

For this lab that is FR-3's persona picker. The PRD already states `bob` and `svc-platform` are "not selectable"; this spike shows that sentence is a load-bearing security control rather than a UI detail. If persona selection ever accepts a caller-supplied team — a query parameter, a header, a field in a request body — L3 collapses, and no IAM binding anywhere would catch it. The minting service is the root of trust.

**4. No ambient credential is used on the data path.** The operator's `gcloud` token appears only in hop 1, to authorise signing. The BigQuery calls carry only the STS-issued token. FR-14's "no ambient credential in process" holds on this path — though spike 0.4 still needs to establish whether the Cloud Run metadata server is *reachable*, which is a different question from whether it is used.

**5. Workload identity pool resources are eventually consistent.** A provider read immediately after creation returned `NOT_FOUND`, then succeeded moments later. IAM binding changes also took time to take effect. Terraform will hit both; expect flaky applies without retries.

**6. Deleting a pool soft-deletes it for 30 days and the ID cannot be reused.** The Terraform-managed pools must not reuse a name burned by a spike. One name was burned:

| Pool ID | State | Reusable from |
| -- | -- | -- |
| `bta-spike-pool` | soft-deleted | 2026-09-11 |

Soft-deleted pools do not appear in `gcloud iam workload-identity-pools list` without `--show-deleted`, so a name can be unavailable while looking free. Neither `github-ci` nor `arena-personas` collides with the above.

## Consequences

- **PRD:** the constraint implied by finding 3 — that the minting service never asserts a team the caller did not legitimately obtain, and that team is never caller-supplied — deserves to be explicit rather than implied by FR-3.
- **Phase 2:** tests asserting L3 denial must assert on error reason and pair with a positive control (finding 2).
- **Phase 1:** Terraform needs retry tolerance for pool and IAM propagation (finding 5), and a pool name distinct from the spike's (finding 6).

## SDK half — the code path phase 2 will use

The raw exchange working does not guarantee `google-auth` can express it, since external-account credentials are normally loaded from a JSON config file — precisely the thing FR-14 says must not exist.

It can. `google.auth.identity_pool.SubjectTokenSupplier` is subclassed to return the caller's JWT from memory, and the resulting `identity_pool.Credentials` are passed explicitly to `bigquery.Client(credentials=…)`:

```python
class InMemorySupplier(identity_pool.SubjectTokenSupplier):
    def __init__(self, token: str):
        self._token = token

    def get_subject_token(self, context, request):
        return self._token
```

Run with `GOOGLE_APPLICATION_CREDENTIALS` unset and `google.auth.default()` never called, so a pass could not be an artefact of ambient credentials. All four cases behaved identically to the raw-HTTP run:

```
alice (team-a)   team-a ALLOWED    team-b DENIED
bob   (team-b)   team-a DENIED     team-b ALLOWED
```

**7. The supplier is the seam FR-14 needs.** It is called per credential refresh rather than once, so a long-lived process holds no cached caller token beyond the credential's own lifetime, and the credential object is constructed per request from the caller's identity. This is the shape phase 2's tool boundary should take: the tool receives a client built for this caller, and has no way to obtain one for anybody else.

## Conclusion

FR-14's L3 credential design is implementable as specified. The blocking spike is cleared and phases 1 and 2 can proceed on that basis, subject to the consequences above.

## Evidence

Spike scripts are throwaway and are not committed (`build-plan.md` §1). Resources created by hand are listed in `TEARDOWN.md` and are removed before Phase 1.
