# Public export

This directory is a standalone source export with no inherited Git history
and no remote configured.

Publication changes:

- Removed the internal session handover and the real teardown inventory.
- Replaced the reference runtime account number and rehearsal project ID in
  spike notes with placeholders.
- Replaced the real evidence bucket name in run-result references with
  `example-project-evidence`. These references are illustrative and do not
  resolve to the original evidence.
- Updated the baseline instructions for local use or a private deployment
  repository, and ignored future deployment inventory JSON in this copy.
- Replaced the internal `CLAUDE.md` working context with a tool-neutral
  `AGENTS.md`; `CLAUDE.md` now only imports it.
- Replaced remaining references to the original project ID in docs and one
  Terraform comment with "the reference project". Private-repository Actions
  run IDs in `docs/tech-debt.md` are kept as provenance and do not resolve
  publicly.
- Brought module and package READMEs, `docs/first-deploy.md` and
  `docs/tech-debt.md` up to date with the reference run, and disclosed that the
  reference deployment shared a billing account (TD-10).
- Recorded the pre-publication code review's findings as TD-36 – TD-49 and in
  `docs/results.md` §*Found in pre-publication review*. No code was changed to
  address them, because none could be verified without a live deployment.
- Added `THIRD_PARTY_NOTICES.md` for the vendored gitleaks and Presidio rules,
  and `CONTRIBUTING.md`.
- Retained the original attack cases, prompts, mitigation rules, thresholds
  and measured outcomes. Publication edits do not change the experiment; where
  review changed how an outcome should be read, the note sits beside it.

Capture a private teardown baseline for your own deployment before running
the estate. Do not publish unredacted inventories, fixture manifests, raw
transcripts, deployment secrets or Terraform state.
