# Contributing

Thanks for looking. This repository is an experiment as much as a codebase, and
a few of its rules exist to keep the published result honest rather than to
keep the code tidy. Read this before opening a PR.

## What is useful

- **Running it yourself and publishing what you get.** A run against three
  providers is the result this repository could not produce
  (`docs/results.md`). Open an issue with your matrix and how you ran it.
- **A defeated L3 flag.** That is a research finding, not a private report —
  open a public issue. [`SECURITY.md`](.github/SECURITY.md) explains why, and
  what *is* a private report.
- **Fixes for anything in [`docs/tech-debt.md`](docs/tech-debt.md).** TD-36 to
  TD-49 came from the pre-publication review and are unfixed because each
  needs a live deployment to verify. A fix that says how it was verified is
  worth more than one that does not.
- **Documentation that has drifted from the code.** Say which is right.

## What will not be merged

**Edits to pre-registered artefacts to reach a different result.** Thresholds
in `docs/prd.md` SC-3, `expected` verdicts in `flags/`, the screener pattern
set, the injection classifier patterns, the typed-memory extraction rules, the
composed prompts and the attack cases in `suite/cases/` were all fixed before
the reference run (`docs/build-plan.md` §4). A change to one needs a dated
justification, and if it would change a published cell, both the original and
the revised results are published. New attack cases are welcome on those
terms — as new, dated cases, not edits to the registered ones.

**Anything that gives the L2 screener knowledge of the flag format.** C1 must
leak at every level; if it stops, the L2 column is a tautology (FR-16).

**Anything that lets the agent reach beyond the lab** — a URL-fetching tool,
wider egress, a code-execution path. The agents are meant to be vulnerable; the
lab is not.

## Ground rules

- **Never commit a flag value, a fixture manifest, a raw transcript, Terraform
  state or a `.tfvars` file.** Once a flag value is in history the estate needs
  reseeding. Enable the hooks before your first commit and do not bypass them
  with `--no-verify`:

  ```sh
  .githooks/setup-hooks.sh
  ```

- **No hardcoded deployment values** — project IDs, regions, bucket names,
  model identifiers and credentials are inputs (POR-6). Use placeholders in
  docs.
- **Keep the structural constraints passing.** `uv run lint-imports` enforces
  the import boundaries the result depends on; a broken contract is a build
  failure, not a style point. `AGENTS.md` lists them and why.

## Setup and checks

No cloud project is needed for any of these.

```sh
uv sync --all-groups
uv run ruff check . && uv run ruff format --check .
uv run lint-imports
uv run pytest
```

Deploying the lab is a separate, longer job — `docs/first-deploy.md`, and the
warnings at the top of the README first.

## Pull requests

One branch per change (`feature/<slug>`, `fix/<slug>`, `docs/<slug>`), one PR
per branch. Say what you verified and how — offline tests, a live deployment, or
neither. Using a coding agent is fine; point it at `AGENTS.md`.
