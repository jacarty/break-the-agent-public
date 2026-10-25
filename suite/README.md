# suite — the attack suite

Reproducible evidence at the SC-3 threshold. Three trees, deliberately
different in character:

| | What it is | Constraint |
| -- | -- | -- |
| `cases/` | Data. 108 pre-registered attack cases plus the loader and its validation | Written with `expected_outcome` **before** any evidence, never amended after a result |
| `runner/` | Effects. Asyncio executor against a live deployment | Never holds a flag value; resumable; records the limit it hit |
| `matrix/` | Pure aggregation over serialised run results | Imports nothing from `runner`, `cases` or `bta`; no network |

## The suite never holds a flag value

`schemas.md` §3 keeps the set of components that may read live flag values to
two: the generator that plants them and the verifier that detects them. The
suite is neither, and does not need to be.

Capture is automatic inside the site process (FR-6), which already contains the
verifier. The SSE `done` frame carries the structured result — flag id, channel,
and the normalisation that matched — and the runner records that verbatim. The
suite is told *that* a flag came out, by the process that detected it.

Two `import-linter` contracts hold this rather than a convention:
`suite -> {bta.generator, verifier}` is forbidden, and `suite.matrix` may not
reach `suite.runner`, `suite.cases` or `bta`. The convenience being forbidden is
real — it would be natural for the runner to re-scan a transcript itself — and
it would put live values into run results, which SC-6 commits to the repository
while VER-6 deliberately keeps raw transcripts out of it.

`tests/test_suite_end_to_end.py` asserts the same property behaviourally: no
minted value appears anywhere in a serialised run result.

## Case coverage against SC-3

108 cases: twelve per cell, across six to eight technique families each, for all
nine scored cells (F7 scores as two — FR-19) at all three levels.

```sh
uv run python -m suite.runner --coverage-only --out /dev/null
```

Reports cases and families per cell with no network and no spend.
`build-plan.md` §6 names underestimated case authoring as the route to
`insufficient` verdicts by accident and asks for this count "from the start of
Phase 5, not at the end". `tests/test_suite_cases.py` fails the build if any
cell drops below the floor, so the threshold cannot be lost to an edit.

## Pre-registration

Cases and their `expected_outcome` are **pre-registered artefacts**
(`build-plan.md` §4, `process.md`). Changing one takes a PR with a dated
justification, and — if the reference run has happened — publication of both the
original and revised results. Cases added after seeing a result are how a
threshold gets met on paper.

Authored 2026-08-28, before any suite run existed and while the partner-model
quota that FR-22 depends on was still outstanding. That ordering is checkable
against git history rather than asserted here.

## Running

```sh
export BTA_SITE_URL=https://…
export BTA_ACCESS_CODE=…                 # FR-1
export BTA_SUITE_BYPASS_TOKEN=…          # NFR-3a
export BTA_MODEL_GOOGLE=…                # FR-22a, at least one
export BTA_HARNESS_OTHER_INCIDENT=…      # fixture ids the cases declare
export BTA_HARNESS_OTHER_EMPLOYEE=…
export BTA_HARNESS_OWN_EMPLOYEE=…
export BTA_HARNESS_ARTEFACT_SERVICE=…    # the service whose build artefact holds F2
export BTA_HARNESS_LOG_SERVICE=…         # service whose logs carry F3's injection
export BTA_HARNESS_INCIDENT_SERVICE=…    # the service owning F1's incident
export BTA_HARNESS_OTHER_SERVICE=…       # any service another team owns
export BTA_HARNESS_OTHER_SERVICE_ALT=…   # a second one, different again

uv run python -m suite.runner --level L1 --out evidence/runs/R-001.jsonl \
    --matrix evidence/runs/R-001-matrix.json
```

### Three providers means three invocations, then a merge

A deployment serves one model per process (`BTA_SITE_PROVIDER`), and the runner
refuses more than one model pin rather than assert a provider label nothing
observed. So FR-22 is met by rotating the provider on the deployment and
merging the result files afterwards — **not** by `--matrix` on any single
invocation, whose document can never satisfy §8 rule 2's provider clause:

