# break-the-agent — agent context

Context for coding agents (Claude Code, Codex and similar) working in this repository. Human readers want [`README.md`](README.md) first.

## Project overview

A self-deployed lab that demonstrates which class of control actually removes an AI agent exploit. Two deliberately vulnerable agents belonging to a fictional retailer, each at three defence levels, with seven flags plus a control flag planted across a real cloud estate. The agent, its prompts and its model are held constant; only the authorisation architecture underneath varies. The deliverable is the **result matrix** and the Terraform that produces it — not the site.

| Document | Read it for |
|---|---|
| `docs/overview.md` | The argument |
| `docs/prd.md` | Requirements. IDs (FR-n, SC-n, VER-n, SEC-n, NFR-n, POR-n) are stable and referenced by tests and attack cases |
| `docs/schemas.md` | Data contracts. The generator, verifier, audit logger, attack runner and matrix builder all read from it — a change here is a change to all five |
| `docs/build-plan.md` | Phases, exit criteria, pre-registered artefacts, structural enforcement |
| `docs/process.md` | Branch/PR cadence, agent gates, the pre-registration carve-out. Read before any feature work |
| `docs/first-deploy.md` | The ordered bootstrap → estate → teardown sequence, and the steps that cannot be redone |
| `docs/results.md` | The reference run and what it does and does not show |
| `docs/tech-debt.md` | Known gaps, tracked as TD-n. Check here before "fixing" something that looks wrong |

---

## Rules

Non-negotiable, regardless of task scope.

- **Never commit directly to main.** Feature or fix branch, then PR. Docs-only changes are the exception — but *not* for pre-registered artefacts, which always take a PR (see `docs/process.md`).
- **Never commit a flag value.** `BTA{...}` values, the fixture manifest, and raw transcripts stay out of git permanently. The pre-commit hook enforces this (enable it with `.githooks/setup-hooks.sh`) and is not to be bypassed with `--no-verify`; once a value is in history the estate needs reseeding.
- **Never read the fixture manifest into context.** Only the verifier reads it (`schemas.md` §3). Anything read into context can reach a transcript.
- **Never re-render audit parameters into model context.** They contain attacker-supplied injections, stored as data for analysis (`schemas.md` §6). That includes later analysis runs.
- **Do not weaken a pre-registered artefact to reach a nicer result.** Thresholds, screener patterns, expected verdicts, composed prompts and attack cases are fixed before the reference run. `insufficient` is a legitimate verdict; a threshold quietly lowered is not.
- **Before committing, verify only intended files are staged.** `git status` and `git diff --cached --name-only`.
- **Run lint and tests locally before opening a PR.** CI gates catch problems earlier; they don't replace local validation.
- **Never install anything onto the machine without being asked.** No `brew`, `apt`, `npm i -g`, `pipx`, `go install`, `curl | sh`, or a binary dropped onto PATH. Needing a tool to verify your own work is not authorisation. `uv sync` into the project's `.venv` is fine. If a check cannot run without a missing tool, say what goes unverified.
- **Never hardcode a deployment-specific value.** Project ID, region, billing account, bucket and dataset names, service URLs, model identifiers and every credential are inputs (POR-6). Documentation shows placeholders.

### Structural constraints that CI enforces

Violating one of these is a build failure, not a review comment. Each is a constraint the project's central claim depends on, and each is easy to breach by accident. Import rules are `import-linter` contracts in `pyproject.toml` (`uv run lint-imports`); `tests/test_structure.py` fails the build if a constrained package exists without its contract.

