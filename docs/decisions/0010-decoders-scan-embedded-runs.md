# 0010 — The verifier's decoders scan for encoded runs inside surrounding text

**Date:** 2026-08-26
**Phase:** 4
**Status:** accepted
**Requirements:** VER-1, VER-2, VER-6, `schemas.md` §1.5, §5
**Related:** ADR 0008, TD-14

## Decision

`base64_decode` and `hex_decode` no longer require the whole document to be one
encoded blob. When the whole-text form does not apply, each scans for candidate
encoded runs *inside* the text and substitutes each decoding in place, keeping
the surrounding words. `redact()` correspondingly substitutes each encoded form
**loosely** — tolerant of interleaved separators and zero-width characters —
rather than as a literal string.

The eleven transform names in `schemas.md` §1.5 are unchanged, `MAX_DEPTH`
stays at 2, and the chain naming in §5 is unchanged. What changed is how much
one decoding step can see.

## Why

Three defects met on one ordinary disclosure shape, and the composite was a
silent containment failure.

`find_captures` folded its needle by every folding step in a chain, including
steps that ran *before* a decode — those acted on the ciphertext, not on the
value, and folding by them ate the value's own hyphens. So
`separator_strip+base64_decode` searched a correctly decoded haystack for a
de-hyphenated value that was not in it.

The residual check that makes VER-6 fail closed *is* `find_captures`, so it
inherited the miss. And `_walk` substituted encodings literally, so it did not
catch a split blob either. Both halves of the redactor missed the same shape.

The whole-text requirement then hid the rest. A bare blob was at least
detected; the same blob with four words in front of it was not reached by any
transform, so `redact()` returned the document **unchanged**, raised nothing,
and reported success. The value was recoverable by hand from a copy cleared for
publication. `git` keeps that, and the estate then needs reseeding.

`the value was <base64>` is not a contrived shape. It is what a model says.

## What this costs

More text is reachable in one step than before, which moves a boundary the
tests used to pin. `percent(base64(separator-split))` is now reached in two
steps rather than three; the negative control was rewritten to use three
applications of a single transform, so "three away" is unambiguous, and it now
also asserts `MAX_DEPTH` directly rather than inferring the bound from one
example.

**The false-positive rate was re-measured, not assumed.** 1,703 corpus
documents × 8 minted values × depth 2 → **0 captures**, unchanged from before
the change. Both reviewers' caveat still stands and is worth repeating: that
corpus is estate prose, not model output, and model output is where fenced
base64 and hex blobs actually appear.

## Alternatives rejected

**Leave detection alone and only fix redaction.** Substitution would cover the
value, but the verdict would still miss the capture — and a miss aggregates to
`held`, which is the most flattering failure available to this project. The
matrix would under-report disclosure.

**Slide the decode start offset across the whole document.** Strictly more
thorough, and quadratic: `separator_strip` removes spaces as well as hyphens,
so a stripped transcript is one long run, and decoding from every offset is
O(n²) on documents that are tens of kilobytes. Rejected on cost. The
consequence is recorded as a known gap — a separator-split blob embedded in
prose is still not *detected*, though it is now *redacted*, so it cannot leak.

**Fail closed on everything not substitutable.** This is what the code did
before, and in principle it is the safe answer. In practice it means the
defeated cells the project most wants to publish are exactly the ones that
cannot produce a redacted artefact, and the only way forward is editing the
redactor mid-run — a change to a containment mechanism made under result
pressure. Failing closed stays as the backstop for an encoding nobody
anticipated, and there is a test that stubs the substituter out to prove the
backstop still fires.
