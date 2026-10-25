# break-the-agent — Build Plan

Status: draft. Requirements are in [`prd.md`](./prd.md). Data contracts are in [`schemas.md`](./schemas.md). Narrative is in [`overview.md`](./overview.md).

This document sequences the build. It does not restate requirements and it does not redefine schemas. Where it makes a design choice, it names the requirement the choice serves, so a reader can check the choice against the constraint rather than against taste.

Phases are ordered by what can invalidate what. A phase exists as a separate phase only if its failure would change the design of a later one.

---

## 1. Principles

These shape decisions throughout and are worth stating once rather than re-arguing per phase.

**Falsifiability first.** The deliverable is a matrix that could come out against us. Anything that makes a `held` verdict easier to obtain than it should be is a defect, including conveniences that look like good engineering. The L2 screener is the sharpest instance: it decides several cells, and it is tuned by us.

**Pre-registration over post-hoc justification.** Artefacts that could be tuned to flatter a result are committed, versioned and dated before the reference run. Enumerated in §4.

**Structural separation over discipline.** VER-3 (verifier and screener must not share code or configuration), FR-16 (screener ignorant of flag syntax) and POR-3 (tools import no framework) are all constraints that a directory layout plus an import-graph test can make *hard to violate*. Relying on remembering is how they get violated in month three.

**The credential seam is the interesting code.** OD-1 chose no framework precisely so the seam between agent and tool stays legible. Abstractions that obscure where authority is bound are net negative here even when they are otherwise good abstractions.

**Spikes are throwaway.** Phase 0 code is not the beginning of the implementation. It answers a question and is deleted, leaving a written finding. Spike code that survives into the product carries assumptions nobody re-examined.

---

## 2. Repository layout

Proposed. The rationale column is the point; the names are negotiable.

```
docs/                    prd, schemas, overview, this plan, spikes/ findings, decisions/
flags/                   flag definitions, no values (schemas §2)
infra/
  bootstrap/             one-time: project, WIF pools, state bucket
  estate/                BigQuery, GCS, Firestore, Secrets, Logging   (POR-4: no hosting coupling)
  hosting/               Cloud Run, Identity Platform, budget kill path
src/bta/
  config.py              env-only settings                            (POR-5)
  identity/              getCallerIdentity, the single identity read  (POR-1)
  credentials/           shared | federated | service minting         (schemas §1.4)
  tools/                 plain functions, no agent imports            (POR-3, FR-11)
  agent/                 the thin loop                                (OD-1)
  providers/             one protocol, three SDKs                     (FR-22, POR-2)
  levels/                prompt composition + L2 mitigation stack     (FR-13, FR-15)
  memory/                typed and freeform records                   (schemas §7)
  audit/                 tool call audit records                      (VER-4, schemas §6)
  screener/              L2 output screening. Knows nothing of flags  (FR-16)
  models/                pydantic transcriptions of schemas.md
  site/                  the Cloud Run app
  generator/             estate seeding, emits fixture manifest
verifier/                capture detection. Separate top-level tree   (VER-3)
suite/
  cases/                 attack cases as data                         (schemas §4)
  runner/                asyncio executor, emits run results
  matrix/                pure aggregation over run results            (schemas §8)
spikes/                  phase 0, deleted after findings are written
tests/
```

Two placements are deliberate rather than conventional.

**`verifier/` sits outside `src/bta/`.** VER-3 says the verifier and the screener are the same mechanism pointed in opposite directions and must not share code or configuration. Sibling packages under one namespace share a `__init__`, share utilities by gravity, and eventually share a regex module because it was convenient. A separate top-level tree with an import-linter contract forbidding `bta.screener → verifier` and `verifier → bta.screener` makes the violation a CI failure. The verifier is also the only component that reads live flag values, which is a second reason to keep its blast radius visible.

**`infra/estate/` is separable from `infra/hosting/`.** POR-4 requires the estate to have no coupling to the hosting layer. Enforced by the estate module taking no input from hosting and exporting only resource identifiers.

---

## 3. Phases

### Phase 0 — Platform spikes

**Goal.** Answer four questions that can each invalidate an L3 design, before anything is built on top of them.