| Constraint | Why | Enforced by |
|---|---|---|
| No import path between `verifier/` and `src/bta/screener/`, in either direction. The verifier also may not import `bta.agent`, `bta.tools`, `bta.levels` or `bta.site` | VER-3 — the screener and verifier are the same mechanism pointed in opposite directions; the verifier consumes serialised artefacts, never the runtime that produced them | Two import contracts. `verifier` is a root package so the reverse direction is expressible (`test_the_verifier_is_a_root_package`) |
| The screener contains no knowledge of flag syntax | FR-16 — a screener matching `BTA{` reduces L2 to a tautology | `tests/test_screener.py` greps the tree for the flag prefix and checks no minted value is matched. C1 leaking at every level is the behavioural half |
| `bta.tools` imports nothing from `bta.agent`, `bta.generator` or `bta.levels` | POR-3, FR-11, FR-13 — tools are plain functions and do not know the level | Import contract |
| Caller identity is read only through `bta.identity` | POR-1 — one adapter; `bta.identity.tokens` is the only module that holds a token | Import contract |
| Non-level prompt text is byte-identical across levels | FR-13 — the only variable is authorisation | `tests/test_levels.py::test_non_level_prompt_text_is_byte_identical_across_levels` |
| `bta.credentials` may not import `bta.tools` | Authority flows one way, from the boundary asking for a credential | Import contract |
| `bta.agent` reaches tools only through the audited boundary — never `bta.generator` or `bta.credentials` | The loop must not be able to mint a credential or read a flag value | Import contract |
| `bta.site` reaches flag values only through the verifier — never `bta.generator` or `bta.screener` directly | FR-6 puts the verifier in the site process; the site may not reach values independently | Import contract |
| `suite/` never imports `bta.generator` or `verifier` | Run results are committed (SC-6); a suite holding values would put them there | Import contract (ADR 0013) |
| `bta.memory` may not import `bta.tools`, `bta.agent` or `bta.generator` | Memory must not acquire a level; F7b's containment lives in `firestore.rules` | Import contract |
| `suite.matrix` may not import `suite.runner`, `suite.cases` or `bta` | SC-6 — the matrix must rebuild identically from run results alone | Import contract |
| The generator's planning half never imports `google` | Determinism tests run with no project, credentials or network | Import contract (`test_the_generator_contract_is_configured`) |
| No hardcoded hostnames or base URLs | POR-5 | **Not enforced** — no lint rule exists (TD-15). Review for it |

---

## Architecture

```
Persona picker → Identity Platform token → STS exchange (L3 only)
    → Cloud Run site (access-code gated)
    → thin tool-calling loop (no framework — see prd.md OD-1)
    → plain-function tools + per-request credentials
    → BigQuery · Cloud Storage · Firestore · Secret Manager · Cloud Logging
```

- **Language:** Python, managed with `uv`.
- **Agent framework:** none. A thin loop — the interesting code is credential minting at the tool boundary, which is exactly the seam a framework abstracts away.
- **Providers:** three adapters behind one interface, all on **Vertex AI** in the deployment project and IAM-authenticated — Google natively, Anthropic via `AsyncAnthropicVertex`, xAI through the OpenAI-compatible endpoint (FR-22). The lab holds no provider API key (SEC-9). Models are pinned in config and recorded per run (FR-22a). Partner models reach fewer regions than Gemini, so `anthropic_vertex_location` is separate from `vertex_location`.
- **Reference run provider coverage:** only Google was run. FR-22's three providers is recorded unmet in `docs/prd.md`, so every cell that would read `held` reads `insufficient`. **`min_providers: 3` is pre-registered and must not be lowered.** Amending FR-22/FR-22a/SC-3 is an operator decision, not an agent one. The `anthropic` and `xai` adapters are unexercised in the reference run — keep them, and do not claim they were exercised.
- **Site:** server-rendered, SSE for streaming (turn-level, not token-level — TD-23).
- **IaC:** Terraform. `infra/estate/` has no coupling to `infra/hosting/` (POR-4).
- **Cloud:** GCP. Disposable project, separate billing account, hard budget cap (SEC-2).

---

## Repository structure

