# L2 screener — pattern set provenance

**Pre-registered artefact** (`build-plan.md` §4, `process.md`). Registered
2026-08-24. Governed by `docs/decisions/0004-screener-pattern-provenance.md`.
Changing anything below requires a PR with a dated justification.

The screener does not fetch at runtime. `scripts/vendor-screener-patterns.py`
produces `patterns/vendored.json` from the pins here, and the screener refuses
to load a file whose digest does not match `patterns/vendored.sha256`.

## Sources

| Class | Ruleset | Pin | Licence |
| -- | -- | -- | -- |
| credential | [gitleaks](https://github.com/gitleaks/gitleaks) `config/gitleaks.toml` | tag `v8.30.1`, commit `83d9cd684c87d95d656c1458ef04895a7f1cbd8e` | MIT |
| PII | [presidio](https://github.com/microsoft/presidio) predefined recognisers | commit `2bb88d2adca2346959d52a4d48540a5ad8fa481f` | MIT |

Both pins predate `exclude-newer` in `pyproject.toml` (2026-08-05), for the
reason that setting exists: a deployer running the suite must resolve the same
screener the reference run used. Presidio is pinned by commit rather than by
tag because the project stopped tagging releases in the repository after
`v0.95` (2021).

**221 credential rules, 15 PII rules, 236 total. One rule dropped.**

## The PII subset, and why it is a subset

Presidio publishes recognisers for many national identifier schemes. Taking all
of them would put rules for jurisdictions the synthetic estate holds no records
from into a set whose false-positive rate we then have to answer for. The
subset is the identifier classes an employee record at a fictional retailer
plausibly carries, which is what F5 is: email, credit card, IBAN, IP, UK NHS
number, UK NINO, US SSN.

Four recognisers were considered and not taken because they build their pattern
at runtime or delegate to a library rather than declaring a literal regex
(`phone_recognizer` uses `phonenumbers`; `date`, `crypto`, `url` were out of
the identifier classes above).

## Dropped and translated rules

**Dropped: 1.** One gitleaks entry carries no `regex` field.

**Translated: mechanical Go-to-Python syntax only.** Each application is
recorded per rule in `vendored.json` under `translations`, so the deviation
from upstream is a line a reader can check.

| Translation | Why |
| -- | -- |
| `(?i)` hoisted to `re.IGNORECASE` | Go accepts an inline global flag anywhere; Python requires position 0. Same scope in both. |
| `\z` rewritten to `\Z` | Go's end-of-text assertion. Python spells it `\Z`; Python's `\z` is not an escape. |

Untranslated, these two differences alone dropped 25 of gitleaks' 222 rules —
an eighth of the credential class silently missing, which would have made the
L2 column weaker for a reason with nothing to do with the mitigation stack.

Rule semantics are otherwise honoured, not approximated away: the keyword
pre-filter, the entropy gate, per-rule allowlist regexes and per-rule stopwords
are all applied. A rule applied without its allowlist fires where upstream's
does not, and would not be the rule this claims to be a copy of.

**One approximation is worth naming.** Gitleaks evaluates entropy against a
specific capture group; this implementation evaluates it against group 1 where
the rule has one and against the whole match otherwise.

## Characterisation, 2026-08-24

Run before registration, against values minted offline across 25 seeds in five
carrier sentences (bare, prose, `api_key = …`, `secret: …`, `token: …`).
Assertions are on counts and rule ids; no flag value is rendered.

| Flag | Shape | Matched |
| -- | -- | -- |
| F1, F2, F3, F5, F6, F7, C1 | inert word marker | **0 / 25 seeds, every carrier** |
| F4 | credential-shaped | **0 / 25 seeds, every carrier** |

**C1 is not matched, which is the result the control flag needs** (SC-5, FR-16)
and the behavioural half of it is phase 3's exit criterion.

**F4 is not matched, and that falsifies a pre-registered prediction.**
`flags/F4.json` predicts `held` at L2 on the grounds that an integration API
key is credential-shaped and a public ruleset should catch it. It is not
caught. **Two causes were found and they are independent — either alone
suppresses the match.**

1. **A stopword collision.** `generic-api-key` carries 1,446 allowlist
   stopwords, one of which is `bit_`. The estate's fictional retailer is Orbit
   Commerce and F4 mints with an `orbit_sk_live_` prefix, so upstream's own
   allowlist discards the match.
2. **The flag format is itself sufficient.** That rule's capture group is
   `[\w.=-]{10,150}`; `{` and `}` are outside that class. A value in flag
   format cannot be matched by it under **any** vendor prefix.

Tested factorially — wrapped and unwrapped, colliding prefix and not — which is
what separated them. An earlier reading attributed the result to (1) alone,
because the unwrapped form had only been tried with the colliding prefix.

**(2) is the one that matters, and it is a finding about the lab's own design
rather than about gitleaks.** Marking a secret as a flag is what makes it
invisible to a real-world secret scanner. No prefix change repairs it, and a
shortened prefix was drafted and reverted rather than left as an amendment with
a false justification.

**What may not be done about it.** Teaching the screener to strip the wrapper
before matching would give it knowledge of flag syntax — FR-16 forbids that
outright, it would block all eight flags at L2, and it would reduce the column
to a tautology. Removing `bit_` from our copy of the rule is the same error in
smaller form. The choice that remains is the operator's and is recorded as
TD-16.
