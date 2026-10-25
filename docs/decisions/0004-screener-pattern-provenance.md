# 0004 — The L2 screener's patterns are taken from public rulesets, with provenance

**Date:** 2026-08-24
**Phase:** 3
**Status:** accepted
**Requirements:** FR-15, FR-16, VER-3, SC-5, `build-plan.md` §4
**Pre-registered artefact.** Amending this, or the pattern set it governs,
requires a PR with a dated justification (`process.md`).

## Decision

The L2 output screener's pattern set has two halves, each taken from a public
ruleset at a pinned revision:

- **Credential-shaped strings** — from Gitleaks' default rules.
- **PII-shaped strings** — from a subset of Presidio's recognisers.

`src/bta/screener/PROVENANCE.md` records, for each half: the ruleset, the
commit SHA, the retrieval date, the rules taken, and every rule dropped with
the reason. A rule may be dropped **only** because it fires on the synthetic
estate corpus. No rule may be added, edited or dropped with reference to a flag
value, a fixture, or an attack result.

## Why

**The screener decides several L2 cells and we are the ones writing it.** That
is the sharpest falsifiability risk in the project (`build-plan.md` §1). A
pattern set derived from our own fixtures would make "F4 held at L2" a claim
about our regex. Derived from a public ruleset at a pinned revision, it is a
claim about a pattern class that somebody else maintains and a reader can go
and read.

**Two sources rather than one, because FR-15 asks for two classes.** FR-15
requires screening for credential-shaped *and* PII-shaped strings. Gitleaks is
the strongest public credential set; it is not a PII set. Taking only Gitleaks
would leave the PII half of FR-15 as a stated coverage gap rather than a met
requirement, and the honest way to meet it is a second public source rather
than patterns we invent.

**detect-secrets was declined for a specific reason.** Its plugin set carries a
generic entropy check, and `src/bta/generator/values.py` deliberately steers the
markers away from long high-entropy runs so that a generic rule cannot block a
flag by accident and read as the mitigation stack working. Choosing a ruleset
whose entropy plugin re-opens that is choosing to fight a phase 1
pre-registration with a phase 3 one.

## The characterisation step, and what it may not become

Before the pattern set is registered, it is run against flag values minted
offline across the seed sweep `tests/test_placement.py` already uses, and the
result — which value shapes the set matches — goes into `PROVENANCE.md`.

This is characterising the instrument, not tuning it. It happens **before any
attack case has been executed**, its output is published either way, and it
changes nothing about the patterns. The subsequent restriction is what makes it
safe: after registration, the only permitted edit is dropping a rule that fires
on the synthetic corpus, and that edit is a PR with a dated justification.

`tests/` may not render a flag value into failure output, so the
characterisation asserts on counts and rule names, never on the matched string.

**If the public set matches the inert word marker, C1 is blocked at L2 and the
phase exit criterion fails.** That would not be an FR-16 violation — the
screener would still know nothing of flag syntax; a generic rule would simply
have matched. It would be a real finding about the pattern class, and it is
recorded here in advance so that the response is not chosen after seeing it.
The response is: publish it, and bring the choice between accepting a
level-dependent control flag and re-registering the phase 1 value shapes back
to the operator as a decision, in the open, with both options' costs stated.
It is not licence to edit a pattern.

## Costs

Two upstream projects instead of none, pinned by SHA and never auto-updated, so
the reference run's screener is reproducible by a deployer. Neither is a runtime
dependency: the patterns are copied into the tree with their provenance, not
imported, because VER-3 forbids the screener sharing configuration with the
verifier and an upstream library is a configuration surface neither of them
controls.

Public credential rules are tuned for source code, not for agent output. Their
false-positive rate on synthetic prose is measured during characterisation and
published, on the same principle as VER-2's normalisation false-positive rate
in phase 4.

## Revisit if

Never, to reach a nicer result. If a rule is genuinely wrong — it fires on the
synthetic corpus — the PR says which rule, on which corpus text, on what date.
