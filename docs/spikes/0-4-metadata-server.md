# Spike 0.4 — Metadata server reachability from the agent runtime

**Status:** answered — **reachable, and by default it defeats L3 entirely.**
**Date:** 2026-08-12
**Requirements touched:** FR-14, SEC-5, SEC-4

## Question

FR-14 describes L3 as having "no ambient credential in process". On Cloud Run, is the instance metadata server reachable from the agent process — and if so, what can the credential it hands out actually do?

## Method

A Cloud Run job running a stock `cloud-sdk` image, with **no service account specified** — the default a deployment gets if nobody thinks about it. It queried the metadata server for the runtime identity, requested an access token, listed the token's scopes, and then attempted to read the estate with it.

## Result

```
identity:            000000000000-compute@developer.gserviceaccount.com
token endpoint:      HTTP 200
scopes:              cloud-platform, plus a broad set of Workspace scopes
BigQuery datasets:   HTTP 200   <-- the estate, read with the ambient credential
```

## Findings

**1. The metadata server is always reachable and cannot be disabled.** It is link-local infrastructure on Cloud Run. There is no configuration, network policy, or egress control that removes it. Any code in the process — including code an injection persuades the agent to run, if such a path ever existed — can obtain the runtime's credential with a single unauthenticated HTTP call.

**2. FR-14's wording is wrong as written, and needs correcting rather than defending.** "No ambient credential in process" is not achievable on this platform. What spike 0.1 demonstrated is narrower and still true: *the credential path used to reach the estate is derived per request from the caller's identity, and no ambient credential is used on it*. But an ambient credential is always **present**, and the difference matters, because a control that depends on a credential being unreachable is not the control we actually have.

**3. The real control is that the runtime identity must be powerless over the estate.** Since the credential cannot be made unreachable, its *authority* is the only variable. This is the same argument the project makes about agents and secrets, turned on the lab's own infrastructure: do not rely on something not being obtained, rely on it being worthless when obtained.

**4. The default is catastrophic for this design.** Deploying without specifying a service account attaches the default compute service account, which historically carries project Editor and, as measured here, reads the estate immediately. A deployment that got everything else right and forgot this one field would show L3 holding under normal operation while being one HTTP request from total bypass — and every result in the matrix for L3 would be worthless.

**5. This is a plausible undetected failure.** Nothing in a passing test would reveal it. The agent's tools would use federated credentials correctly, denials would look correct, and the ambient path would simply never be exercised by any attack the suite runs. It is precisely the class SC-4 calls `absent` — a control the design specified that the implementation did not apply.

## Consequences

- **FR-14 must be restated** to claim what is true: no ambient credential is used on the data path, and the runtime identity holds no estate permissions.
- **A new requirement is warranted**: the Cloud Run runtime identity is a dedicated service account with no estate access, the default compute service account is never used, and this is asserted rather than assumed.
- **A test belongs in the suite**, not just in Terraform review: fetch a token from the metadata server inside the running service and assert it is refused by BigQuery, Firestore and GCS. That converts an invisible misconfiguration into a failing test.
- **Phase 1** must create that dedicated runtime identity, and Phase 4 must deploy with it explicitly.

## Evidence

Cloud Run job `bta-spike-metadata`, listed in `TEARDOWN.md`. Spike scripts are throwaway and not committed.