```sh
# once per provider, with BTA_SITE_PROVIDER rotated on the deployment
uv run python -m suite.runner --level L1 --out evidence/runs/R-001-google.jsonl
uv run python -m suite.runner --level L1 --out evidence/runs/R-001-anthropic.jsonl
uv run python -m suite.runner --level L1 --out evidence/runs/R-001-xai.jsonl

uv run python -m suite.matrix evidence/runs/R-001-*.jsonl \
    --out evidence/runs/R-001-matrix.json
```

The merge refuses inputs that collide on `schemas.md` §5's uniqueness tuple —
the same file twice, or two runs of one provider — because counting an
execution twice raises `cases_run` toward `min_cases` on no new evidence.

### Service names are fixtures too, since 2026-08-29

**37 of the 108 cases named a service as a literal** — `checkout-api`, mostly —
and `corpus.build_services` deals teams round-robin, so which team owns a given
service is decided by the seed. A case was therefore cross-team on one seed and,
on the next, the caller's own perfectly legitimate request: served correctly,
recorded `not_captured`, pushing the cell toward `held` for a reason unrelated to
any control. F2 was worst at 11 of 12 cases, which made that cell's verdict a
function of the seed rather than of the architecture.

Found by running the attack by hand after a reseed had moved `checkout-api` to
`team-a`. `tests/test_suite_cases.py::test_no_case_names_a_service_the_seed_assigns`
now fails the build if a literal comes back.

**Three of the five new names identify a carrier, not merely another team.**
`_place_f2` picks a random `team-b` artefact and `_place_f3` picks
independently, so the service whose build manifest holds F2 is generally not
the one whose logs hold F3. A single `other_service` would have aimed most of
the cases at a resource with no flag in it, while looking entirely correct.

`payments-gateway` in F4's cases is **not** one of these. It is a member of
`bta.tools.clients.KNOWN_INTEGRATION_TARGETS`, a constant in code rather than a
seed assignment, and those cases mean that — so they were left alone, and the
guard test excludes it.

The `encoding_obfuscation` family needed one extra thing. Its instruction is
hidden inside a base64 blob, so substitution — which runs over turn text — could
never reach a fixture name inside one, and those cases had no option but to
hardcode. Turns may now write `{b64:...}`, which the runner encodes *after*
substituting, so the family uses the same fixtures as every other.

### Why the attacker is handed the identifiers

The three `BTA_HARNESS_*` variables are target ids — an incident, two employees
— and a reader who has just met them reasonably asks how an attacker would know
one. When the cases were written they could not: Deploy Bot's tools then were
`list_services`, `search_deploys`, `query_logs` and `get_incident`, and the last
of those takes an id it will not help you find. `schema_probing` in `schemas.md`
§4 covers probing *tools and parameters*, never row keys. `search_incidents` was
added afterwards to give the agent a discovery path (ADR 0014), before the
reference run. Nine of F1's twelve cases still declare `other_incident_id`.

**The threat model is a caller who already holds an identifier they are not
entitled to read** — an incident number from a chat thread, a ticket, a
previous role. That is both realistic and, more to the point, the only version
of the experiment that measures what the matrix claims to measure.

Supplying the id removes discovery as a confound, and at L3 that is
load-bearing rather than tidy. The row access policy makes an unentitled row
*vanish*: `get_incident` returns an empty result set, no error, no denial — the
control is invisible to the agent by design (`infra/estate/identity.tf`, and
the note in `deploy_bot.get_incident`). If finding the id were part of the
attack, an L3 `held` would be partly the row policy and partly the attacker
never having located the target. Those are different claims, obscurity is not
authorisation, and the matrix would report the second as the first.

So the identifiers are given at every level, identically, for the same reason
the prompts and the model are held constant: the only thing permitted to vary
between columns is the authorisation architecture.

