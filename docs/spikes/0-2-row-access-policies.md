# Spike 0.2 — Row access policies with a federated principalSet grantee

**Status:** answered — **yes**.
**Date:** 2026-08-12
**Requirements touched:** FR-17 (F1), FR-14

## Question

Does a BigQuery row access policy accept a `principalSet://` grantee — a workload identity pool attribute principal rather than a user, group or service account — and actually filter on it?

If no, F1 cannot be a row inside a shared table and becomes a per-team dataset instead, changing both the estate and the generator.

## Method

One shared dataset, one table, two rows with `owner_team` of `team-a` and `team-b`. Both teams' principalSets hold dataset READER, so table-level access is identical for both. Two row access policies:

```sql
CREATE OR REPLACE ROW ACCESS POLICY team_a_policy
  ON `<project>.spike_shared.incidents`
  GRANT TO ('principalSet://iam.googleapis.com/projects/<num>/locations/global/workloadIdentityPools/<pool>/attribute.team/team-a')
  FILTER USING (owner_team = 'team-a')
```

Queried via the federated credentials from spike 0.1, with identical SQL for both personas.

## Result

```
alice (team-a)  rows visible: ['INC-A']
bob   (team-b)  rows visible: ['INC-B']
```

## Findings

**1. `GRANT TO` accepts `principalSet://` and enforces it.** Both DDL acceptance and runtime filtering work. Attribute-based row-level security is available without naming any user or service account.

**2. This makes F1 a stronger flag than the fallback design.** With per-team datasets, an agent denied access learns only that a dataset exists and is closed to it. With a row access policy, the agent can query the table successfully, get results, and simply never see the row it is after — no error, no denial, nothing for a model to notice or narrate around. There is no refusal to social-engineer, because from the agent's perspective nothing was refused.

That is a materially better demonstration of the project's claim: the control is invisible to the agent rather than presented to it as an obstacle.

**3. DDL acceptance is not enforcement.** The policy creation succeeded before any query was run. Testing only that `CREATE ROW ACCESS POLICY` returns success would have proven nothing. The verdict rests on the two queries.

## Consequences

- **F1 is planted as a row in a shared incidents table**, protected by a row access policy on `owner_team`, not as a separate dataset.
- The estate generator plants one table containing both teams' rows, which is closer to how a real incident store looks.
- Phase 2's `get_incident` and `search_deploys` tools can query the shared table unconditionally; the filtering is the platform's job, and the tool contains no team logic at all. That is the point.

## Evidence

Spike scripts are throwaway and not committed. Resources created by hand are listed in `TEARDOWN.md`.