```
docs/                    prd, schemas, overview, build-plan, process, results, tech-debt,
                          decisions/ (ADRs), spikes/
flags/                   eight flag definitions — no values
evidence/                the reference run: matrix.json (built by suite.matrix from runs/)
                          and runs/*.jsonl, redacted per VER-6. Raw transcripts never live here
infra/
  bootstrap/             project APIs, two WIF pools, state bucket, ci-deploy identity,
                          Artifact Registry repository. Applied locally (TD-6)
  estate/                BigQuery, Storage, Firestore, Secret Manager, Logging, identities
  billing-guard/         budget → Pub/Sub → function that detaches billing (SEC-2, SEC-3)
  hosting/               Cloud Run, Identity Platform, site secrets — see its README
scripts/                 lab.sh, verify-teardown.sh, token minting
src/bta/
  models.py              closed enums from schemas.md (Level, Agent, Surface, ...)
  config.py              deployment inputs from the environment (POR-5, POR-6)
  identity/              the one adapter that reads a caller's token (POR-1)
  credentials/           level → credential policy table (policy.py) and minter (factory.py)
  audit/                 AuditRecord and the sink protocol; unreachable from tools (VER-4)
  tools/                 the nine tools, the auditing boundary, client dispatch
  generator/             planner (pure) + applier; emits the fixture manifest
  agent/                 the thin loop, transcript capture, the harness
  providers/             one protocol, three Vertex adapters
  levels/                prompt composition, composed prompts, the L2 mitigation stack
  memory/                typed (L3) and freeform (L1/L2) records; in-memory and Firestore stores
  screener/              L2 output screening; vendored patterns with PROVENANCE.md (FR-16)
  site/                  the Cloud Run app — gate, persona picker, chat, capture panel
verifier/                capture detection and VER-6 redaction. Outside src/bta/ on purpose (VER-3)
suite/                   cases/ (108 pre-registered attack cases), runner/, matrix/
tests/                   pytest — unit, structural and integration
.github/workflows/       ci.yml (gates; estate/hosting on dispatch), image.yml, teardown.yml
```

Packages with their own README (`src/bta/tools/`, `src/bta/agent/`, `src/bta/site/`, `src/bta/generator/`, `suite/`, `infra/*/`) document their internals; read them before changing those trees.

---

## Key patterns

- **Credential minting happens at the tool boundary**, not in the tool. Every tool takes `ctx: ToolContext` as its first positional argument (ADR 0001). `ctx.client(Surface.BIGQUERY)` names a surface, never a credential mode, and resolves through `bta.credentials.policy.mode_for(level, surface)` — a twelve-entry table that is the experiment's whole independent variable. The same function body runs under `shared`, `federated` and `service` modes without knowing which.
- **Auditing is applied by the wrapper**, never written inside a tool. `bta.tools.boundary.invoke` is the only path that can call a tool; `ToolContext` carries no audit sink. `KeyboardInterrupt`/`SystemExit` are recorded then re-raised. `AuditRecord` is a frozen dataclass with an explicit `to_dict()` field list, not a pydantic model (ADR 0002).
- **Authorisation outcomes are attributable.** `denied_iam`, `denied_capability` and `denied_document` are distinct, and `src/bta/tools/errors.py` decides which — conservatively and per surface. An error is a denial only when it can be attributed to a control: a denial recorded as an error costs one data point, while an error recorded as a denial manufactures evidence for the claim under test. "The model refused" is never an authorisation outcome.
- **Levels are alternatives, not layers.** L3 does not include L2's mitigations (FR-14). L1 and L2 are credentially identical on purpose — if they diverge, the L2 column stops measuring the mitigation stack.
- **Memory is typed at L3, freeform at L1/L2.** The L3 extractor (`src/bta/memory/extract.py`) is rule-based, never model-driven; a model choosing what lands in a typed record would put F7b's containment back on the model.
- **Redaction runs on every run**, not before publication (VER-6). It substitutes markers rather than deleting text, because the disclosure is the evidence. It covers audit records as well as transcripts (ADR 0008), scans for encoded runs embedded in prose (ADR 0010), and fails closed: output is re-scanned and `RedactionIncomplete` raised rather than returning a copy that only looks redacted. Known residual: TD-26.

---

## Testing

```sh
uv sync --all-groups
uv run ruff check . && uv run ruff format --check .
uv run lint-imports
uv run pytest
```

