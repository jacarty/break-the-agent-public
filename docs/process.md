# Development Process

> The maintainer's working process — issue board, agent gates, commit cadence. Outside contributors want [`CONTRIBUTING.md`](../CONTRIBUTING.md); the pre-registration rules below apply to everyone.
>
> Process rules for feature work. Read this alongside `AGENTS.md` (architecture context), `prd.md` (requirements), `schemas.md` (data contracts) and `build-plan.md` (phases and exit criteria) before starting any work.

## Branch and PR cadence

- One branch per build-plan phase sub-section or discrete piece of work.
- One PR per branch, merged to `main`, branch deleted on merge.
- Branch naming: `feature/p<N>-<slug>` for phase work (e.g. `feature/p2-tool-boundary`), `fix/<slug>`, `docs/<slug>`, `spike/<N.N>-<slug>`.
- **Docs-only changes** commit directly to `main` — **except** the pre-registered artefacts below, which never take that path.

### Pre-registered artefacts — always PR, never direct

These are the documents whose value depends on having been written *before* the evidence. Amending one after seeing a result, even innocently, damages the claim the project exists to make. They are listed in `build-plan.md` §4 and repeated here because this is where the temptation arises.

| Artefact | Rule |
| -- | -- |
| SC-3 thresholds (`prd.md`) | Registered before the reference run. Not revised afterwards to reach a nicer result. |
| Flag `expected` verdicts (`flags/`) | Divergence from the observed matrix is a finding to publish, not an error to correct. |
| L2 screener pattern set | Pre-registered, derived from a public ruleset. Changing it changes several L2 cells. |
| L2 injection classifier pattern set | Pre-registered 2026-08-24. Decides F3's L2 cell. Ours rather than public-derived, so the provenance is weaker and the discipline has to be stricter. |
| L3 typed-memory extraction rules | F7b's verdict is a direct function of these. |
| Composed system prompts per level | FR-13's only-variable-is-authorisation claim depends on them. |
| Attack cases and `expected_outcome` | Cases added after seeing a result are how a threshold gets met on paper. |

Changes to any of these require a PR, a dated justification in the PR body, and — if the reference run has already happened — publication of both the original and revised results. If that feels heavy, that is the intent: the friction is the control.

## Issue tracking

Every feature PR corresponds to one or more tracked issues. Closing the loop is part of the PR lifecycle:

1. **When opening the PR** — reference the issue(s) in the PR body (`Closes #XX`).
2. **When the PR merges** — move the corresponding issue(s) to Done in the same session. Stale "In Progress" issues make the board lie.
3. **If the PR only partially addresses an issue** — leave it open, comment on what shipped versus what remains, link the PR.

New feature issues are subject to the scope gate in `prd.md` §11: an addition qualifies only if it changes what the result matrix can demonstrate.

## Agent cadence

All agents are defined in `.claude/agents/`. This is the policy for when to invoke each one automatically versus ad-hoc.

### Pre-commit (always, except trivial)

| Agent | Trigger | Why |
|---|---|---|
| **linter** | After the last edit in a session, before `git add` | Cheap cleanup of formatting, unused imports, style violations. Run unconditionally. |

**Self-check before every commit** (not agent-gated — just do it):

1. `git status` — verify only intended files are staged.
2. `git diff --cached --name-only` — scan the list. Unstage anything unrelated.
3. Run lint and tests locally. Do not push code that fails either.
4. Confirm the pre-commit hook ran and passed. If it blocked on flag material, **fix the content — never `--no-verify`**. Once a flag value is in history the estate needs reseeding.

### Pre-PR (conditional gates)

These run **before** the PR is created. Findings fold into the branch (new commits, amend, or rebase) so the PR opens in its final state — one coherent diff, not "feature commits then fix-up commits".

**Fold timing** — two legitimate shapes:

- **One-atomic-fold** — keep the working tree uncommitted while gates run, then commit feature + findings as one commit. Preferred for larger PRs.
- **Two-commit-fold** — commit features, run gates, add findings as a second commit. Fine for small fixes.