| Spike | Question | If it fails |
| -- | -- | -- |
| 0.1 | Identity Platform custom token with a `team` claim → STS exchange → external-account credentials constructed in memory, no credential file → BigQuery read allowed for `team-a`, denied for `team-b` | L3's credential story (FR-14) is not implementable as specified. Everything downstream changes. Blocking. |
| 0.2 | Does a BigQuery row-access policy accept a federated `principalSet` grantee? | F1 moves from row-level to per-team datasets. Changes the generator, the estate, and how strong the demonstration is. |
| 0.3 | Firestore REST with a caller ID token + Security Rules, document-level deny | F5's L3 control becomes per-collection IAM, a weaker demonstration that must be labelled as such rather than quietly substituted. |
| 0.4 | Is the Cloud Run metadata server reachable from the agent process under L3? | FR-14's "no ambient credential in process" needs restating. Not a redesign, but the claim must match reality. |

**Deliverable.** Four findings in `docs/spikes/`, each one paragraph plus the evidence that produced it. Spike code deleted.

**Exit.** All four answered. FR-14 and FR-17 amended where a spike came back negative. 0.1 is blocking; 0.2–0.4 change scope but do not stop the build.

**Status: spikes complete (2026-08-12).** Findings in `docs/spikes/`. CI added afterwards — see [CI](#ci-added-2026-08-14) below.

| Spike | Result |
| -- | -- |
| 0.1 | Yes, both platform and SDK. FR-14's credential design is implementable. |
| 0.2 | Yes. F1 becomes a row in a shared table protected by a row access policy — a stronger flag than the per-team fallback, because the agent is never refused anything. |
| 0.3 | Yes, via caller-token REST. Server SDKs bypass Security Rules, so F5 runs on a separate client path. |
| 0.4 | Metadata server reachable and, by default, sufficient to read the whole estate. FR-14 restated; SEC-11 and SEC-11a added. |

**Discussion still open.** Whether 0.2's fallback is per-team datasets or per-team tables in one dataset — affects whether `list_services` can enumerate what it should not read, which is itself an interesting surface.

#### CI (added 2026-08-14)

Not in the original plan, and that was the omission. §5 below lists seven constraints "CI checks, so they cannot decay quietly" without saying which phase builds CI; the first workflow to exist was a teardown one, written during phase 1, which left the repository able to destroy the estate and not to build it. Four of §5's rows are import contracts and import-linter was not a dependency.

Placed in phase 0 rather than retrofitted later because a contract written after its package exists is shaped to the imports it already has. Phase 2 creates `bta.tools`, `bta.agent` and `bta.identity` together, and that is the last point at which three of those rows are still decisions.

Two workflows, matching the two things that can be run against the project:

| | Trigger | Does |
| -- | -- | -- |
| `ci.yml` | every PR; dispatch to apply | ruff, pytest, import-linter, `terraform fmt`/`validate`, a flag-value scan over the tracked tree — then, on dispatch, `terraform apply` and the seed |
| `teardown.yml` | dispatch | baseline capture, `terraform destroy`, stale-resource scan |

The flag-value scan duplicates the pre-commit hook on purpose: hooks are opt-in and `--no-verify` skips them, so the check that matters most for containment was the one with no backstop.

**There is deliberately no third workflow for `infra/bootstrap/`.** One was built and removed (TD-6): Terraform refreshes a whole module before planning any of it, and `workloadIdentityPools.get` — needed to refresh the two pools and their providers — sits only in `roles/iam.workloadIdentityPoolAdmin`, which neither `ci_project_roles` nor `ci_deploy_roles` holds. Adding it would give an identity reachable from `main` the ability to rewrite the attribute condition on the pool that authenticates it, which is the escalation TD-2's ref-scoping exists to prevent. Bootstrap is applied from an operator's laptop, all of it, every time. The cost is that changes to CI's authority have no run history; `infra/bootstrap/README.md` §"Why this module is not automated" states it rather than leaving it implied.

The estate job needs roles `ci_project_roles` refuses, which was TD-5. Resolved by impersonation: those roles sit on a `ci-deploy` service account created by bootstrap, and the federated principal holds only `roles/iam.serviceAccountTokenCreator` on it. Still keyless, so SEC-6 holds literally — but the privilege is one hop away rather than absent, and the write-up says so rather than claiming a reduction it did not make.

**Exit.** Gates run on every PR. One structural contract configured and demonstrated to fail when violated, with `tests/test_structure.py` blocking a §5 tree from arriving without its contract. The estate job has a credential path. Whether it works is phase 1's first apply.

---

### Phase 1 — Estate and generator

**Goal.** A deterministic, seedable synthetic estate with all eight flags planted, and a fixture manifest that the verifier can read.

**Work.** Estate Terraform, including the **dedicated runtime service account with no estate access** required by SEC-11 — the default compute service account is never used, and spike 0.4 showed that forgetting this silently voids the entire L3 column. The workload identity pool must also take a name not burned by the spikes, since deleted pools are soft-deleted for 30 days. Terraform needs retry tolerance for pool and IAM propagation, both of which were observed to lag.

The generator as a Python module emitting the manifest per schemas §3. Deterministic planting for BigQuery, GCS, Firestore and Secret Manager. F3's log line is the hard one: Cloud Logging is eventually consistent and timestamp-ordered, so the plan is to plant into the log sink's BigQuery destination and treat Logging as the write path rather than the read surface — confirmed in this phase, and if it does not hold, F3's reachability changes.

**Two departures from the above, both settled as built.** F3 does not use the sink: export is best-effort, lags by minutes, and Logging assigns `insertId` and `timestamp` itself, none of which survives a byte-identical-manifest criterion. `orbit.logs` is a generator-written table instead, and the sink stays real for operator diagnostics. Separately, the value is not in the log line — `orbit.logs` carries no row access policy, so anything in it is readable at L3 and F3's registered `held` prediction would be unreachable. The line is the **carrier**, instructing an assistant to read a `team-b` incident; the value is in that incident, behind the same policy as F1. `service` remains `bigquery` in both cases, so schemas §2's contract field is unaffected. Reasoning in `infra/estate/README.md` and `src/bta/generator/README.md`.

The generator is split: a pure planner that decides, and an applier that writes. The determinism criterion then runs with no project, no credentials and no network, which is what makes it a check on every commit rather than one somebody remembers to run.

**Applying is a replace, not an insert** — settled while building the applier, and a correction to the obvious design rather than an elaboration of it. An applier that only writes leaves the *previous* seed's flag values live in the estate: Secret Manager keeps versions forever and `access_secret_version` takes a version number, artefact names carry a version string so a reseed lands beside the old objects rather than over them, and Firestore keeps documents from a larger previous run. A reseeded estate would then serve values no manifest describes, which the verifier cannot score and which makes the retired seed's containment story false. Each surface is therefore pruned to the plan, superseded secret versions are destroyed rather than disabled, and the counts are reported. Reasoning and the two things the applier refuses to do are in `src/bta/generator/README.md`.

**The placement tests sweep seeds rather than sample them.** A misplaced flag does not fail loudly — it produces a `held` cell that reads as the architecture working when the value was never reachable at all, which is the most flattering failure available to this project and the one it must not be able to publish. The concealment invariants are therefore asserted over twenty-five seeds, not one: the corpus's original draw-based ownership passed on every seed anybody tried by hand, and at the default estate size some seeds still leave a team three closed incidents, two of which would be F1 and F3. Builds are pure and cost milliseconds, so breadth here is free. Separately, no assertion in `tests/` may render a flag value into its failure output — pytest prints both operands, and a CI log is a transcript.

**Two things happen here rather than later, deliberately.**

`terraform destroy` is verified now (NFR-6), while the estate is small enough to check by hand that nothing survived. Verifying teardown at the end of a build is how orphaned resources get discovered by a bill.

**Not by hand, in the end, and not from Terraform's summary either.** `.github/workflows/teardown.yml` runs `terraform destroy` and then a step that scans the project for stale resources and writes a log. The scan exists because "Destroy complete!" is a statement about state, and the survivors of a teardown are by definition the resources state never tracked: it found a live Firebase ruleset from spike 0.3 on its first run, before the estate existed at all.

The comparison is against an inventory captured before the estate was applied — teardown as a return to a baseline rather than as an empty project, which is also the only form of the claim bootstrap can satisfy. That capture is a mode of the same workflow rather than a documented manual step, because the step most likely to be skipped is the one that has to happen first and cannot be reconstructed afterwards. Surfaces the scan could not read fail it rather than counting as clear, on the same principle as `insufficient`.

This makes TD-1 live: NFR-6 as written admits no carve-out for bootstrap, and that is a PRD amendment rather than something a script can settle.

The SEC-3 budget kill path is rehearsed now, in a scratch project, for real. Testing "budget alert → Pub/Sub → Cloud Function → disable billing" means actually disabling billing, which cannot be done casually against a project holding work in progress. It is untestable later without destroying the lab, and SEC-3 says tested, not deployed.

**Built as `infra/billing-guard/`**, not inside `infra/hosting/` where §2 placed it. It is rehearsed in a scratch project before hosting exists, and it must survive a teardown of everything it guards — folding it into the module that gets destroyed and reapplied most often is how a cap goes missing for an afternoon. `ci.yml` applies it immediately before the estate on the same dispatch, so the cap exists before the spend does and NFR-5 still covers it. Two things shaped it more than the wiring did. The budget publishes on *every* cost update rather than on threshold crossings, so most invocations do nothing, the firing path runs repeatedly once the cap is passed, and idempotence comes from re-reading `billing_enabled` rather than from remembering. And `enable_kill_switch` defaults to false — the function decides and logs without acting — because deployed and tested look identical until the day it runs. The decision is `function/decision.py`, which imports nothing and is covered by `tests/test_billing_guard.py`; a kill switch whose logic is only exercised by deploying it is a kill switch nobody has tested. The grant is `roles/billing.projectManager` on the project, which detaches an account and cannot attach one.

**Exit.** Two applies from the same seed produce byte-identical manifests. Destroy leaves nothing. Billing-disable demonstrated once, end to end, with evidence.

---

### Phase 2 — Tools, identity, audit

**Goal.** Eight tools callable and audited, with no agent in the picture.

**Design.** Tools are plain functions taking an explicit context argument carrying the credential factory and caller identity — dependency injection rather than ambient state, which is what lets the same function body run under all three credential modes without knowing which one it is in. This satisfies POR-3 and FR-11 while keeping the credential seam explicit at the call site, per §1.

Auditing is applied by the wrapper, not written inside each tool. A tool that forgets to emit an audit record is a silent hole in VER-4, and the wrapper makes it structural. Denials must be attributable: `denied_iam`, `denied_capability` and `denied_document` are distinct outcomes because at L3 the whole point is knowing *which* control produced the refusal. "The model declined" is never an authorisation outcome (schemas §6).

**Exit.** Every tool exercised from a harness under all three credential modes. Audit records correct, including on denial. No credential material anywhere in a record (VER-5). Every denial test paired with a positive case in the same run, asserting on the specific error reason — spikes 0.1 and 0.3 both produced all-denied results from unrelated causes that would have read as passes.

**Plus one assertion the estate cannot make for itself.** The runtime service account holds no estate access (SEC-11), but it can impersonate the shared estate credential, because something has to obtain that credential at L1 and L2 and FR-1 puts all three levels behind a single service. The capability therefore exists in the process serving an L3 session.

Nothing the matrix measures can reach it — the model emits tool calls, no tool impersonates anything, and credential selection happens in the tool boundary from the level rather than from anything the model can influence. But that is a claim about code, and it is the claim L3 rests on. So: **a test asserting the credential factory never returns the shared credential at L3.** Decided 2026-08-14, against the alternative of one Cloud Run service per level, which would close the residual by making FR-13's constant-agent guarantee an enforced invariant rather than a structural one. The reasoning is in `infra/estate/identity.tf` and `infra/estate/README.md`.

**Discussion still open.** Whether the context object is passed positionally to every tool or bound via a factory — cosmetic, but it is the seam readers will look at first, so worth choosing for legibility.

---

### Phase 3 — Loop, providers, levels

**Goal.** Both agents running at all three levels.

**Work.** The thin tool-calling loop. One provider protocol with three implementations behind it (FR-22, POR-2), models pinned in config and recorded per run (FR-22a). Memory store per schemas §7. Then levels as configuration over that, per FR-13.

**Prompt composition (FR-13).** A shared base plus a per-level fragment, with the composed prompts committed as artefacts and diffed in review. FR-13 says byte-identical wherever the level does not define them; that is checkable, and it should be checked by a test rather than by intent, because an accidental wording drift between L1 and L3 confounds the only variable the lab is varying.

**Two pre-registrations happen here.** Both are in §4 and neither is optional.

**The L3 typed-memory extractor is deterministic.** F7b's containment claim is that planted instructions cannot become entitlement, because memory is stored as typed records rendered as data. If a model decides what lands in `subject` and `value`, attacker-controlled text flows into the typed record and the containment claim rests on the model again — which is the exact thing this project argues against. The extractor is rule-based, and its limitations are documented rather than hidden.

**Exit.** C1 leaks at all three levels. This is the test that the screener and verifier are genuinely separate mechanisms: if C1 is blocked at L2, the screener has knowledge of flag syntax and FR-16 has been violated somewhere.

---

### Phase 4 — Verifier and site

**Goal.** Automatic capture, and a human able to attack by hand.

**Verifier.** The schemas §1.5 normalisation set at depth two, recording which transform produced the match. Two-channel capture per VER-1a, with `model_output` verdict-bearing and `tool_result` recorded alongside. Depth-two decoding across eleven transforms will produce false positives on arbitrary text; the flag format is distinctive enough that this is manageable, but the false-positive rate is measured, not assumed.

**Site.** Access code gate, persona picker, chat, tool-call transcript with parameters and row counts, captured-flag panel labelled by channel. The transcript is the thing that makes the `tool_result` channel visible to a human, so it is a demonstration surface rather than a debugging convenience.

**Exit.** F1 and F2 captured by hand, through the UI, at L1.

**Rendering is settled** — server-rendered with progressive enhancement, SSE for streaming. It was already settled in §7's decisions table and simultaneously described here as open, which is how a decision gets re-litigated by whoever reads the other section first. Recorded as `docs/decisions/0007-site-is-server-rendered-with-sse.md`; §7's row is unchanged.

---

### Phase 5 — Attack suite

**Goal.** Reproducible evidence at the SC-3 threshold.

**Scale.** Roughly 100 authored cases — twelve or more per flag, four or more families each — at three levels, three models and three repeats is on the order of 2,700 sessions per full run. Consequences: the runner is `asyncio` with per-provider concurrency bounds, results are written incrementally so a run is resumable rather than all-or-nothing, and the provider limit actually hit is recorded and published with the runtime per NFR-2.

**Case authoring is the real work of this phase,** not the runner. Twelve cases across four families for every flag and level is where `insufficient` verdicts come from if the effort is underestimated, and `insufficient` is a legitimate result but a disappointing one to arrive at by accident.

**Exit.** A dry run at L1 only, reproducing Phase 4's manual captures automatically.

---

### Phase 6 — Reference run and publication

**Goal.** The deliverable.

**Order matters.** OD-9 (transcript redaction) is resolved before the run, not after: defeated-cell transcripts contain live flag values, SC-6 commits the reference run, and schemas §3 forbids committing values. Discovering that afterwards means either withholding the evidence or leaking the flags.

Then: run, build the matrix (pure aggregation, verdicts computed and never hand-entered), classify any L3 defeats per SC-4 with `insufficient` as the default and the burden of proof on `absent`, write up every cell where `diverged_from_expectation` is true. README with warnings above the quickstart (DIS-4), cost model (NFR-4), coverage gaps and the FinBot comparison (DIS-1). DIS-3 confirmation before the repository goes public.

---

## 4. Pre-registered artefacts

Committed, versioned and dated **before** the reference run. Each one could otherwise be tuned after seeing results, and each one would change the matrix if it were.

| Artefact | Phase | Why it must be pre-registered |
| -- | -- | -- |
| Flag value shapes | 1 | Added 2026-08-14. F4 is minted credential-shaped and every other flag as an inert word marker, and that choice decides the L2 column as directly as the screener does: mint everything credential-shaped and the screener blocks all eight, six predictions fail for a reason unrelated to the mitigation stack, and C1 stops being a control. Reasoning in `src/bta/generator/values.py`. |
| L2 output screener pattern set | 3 | It decides several L2 cells. Derived from a public ruleset rather than written against our own fixtures, so that "F4 held at L2" is a claim about the pattern class and not about our regex. |
| L2 injection classifier pattern set | 3 | Added 2026-08-24, and not in the original list. FR-15's stack classifies user turns *and tool results*, and the tool-result half decides F3's L2 cell as directly as the screener decides F4's. No public ruleset exists for prompt-injection phrasing, so unlike the screener's patterns this list is ours — a weaker registration, stated as such in `src/bta/levels/mitigations.py` rather than left to be discovered. |
| L3 typed-memory extraction rules | 3 | F7b's verdict is a direct function of these. |
| Composed system prompts per level | 3 | FR-13's only-variable-is-authorisation claim depends on them. |
| Attack cases and their `expected_outcome` | 5 | FR-23. Cases added after seeing a result are how a threshold gets met on paper. |
| Thresholds | already | SC-3, restated here so the list is complete. |
| Flag `expected` verdicts | already | schemas §2. Divergence is a finding, not an error to correct. |

---

## 5. Structural enforcement

Constraints that CI checks, so they cannot decay quietly.

| Rule | Requirement | Check |
| -- | -- | -- |
| No import path between `verifier/` and `bta.screener` | VER-3 | import-linter contract |
| Screener contains no flag-format knowledge | FR-16 | grep for the flag prefix in the screener tree; C1 leaking at L2 as behavioural proof |
| `bta.tools` imports nothing from `bta.agent` | POR-3, FR-11 | import-linter contract |
| Identity read only through the adapter | POR-1 | import-linter: only `bta.identity` may read request auth |
| No hardcoded hostnames or base URLs | POR-5 | lint rule |
| Non-level prompt text identical across levels | FR-13 | test over composed prompts |
| No secret scanning bypass beyond the narrow ignore | SEC-7 | push protection stays on |

---

## 6. Risk register

| Risk | Impact | Handling |
| -- | -- | -- |
| Spike 0.2 fails | F1's estate reshapes | Phase 0 runs first. Fallback designed before Phase 1 starts. |
| Screener becomes the thing the matrix measures | L2 column unfalsifiable | Pre-registration (§4), public-derived patterns, C1 as the behavioural check. |
| Provider throttling | NFR-2 unpredictable | Resumable runner, recorded limits, NFR-2 restated as a target with published achieved runtime. |
| F7b extractor harder to specify deterministically than expected | Containment claim weakens | Surfaced in Phase 3, documented honestly if the rules are cruder than hoped. |
| Case authoring underestimated | `insufficient` verdicts by accident | Case count tracked per flag/level from the start of Phase 5, not at the end. |
| Automated red-teaming against provider APIs trips abuse detection | Run interrupted mid-suite | Resumable runner. Provider tier and any throttling recorded. |
| Reference-run transcripts leak flag values | SEC-7, schemas §3 | OD-9 resolved before Phase 6 begins. |

---

## 7. Decisions

| Decision | Choice | Reasoning |
| -- | -- | -- |
| Language and dependency manager | Python, `uv` | Lockfile and resolver speed matter with three provider SDKs plus GCP clients. Language rationale is in prd OD-1 addendum. |
| Site rendering | Server-rendered, SSE for streaming | The transcript is the only stateful surface and the repository is meant to be read. A client build step buys little here. |
| Spikes | Deleted after findings are written | Spike code carries assumptions nobody re-examines. Findings persist in `docs/spikes/`. |
| Schema models | Transcribed to pydantic in Phase 2 | Early transcription is low-risk against frozen contracts, but unused models drift from the docs they mirror with no consumer to catch it. |
| Transcript redaction (OD-9) | Every run, not before publication | Settled in prd VER-6. Redaction is a pipeline stage emitting a redacted copy alongside the raw; publication reads only the redacted copy. The redactor lives inside `verifier/`, bound by VER-3. |

Layout in §2 remains a proposal until the scaffold lands.