| Level | Tool | Scope |
|---|---|---|
| Unit | pytest | Tools, verifier normalisation, matrix aggregation |
| Structural | import-linter | The constraints table above |
| Integration | pytest | Tool boundary under each credential mode, denial paths |
| End-to-end | the attack suite | Not a test framework — it emits run results (`schemas.md` §5) |

The attack suite is deliberately not built on pytest: run results are the output artefact, and a test runner's reporting model fights that.

---

## Git workflow

```
main                         ← protected
└── feature/<slug>
└── fix/<slug>
└── docs/<slug>
```

---

## Deployment

`terraform apply` from clean to a seeded estate, then `terraform destroy` verified rather than assumed (NFR-5, NFR-6). CI holds no long-lived cloud credential — Terraform applies via workload identity federation, on a pool separate from the arena's (SEC-6, SEC-8). NFR-5 is not fully met; the README lists the manual steps.

Hosting is part of the same CI dispatch (`apply_hosting` plus an `image` digest); `teardown.yml` destroys hosting before the estate. The order cannot be changed: `infra/hosting/` refuses a mutable tag, so the image is built and pushed first, then hosting is applied with the digest-pinned reference and the flag-value inputs read from the fixture manifest — never from a `.tfvars` file. `docs/first-deploy.md` is the manual path.

### Claude Code permissions

`.claude/settings.json` is strict JSON with no comments — a settings file that fails to parse is ignored **silently**, taking the `deny` rules with it. Other agents should follow the same boundaries.

- **deny** — live flag values (`fixture-manifest*.json`, `.fixtures/`), raw transcripts, audit records, `.env`, tfvars and tfstate. Also `curl | sh`, `wget | sh`, `uv tool install` and `gh extension install`.
- **ask** — `terraform apply`/`destroy`/`import`/`state`, every `gcloud` verb that mutates IAM, billing, secrets or storage, and every package manager that installs onto the machine.
- **allow** — read-only Terraform and gcloud, uv/pytest/ruff, git, gh, and safe shell utilities.

---

## Common pitfalls

- **Flag payloads are credential-shaped by design** (SEC-7) and will trip secret scanning. Push protection stays on; narrow allowlist entries go in `.githooks/secret-scan-allowlist`, and flag values are never allowlistable.
- **The L2 screener is the project's sharpest self-inflicted risk.** It decides several L2 cells. Its pattern set is pre-registered and derived from a public ruleset, so any L2 result is a claim about a pattern class rather than about our regex. The registered set does not match F4, so F4 diverges from its `held` prediction at L2 — accepted and published, not tuned away (ADR 0009).
- **C1 must leak at all three levels.** If C1 is ever blocked at L2, the screener has acquired knowledge of flag syntax and FR-16 has been violated.
- **Firestore's server SDK bypasses Security Rules.** L3 document-level authorisation therefore runs through the REST API with the caller's own token.
- **A BigQuery reseed drops row access policies**, which are F1's and F3's L3 control. The applier fails closed (`_assert_policies_survived`), but `infra/estate` must be re-applied after any seed.
- **The suite bypasses per-persona rate limits** (NFR-3a) but never the daily token ceiling. The bypass token is a deployment secret.
- **Known measurement defects are recorded, not fixed** — TD-36 to TD-49 in `docs/tech-debt.md`, from the pre-publication review. Several (TD-37 F7b unreachable, TD-38 tool errors as non-captures, TD-40 memory crossing invocations) change what a zero in the matrix means. A fix needs a live deployment to verify and, where it touches measurement, a re-run published alongside the original.
- **A defeated L3 flag is the most valuable result this project can produce.** Classify per SC-4 — `absent` (implementation defect) or `insufficient` (architecture failed) — with `insufficient` as the default and the burden of proof on `absent`. Publish either way.

---

## Updating this document

Fix drift when you notice it — stale context is worse than none. An agent may rewrite this file, but never `prd.md`, `schemas.md`, or any pre-registered artefact (`docs/process.md`).
