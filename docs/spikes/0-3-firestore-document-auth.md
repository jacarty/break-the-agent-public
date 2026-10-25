# Spike 0.3 — Document-level authorisation via Firestore Security Rules

**Status:** answered — **yes**.
**Date:** 2026-08-12
**Requirements touched:** FR-14 (L3 Onboarding), FR-17 (F5), POR-3

## Question

Can Firestore deny a single document to a caller based on a claim in that caller's own token, without any application code making the decision?

If no, F5's L3 control degrades to per-collection IAM — a weaker demonstration that would have to be labelled as such rather than quietly substituted.

## Method

Firestore in **Native mode** (required for Security Rules; the mode is permanent per database). Two documents in `employees`, owned by `team-a` and `team-b`. Rules:

```
match /employees/{doc} {
  allow read: if request.auth != null
              && request.auth.token.team == resource.data.owner_team;
  allow write: if false;
}
```

Reads issued against the Firestore REST API carrying the persona's Identity Platform ID token from spike 0.1, plus a control read using the operator's Google credential.

## Result

```
alice -> emp-a  200 ALLOWED        alice -> emp-b  403 PERMISSION_DENIED
bob   -> emp-a  403 PERMISSION_DENIED   bob -> emp-b  200 ALLOWED
admin -> emp-a  200 ALLOWED        admin -> emp-b  200 ALLOWED   (rules bypassed)
```

## Findings

**1. The custom `team` claim is available to rules as `request.auth.token.team`.** The same claim that drives IAM attribute mapping in spike 0.1 drives document-level rules here, from one token. Two different enforcement systems, one identity assertion.

**2. Server credentials bypass rules completely — this is the architectural constraint.** The admin control read both documents. Firestore's server SDKs authenticate as a service and are not subject to Security Rules at all. F5's L3 control therefore **cannot** use the server SDK: it must go through the REST API with the caller's token.

The consequence for Phase 2 is concrete. `get_onboarding_status` and any other Firestore-backed tool run on a *different client path* from the BigQuery and GCS tools — caller-token REST rather than federated credentials. The tool signature stays identical, but the injected client differs, and a tool that accidentally receives an admin Firestore client silently loses the entire L3 control while continuing to return correct-looking data. That is a silent failure mode and deserves an explicit test asserting a denial, not just a test asserting the happy path.

**3. Rules propagation is slow enough to produce a false pass.** Ten seconds after publishing, *every* read was denied — including the ones that should have succeeded. Had the test only asserted denials, it would have recorded a pass while the rules were not yet in effect. The same trap as finding 2 of spike 0.1, from a different cause.

Any test of an L3 denial must be paired with a positive case in the same run. A denial on its own is not evidence of a control; it is equally consistent with a broken configuration.

**4. Writes are denied to callers outright.** `allow write: if false` means personas cannot mutate employee records through this path at all, regardless of team. Provisioning happens through the capability-scoped write path instead (F6), not through Firestore directly.

## Consequences

- **F5's L3 control is document-level Security Rules over caller-token REST**, as originally designed. No degradation needed.
- **Phase 2** injects a distinct client for Firestore-backed tools, and the test suite must assert a denial with a paired positive.
- **Phase 1** must create the database in Native mode; the choice is permanent per database.

## Evidence

Spike scripts are throwaway and not committed. Resources created by hand are listed in `TEARDOWN.md`.
