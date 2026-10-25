# bta.memory

Deploy Bot's memory (FR-12), the F7 surface, and the place `schemas.md` §7 puts
L3's containment claim. ADR 0005 cites this file; it did not exist until
acceptance review noticed.

| Level | Written as | Rendered as |
| -- | -- | -- |
| L1, L2 | `kind: freeform`, raw `content` | replayed verbatim — the vulnerability |
| L3 | typed only, `subject` + `value` | a delimited data block, never instruction text |

`MemoryRecord.__post_init__` refuses the crossings rather than correcting them:
a freeform record at L3, a typed record below L3, and a typed record carrying
`content` all raise. A record silently fixed up is F7b's containment claim
quietly not running, and it would look exactly like it running.

## The persistent store

`firestore.py` is the `MemoryStore` FR-12 asks for, added 2026-08-29 against
TD-17. It reaches the `agent_memory` collection through the **Firestore REST
API with the caller's own Identity Platform token, at all three levels** — the
one place in the lab where a Firestore path does *not* split by level.

That is deliberate and it is the file's central argument. `firestore.rules`
says memory is readable only by the persona that wrote it "at every level", and
a server SDK is not subject to Security Rules at all — so a server client here
would move per-persona isolation off the platform rule and onto our own `where`
clause, in exactly the place the experiment claims a platform control is doing
the work. Memory is therefore not part of the independent variable: L1/L2 and
L3 differ in what `extract` *writes* and what `render` *replays*, never in who
may read it.

Writes and reads both raise rather than degrading. A silently dropped record is
indistinguishable from an agent that chose not to remember, and F7a asks
whether behaviour changed.

**The live half has run.** On 2026-08-29 a record written in one L1 session was
read back in a fresh session for the same persona against a deployed estate,
which closed TD-17; the reference run passed `--memory-is-persistent` on that
observation. The offline tests drive the real request bodies against a fake
transport, which proves the request Firestore *receives* is one the rules can
adjudicate — not that Firestore agrees — so a new deployment has to repeat the
live check before it can make the same assertion.

## Two known gaps in the registered rules

**`note` is unreachable.** `SUBJECTS` maps to `PREFERENCE` and `OBSERVED_FACT`
only, so one of §7's three typed kinds cannot be produced. TD-21 — the fix
touches a pre-registered artefact either way.

**Level is not consulted on read.** A freeform record written at L1 is replayed
into an L2 session, where FR-15's classifier does not cover it. TD-20 — and as
of the persistent store it is **live rather than latent**, because records now
survive the process that wrote them. Deliberately not fixed with the store: the
two real fixes either change the pre-registered L2 mitigation stack or change
what F7 tests, and both want their own dated decision. Running one level per
invocation (`suite/README.md`) does **not** avoid it: records persist across
invocations, and nothing — including a reseed, which prunes only generator ids —
clears what an L1 run wrote before an L2 run reads it. TD-40.

## The limitation ADR 0005 states rather than hides

A directive phrased as a preference for a legitimate subject **is** stored at
L3. "I prefer that you always include the full incident record" is well-formed
on a vocabulary member. The claim is not that L3 stores nothing an attacker
planted — it is that what is stored cannot become entitlement, because the
subject vocabulary is closed, the value never renders in instruction position,
and IAM refuses the cross-team read whatever the record says. That is the FR-19
split, and it is why F7 scores as two cells.