Rule of thumb: ≥ 300 net lines → one-atomic-fold. < 300 lines and a single logical fix → two-commit-fold. If a background agent is writing files while gates run, wait for it before committing.

| Agent | Trigger — runs if ANY of these are true |
|---|---|
| **code-reviewer** | Net diff ≥ 300 lines • touches `src/bta/credentials/**`, `src/bta/identity/**`, `verifier/**`, or `src/bta/screener/**` • touches IAM bindings or authorisation logic • introduces a new tool or endpoint • explicitly requested |
| **devops-reviewer** | Touches `infra/**` • touches CI/CD workflows • changes Cloud Run config • changes IAM, WIF, or budget resources |
| **test-generator** | A new tool function lands without tests • a new endpoint lands without tests • logic grows > 100 net lines in a PR • explicitly requested |
| **doc-generator** | Any non-trivial PR, after code-reviewer findings are folded in. **Constrained — see below.** |
| **phase-acceptance** | A PR that closes out a build-plan phase. Reads the phase's exit criteria and the PRD requirements it cites, and checks the code against every one. |

**doc-generator is constrained on this repository.** It may write READMEs, docstrings, ADRs under `docs/decisions/`, and `AGENTS.md`. It must **not** modify `prd.md`, `schemas.md`, or any pre-registered artefact above. An agent whose job is refreshing documents to match reality is precisely the wrong tool near a document whose value is that it was written before reality — and the failure would be silent, which is worse.

### Phase boundaries (periodic, not per-PR)

| Agent | Cadence |
|---|---|
| **codebase-review** | End of each build-plan phase. Refreshes `docs/tech-debt.md`. |
| **doc-generator** | Paired with the phase-end codebase-review, under the constraint above. |

### Ad-hoc (no automatic trigger)

- **code-optimiser** — performance concerns (relevant mainly to the suite runner, where NFR-2 is a real constraint).
- **refactorer** — scoped refactor work.
- **research-brief** skill — Phase 0 spikes and any "which API actually supports this" question.
- **Explore** / **general-purpose** — searching beyond what Grep/Glob handle.

## Exception: trivial-change fast path

If a PR is **all** of the following, only the linter runs:

- ≤ 50 net lines added
- ≤ 2 files touched
- No changes to `infra/**`, CI/CD workflows, or authorisation logic
- No new tools, endpoints, or exports
- **No changes to `verifier/**`, `src/bta/screener/**`, `src/bta/credentials/**`, or `src/bta/identity/**`**
- **No changes to any pre-registered artefact**

The last two exclusions are absolute regardless of size. A two-line change to a credential binding or a screener pattern is exactly the kind of change this project cannot afford to wave through — VER-3 and FR-16 are both one careless import away from being violated.

## Parallel execution

When multiple pre-PR gates apply, spawn the agents in parallel. Same for phase-boundary agents.

## When an agent flags something

1. **Read the full report** before acting.
2. **If blocking (security, correctness, isolation, containment)**: fix before pushing.
3. **If non-blocking (style, nit)**: address inline if cheap, otherwise add to `docs/tech-debt.md` and note it in the PR body under "Known gaps".
4. **If the agent is wrong**: say so in the PR body and keep moving. Agents are a second pair of eyes, not a binding authority.
5. **If phase-acceptance flags a missing requirement**: always blocking. Fix, re-run, then proceed.

## What gets written down

After each feature PR merges: close the issue(s), and update the status log via a docs-only commit.

After each **phase** closes: codebase-review + doc-generator refresh `docs/tech-debt.md` and `AGENTS.md`.

Significant technical decisions taken during implementation that aren't already in the PRD go to `docs/decisions/NNNN-short-title.md`. Decisions that *change* a PRD requirement go to the PRD via PR, with the original text preserved in the diff — `prd.md` already carries a "Closed since first draft" section for this.

Spike findings go to `docs/spikes/`, one file per spike, written before the spike code is deleted.

## Updating this document

Living document. When a rule doesn't fit the work, update the rule and commit with a short justification. Don't accumulate stale rules.

The pre-registration rules are the exception: they are not adjusted because they became inconvenient during a run. That is the situation they were written for.
