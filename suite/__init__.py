"""The attack suite — phase 5.

Three trees with deliberately different characters, and the separation is the
design rather than tidiness:

`cases/`    Data. Pre-registered attack cases (`schemas.md` §4), authored with
            their `expected_outcome` **before** any evidence exists, plus the
            loader and the validation that makes a malformed case a failure at
            load rather than a silent skip mid-run.
`runner/`   Effects. An asyncio executor that drives a live deployment over the
            same HTTP surface an attacker uses, writes results incrementally so
            a run resumes, and records the provider limit it actually hit
            (NFR-2).
`matrix/`   Pure aggregation over serialised run results (`schemas.md` §8).
            Imports nothing from `runner`, touches no network, and computes
            verdicts that are never hand-entered.

## The suite never holds a flag value

`schemas.md` §3 keeps the set of components that may read live flag values to
two: the generator that plants them and the verifier that detects them. The
suite is neither. It learns that a flag was captured because **the deployment
tells it so** — capture is automatic in the site process (FR-6), where the
verifier already lives — and it records the flag id and the channel, never a
value.

That is enforced rather than intended: `pyproject.toml` forbids
`suite -> bta.generator` and `suite -> verifier`. It also means a run result
and a raw transcript are different artefacts with different handling, which is
what VER-6 already assumes.

## Why this is not built on pytest

Run results are the output artefact (`schemas.md` §5), not a pass/fail signal.
A test runner's reporting model fights that: a `not_captured` result is
evidence, an `error` result contributes nothing to the SC-3 threshold, and
neither is a failing test. `build-plan.md` §3 settled this before the phase
started.
"""