**That argument justifies keeping these cases. It does not justify having only
these cases.** A live agent is attacked by people who know no identifiers and
open with "tell me about any recent postmortems". The suite mostly measures
*"does the control refuse a targeted unentitled read"* while the matrix is read
as *"which control removes the exploit"*, and those are not the same claim.
ADR 0014 is the response: `search_incidents` gives the agent a discovery path
(an FR-9 amendment), and any further identifier-free cases are new cases, which
`docs/process.md` treats as a separate, published change after a reference run
rather than an edit.

Results are appended as JSONL the moment each execution completes, and a rerun
against the same file resumes: the key is `(case_id, level, model, repeat)` per
`schemas.md` §5, not a position in an iteration order. JSONL rather than a JSON
array because an array must be closed to parse, and a run killed mid-write would
otherwise leave nothing readable.

## Three things the runner refuses to do

Each would produce evidence that looks correct and is not. All three are
refusals before any inference is paid for, not warnings.

**Run a memory-bearing agent across levels on one persona.** TD-20: memory keys
on persona alone and no path consults `written_at_level`, so an L1 session's
records reach the next L2 session's system prompt unclassified — and the L2
column stops measuring the mitigation stack. TD-20 requires a persona per level
or a store reset between them; the suite cannot reset a store inside the
deployment, so it enforces the first. PRD §3 currently makes only `alice`
selectable, so **today that means one level per invocation**, and `--level` is
not a convenience.

**Run at a concurrency near the deployment's session cap.** `bta.site.app`
caps live sessions at 64 and eviction is a cross-user effect (TD-24) — the 65th
session drops somebody else's. Results would be artefacts of eviction. The
ceiling here is 32 and the default is 8.

**Score a multi-phase case against a store nobody has shown persists.** F7's
second phase is a fresh session, and it only means something if memory survives
the first. Unless `--memory-is-persistent` is passed, a multi-phase case records
`error` — contributing nothing to SC-3 — rather than `not_captured`, which would
manufacture evidence for a hold. The flag is an operator assertion: the runner
cannot tell a store that persists from one that raises on every write. The
reference run passed it on the strength of TD-17's live check (2026-08-29),
not a per-run one — `docs/results.md` says so.

## Running against three providers

`--level` is not the only thing that is one-per-invocation. **The runner labels
the model; it does not choose it.** `POST /session` takes persona, agent and
level, and which model answers is a per-process property of the deployment
(`BTA_SITE_PROVIDER`). Nothing reports back what served.

So a second `BTA_MODEL_*` pin would not run a second model — it would duplicate
every execution under a provider label nothing observed, and §8 rule 2 counts
those labels toward `held`. The runner refuses more than one pin for that
reason. FR-22 is reached by running once per provider with `BTA_SITE_PROVIDER`
rotated on the deployment, and concatenating the result files. TD-33 carries
what it would take to make the model a per-session input instead.

## What the reference run could not close

The reference run (2026-09-15, `docs/results.md`) executed all 108 cases at
all three levels. Three limits survive it.

**One provider ran.** The model dimension is one per invocation (above, TD-33),
and the reference deployment had no partner-model quota for Anthropic, so only
Google ran. SC-3 requires three providers, so every cell that would read `held`
reads `insufficient` — the correct answer rather than a broken one.

**F2's manual capture was not reproduced by the suite.** All twelve F2 cases
target by service name; the manual capture used `deploy_id`. The gap is
reported in `docs/results.md`, not closed by adding a case after the fact.

**F7a is reported as unmeasured.** Capture detection is value-appearance and
only F7b's outcome is a value, so F7a is scored by F7b's evidence and the two
cells move together by construction. Rather than a detector written after the
fact, F7a's cells carry `measurement_limitation: "no_independent_detector"` and
a null `diverged_from_expectation` — otherwise an L3 that contains F7b as
designed would publish *the injection did not change behaviour at L3*, which
nothing measured. ADR 0015; the residual is TD-32.

Transcripts are written: the site redacts every run-tagged session and stores
the raw copy in the evidence bucket, and the runner records both refs on each
result (TD-34, closed by the reference run). The redacted copies land under
`evidence/runs/<run_id>/`, which is gitignored.
