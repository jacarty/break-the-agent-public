# 0011 — Site state is bounded in process, not moved to a shared store

**Date:** 2026-08-27
**Phase:** 4 (post-merge fix, before the first apply)
**Status:** accepted
**Requirements:** FR-1, FR-2, FR-7, NFR-3, POR-4
**Related:** TD-24, TD-25, TD-27

## Decision

`Arena.gated` and `Arena.sessions` become age- and size-limited maps
(`bta.site.state.TTLMap`) held in the same process they were already held in.
`POST /gate` acquires a rate limit — per client address per minute, per client
address per hour, and a global hourly ceiling — counted in `bta.site.quota`
alongside the NFR-3 limits but in its own buckets.

Site state is **not** moved to Firestore. TD-24 stays open for what remains of
it after this: a browser session is only coherent if it lands on the same
instance, and NFR-3's counters are per instance, so the effective limit is the
configured one multiplied by `max_instance_count` (4). Unbounded growth stops
being part of TD-24.

## Why

TD-27 and TD-24 describe one structure and two different faults, and TD-27 said
to decide them together rather than in isolation. Doing so separates them.

**Unbounded growth is a defect. Per-instance state is a design consequence.**
The first is an internet-reachable endpoint that adds a permanent entry to a
dictionary in a 2 GiB container with `min_instance_count = 1` and
`cpu_idle = false`, so nothing ever resets it. The second is a property FR-7
already accepts — no persistence between browser sessions — with a cost that
was written down when it was chosen. Only the first has to be fixed before the
service is exposed, and fixing it does not require the shared store the second
needs.

**A shared store is a phase 5 decision and taking it here would take it
badly.** Firestore is in the estate, so the mechanism exists, but using it for
site state couples `infra/hosting/` to `infra/estate/`, which POR-4 keeps
apart. It is also the same question TD-25 has to answer for the audit sink —
where raw records land, under what IAM, and whether Cloud Logging receives the
redacted copy (ADR 0008) — and the suite is the first consumer that needs both
answers. Answering half of it now, for the half of the problem that is not the
defect, is how the other half gets answered by default.

**The gate limit is a budget control and is not claimed as anything else.**
FR-2 is right that the access code is a shared secret rather than
authentication, and this does not change that. What it changes is that the one
unauthenticated endpoint on the service could be driven at network speed, and
that endpoint is what stands between the open internet and a daily token
ceiling. The limit lives in `quota.py` — the existing machinery, per the same
argument NFR-3a makes about not having a second rate control to reason about —
but in separate buckets, because a gate attempt that spent a persona's
interactive allowance, or that was refused because the *model* budget for the
day was gone, would be a limit doing something other than what it says.

## What was considered and rejected

**Refusing new entries when a map is full**, rather than evicting the oldest.
It protects a legitimate operator from being evicted mid-use, and in exchange
anyone who fills the map locks everyone out of the front door until entries
expire. That converts bounded memory into a clean denial of service, which is a
worse failure than "enter the code again".

**Keying the gate limit on the leftmost `X-Forwarded-For` entry.** Cloud Run
appends the address it observed to whatever the client sent, so the leftmost
value is attacker-controlled: a caller supplying a fresh header per request
would get a fresh bucket per request, and the limit would be decorative.
Ingress is `INGRESS_TRAFFIC_ALL` with no load balancer, so the rightmost entry
is the one the platform wrote. A load balancer added later would collapse every
caller into one bucket — restrictive rather than open, but it needs
`_client_key` changed rather than left to be discovered.

**Per-address limits without a global ceiling.** Address rotation is cheap and
would leave guessing effectively unbounded. The global ceiling has a real cost
— anyone can spend it, and while it is spent the operator is refused at the
front door too — so it is defaulted high enough (600/hour) that ordinary use
cannot reach it and low enough that automated guessing is three orders of
magnitude off what an open endpoint offers.

**Making the map ceilings deployment inputs.** They are properties of one
container's memory, not of a deployment (POR-6), and an operator tuning them
would be tuning the wrong thing: the limits that protect the budget are the
NFR-3 three. The gate limits *are* Terraform variables, following the existing
three exactly — plain variables with defaults, overridable by `TF_VAR_*`, not
Secret Manager material, because a rate limit is not a credential.

## Consequences

- Memory growth on the site is bounded by construction: 512 gate tokens, 64
  browser sessions, and gate buckets whose key count is itself bounded by the
  global hourly ceiling. That last clause is only true because the bucket sweep
  runs over the whole window rather than deleting already-empty lists — the
  first version of it reclaimed nothing, since a list empties only for the key
  currently making a request, and a one-shot address would have been retained
  for the life of the instance. Creation rate is not residency.
- **The global ceiling is global to one instance.** With `max_instance_count`
  at 4 an attacker spread across instances gets four uncoordinated buckets, so
  the effective ceiling is up to four times the configured value. The
  per-address limits degrade proportionally under the same weakening; this one
  was supposed to be the single deployment-wide backstop and is not one until
  TD-24's shared store lands.
- **Session eviction is now a cross-user effect.** Before this, session churn
  only grew memory; the 65th `POST /session` now evicts someone else's live
  session, and `/session` carries no limit of its own beyond the gate. Harmless
  for phase 4's exit criterion — one person, one browser — but phase 5's suite
  could plausibly exceed 64 concurrent sessions, and would then see sessions
  vanish mid-run and score results that are artefacts of eviction rather than
  of a control. `MAX_SESSIONS` is a phase-5 precondition, recorded in TD-24.
- A gate token can no longer outlive the cookie that carries it — one field
  feeds both the TTL and the cookie's `max_age`.
- Sustained gate churn by someone holding the access code can evict an
  operator's token. Recovery is entering the code again.
- TD-27 closes. TD-24 narrows to session coherence and per-instance counters,
  and remains open with TD-25 for phase 5.
- Three new `infra/hosting/` variables, defaulted, on a module that has never
  been applied — so there is no drift to reconcile at the first apply.
