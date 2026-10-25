# bta.tools

The nine tools (FR-9, FR-10 — `search_incidents` was added 2026-08-29 as an
FR-9 amendment, after Phase 2's original eight), the boundary that calls them,
and the client dispatch that makes a level mean something.

There is no agent here. Phase 2's goal is tools callable and audited with
nothing model-shaped in the picture, and the import contract in
`pyproject.toml` keeps it that way: `bta.tools` may not import `bta.agent`, in
either direction, and may not import `bta.generator` either — the generator
holds live flag values and `schemas.md` §3 says no tool may reach them.

---

## The shape

```
invoke(spec, ctx, arguments, sink=…)          boundary.py   — audits, always
  └── spec.fn(ctx, **arguments)               deploy_bot.py / onboarding.py
        └── ctx.client(Surface.BIGQUERY)      context.py
              └── EstateClients.for_surface   clients.py    — the level decides
                    └── CredentialFactory     bta.credentials
                          └── mode_for(level, surface)      — the experiment
```

Read `bta/credentials/policy.py` first. It is a table of twelve entries — four
surfaces at three levels — and it is the independent variable of the whole project: everything else about the lab is
held constant across levels (FR-13), and that table is what changes.

---

## Four properties worth stating, because tests assert each one

**A tool names a surface, never a credential mode.** `ctx.client(...)` takes a
`Surface` and resolves the mode from the level on the context. There is no
argument that selects a credential, so there is no argument a model can
influence that selects one. `tests/test_credential_policy.py` asserts the
factory never returns the shared estate credential at L3 — which is the claim
`infra/estate/identity.tf` explicitly defers to this phase.

**A tool cannot emit an audit record, or forget to.** VER-4 says every call is
audited including denied ones. `ToolContext` carries no sink; `invoke` is the
only path that can call a tool, and it writes the record on every path
including the ones where the tool raised — including `KeyboardInterrupt` and
`SystemExit`, which are recorded and then re-raised rather than swallowed, so
an operator's Ctrl-C during a suite run still stops the run. A tool that wanted
to audit itself has nothing to reach for.

**A denial is only recorded when it can be attributed, per surface.**
`errors.classify` maps a provider failure to `denied_iam`, `denied_capability`,
`denied_document` or — by default — to no authorisation outcome at all. The
accepted reasons are scoped per `Surface` rather than kept in one set:
`forbidden` is Cloud Storage's reason for a genuine object ACL denial, and is
*also* what BigQuery returns for "caller does not have required permission to
use project X" — a missing project-level role, which says nothing about whether
the caller was entitled to the data. BigQuery uses `accessDenied` when
authorisation is what refused, so declining to honour `forbidden` on that
surface loses nothing. One shared set recorded that
missing role as `denied_iam`; the per-surface table is what closes it. The
reason itself is read structurally rather than by message match, walking
`reason`/`status`, the legacy `errors[]` list, and — for the Firestore server
SDK and Secret Manager, whose gRPC exceptions carry `grpc_status_code` rather
than a `code()` that resolves it — the gRPC status code's name. A 403 whose
reason cannot be read this way is an error, not a denial. Spike 0.1 finding 2
is why: a federated principal missing a project-level role fails with a
*quota* 403 that looks exactly like a working control, and would record a
false `held` in every L3 BigQuery cell.

**`Level` is checked at both ends of the seam, not once.** `ToolContext` and
`EstateClients.for_surface` each call `require_level` independently, because
`Level` is a `StrEnum`: a plain string compares equal to a member but fails
`is Level.L3`, which is exactly the dispatch both the credential policy lookup
and the client dispatch use. A level arriving as an unchecked string would
resolve to `federated` in the policy table (dict lookup, `==`) while taking the
L1/L2 branch in `clients.py` (`is`) — correct-looking data, an audit record
reading `credential_mode: federated`, and the L3 document control silently
gone. `EstateClients.for_surface` is reachable without going through a
`ToolContext` at all, which is why it repeats the check rather than trusting
the caller to have made it.

---

## Where the levels actually differ

All of it is in `clients.py`. The tool bodies contain no level and no branch on
one, which is what makes FR-13's claim true of the code rather than of the
intention.

| Surface | L1 / L2 | L3 |
| -- | -- | -- |
| BigQuery, Cloud Storage | shared estate credential, estate-wide read | federated, bound to `attribute.team` |
| Firestore | server SDK — **not subject to Security Rules at all** | caller's own token against the REST API |
| Secret Manager | this process reads the key; failures quote it | provisioning identity reads it; outcome only |
| Write path | any role accepted | capability-scoped, `denied_capability` |

The Firestore row is the one to watch. Spike 0.3 finding 2: a tool handed a
server client at L3 keeps returning correct-looking data with the entire
document-level control gone, and nothing in its behaviour, its result or its
audit record would say so. `tests/test_estate_clients.py` exists for that row.

The L3 Firestore REST path is why `requests` is a direct dependency of this
package rather than one it rode in on. It was already present transitively
behind the Google client libraries; `clients.py` now imports it by name for
that one path, on the principle that a direct import resting on somebody
else's transitive dependency breaks the day that dependency drops it.

---

## The harness

```sh
uv run python -m bta.tools.harness --level L1
```

**Run against a live estate on 2026-09-15, at all three levels** (TD-11). Every
positive control was `allowed`; credential mode was `shared` at L1 and L2 and
`federated`/`service` at L3; denials arrived attributed as `denied_document`
(Firestore rules) and `denied_capability`. One Cloud Storage negative control
returned an unattributable `Forbidden` and was correctly *not* scored as a
denial — the per-surface rule above doing its job.

**It needs three hand-made `gcloud` grants** that Terraform does not make: the
estate grants impersonation of its service accounts to the runtime identity
alone, so an operator's account cannot mint them until granted. That is a live
gap in NFR-5 and the README says so.

It also needs the deployment inputs in `bta/config.py`, fixture ids to probe
with (`BTA_HARNESS_OWN_*` and `BTA_HARNESS_OTHER_*` — supplied by the operator,
because the harness may not read the fixture manifest to find them), and at L3
an Identity Platform ID token for a persona in `BTA_HARNESS_SUBJECT_TOKEN`,
which `scripts/mint-persona-token.py` prints.

Every probe is paired: `alice` reads her own team's incident and then another
team's, her own artefact and then another team's. A run where every call denies
is the failure both spikes produced, and it is indistinguishable from a working
control unless the positive case is in the same run — so the harness exits
non-zero when a positive control fails, rather than printing a table for
somebody to misread.

---

## What the offline tests do not cover

No test in the repository constructs a `bigquery.Client`, a `storage.Client`, a
server-SDK Firestore client or a Secret Manager client. The fakes in
`tests/fakes.py` enforce the same rules the estate's Terraform does — row
access policies, the bucket's conditional bindings, `firestore.rules` — but
they are not those mechanisms, and passing tests are not evidence that the
estate is configured correctly. That evidence is the harness's to produce, and
it has to be produced again for every new deployment.
