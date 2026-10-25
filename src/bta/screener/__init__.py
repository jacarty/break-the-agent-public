"""The L2 output screener — FR-15, FR-16.

One of the four components of the L2 mitigation stack. It reads model output
and redacts credential-shaped and PII-shaped strings before that output reaches
the attacker.

**It knows nothing about flags.** FR-16 is the whole point of this package: a
screener that matched the flag prefix would block all eight flags at L2, six
pre-registered predictions would fail for a reason unrelated to the mitigation
stack, and the L2 column would be a tautology. The patterns come from two
public rulesets, vendored at pinned revisions by
`scripts/vendor-screener-patterns.py` and registered in `PROVENANCE.md` per
`docs/decisions/0004-screener-pattern-provenance.md`. Nothing in this tree
contains the flag prefix, and `tests/test_screener.py` greps for it.

C1 leaking at L2 is the behavioural proof of that claim, and it is phase 3's
exit criterion for exactly this reason.

**VER-3.** No code or configuration here is shared with the verifier. They are
the same mechanism pointed in opposite directions: the verifier knows flag
values and looks for them, this looks for a pattern class and has never been
told a flag exists. The import contract in `pyproject.toml` makes the sharing a
build failure rather than a review comment.

**It fails closed, and not by redacting everything.** A regex that raises, a
missing pattern file, a digest that does not match — none of those produce a
`ScreenResult`. They raise, and `bta.levels.mitigations` turns an exception
from here into a withheld turn. A screener that returned the model's text
unchanged when it broke would be a mitigation that silently stopped running,
which is the L2 column reporting a control that was not there.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import warnings
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PATTERNS_DIR = Path(__file__).parent / "patterns"
VENDORED = PATTERNS_DIR / "vendored.json"
DIGEST = PATTERNS_DIR / "vendored.sha256"

#: Substitution, not deletion — the same reasoning VER-6 gives for transcripts.
#: Text with the credential cut out no longer records that a credential was
#: about to be disclosed, and that disclosure is what an L2 cell is measuring.
REDACTION = "[redacted: {rule_class}]"


class ScreenerUnavailable(RuntimeError):
    """The screener could not run. Never a pass — see the module docstring."""


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    rule_class: str
    description: str
    pattern: re.Pattern[str]
    keywords: tuple[str, ...]
    entropy: float | None
    allowlist: tuple[re.Pattern[str], ...]
    stopwords: frozenset[str]


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    rule_class: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """What the loop renders instead of the model's own text.

    `findings` deliberately carries offsets and rule ids and **not the matched
    string**. A screener that reported what it caught would be a second place
    holding the thing the redaction exists to remove, and at L2 the matched
    string is sometimes F4.
    """

    text: str
    findings: tuple[Finding, ...]

    @property
    def redacted(self) -> bool:
        return bool(self.findings)


def _shannon(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    length = len(text)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _load_document() -> dict:
    if not VENDORED.exists() or not DIGEST.exists():
        raise ScreenerUnavailable(
            f"{VENDORED.name} or its digest is missing. Run "
            f"`uv run python scripts/vendor-screener-patterns.py`. The screener "
            f"does not fetch at runtime and does not run without its registered "
            f"pattern set (ADR 0004)."
        )
    body = VENDORED.read_text(encoding="utf-8")
    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    expected = DIGEST.read_text(encoding="utf-8").strip()
    if actual != expected:
        raise ScreenerUnavailable(
            "the vendored pattern set does not match its recorded digest. The "
            "pattern set is a pre-registered artefact; an edit to it is a PR "
            "with a dated justification, not a local change (process.md)."
        )
    return json.loads(body)


@lru_cache(maxsize=1)
def load_rules() -> tuple[Rule, ...]:
    document = _load_document()
    rules = []
    # Several upstream rules write a literal `[` inside a character class as
    # `[[`, which Python warns about as a possible nested set. Go reads it the
    # same way Python does — as a literal — so the behaviour matches and the
    # warning is advisory. Suppressed at this one call site rather than
    # globally: a `FutureWarning` filter in `pyproject.toml` would also hide
    # the next one, which might not be advisory.
    for entry in document["rules"]:
        flags = re.IGNORECASE if entry.get("ignore_case") else 0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            compiled = re.compile(entry["regex"], flags)
            allowlist = tuple(re.compile(r) for r in entry.get("allowlist_regexes", []))
        rules.append(
            Rule(
                id=entry["id"],
                rule_class=entry["class"],
                description=entry["description"],
                pattern=compiled,
                keywords=tuple(entry.get("keywords", [])),
                entropy=entry.get("entropy"),
                allowlist=allowlist,
                stopwords=frozenset(entry.get("stopwords", [])),
            )
        )
    return tuple(rules)


class Screener:
    """Applied to assistant text at L2 and at no other level (FR-14)."""

    def __init__(self, rules: tuple[Rule, ...] | None = None) -> None:
        self._rules = load_rules() if rules is None else rules

    def __len__(self) -> int:
        return len(self._rules)

    def screen(self, text: str) -> ScreenResult:
        if not text:
            return ScreenResult(text=text, findings=())

        lowered = text.lower()
        spans: list[tuple[int, int, Rule]] = []
        for rule in self._rules:
            # Gitleaks' own pre-filter. Honoured because a rule applied without
            # it fires more often than the rule this claims to be a copy of.
            if rule.keywords and not any(word in lowered for word in rule.keywords):
                continue
            try:
                matches = list(rule.pattern.finditer(text))
            except re.error as exc:  # pragma: no cover - compiled at load
                raise ScreenerUnavailable(f"rule {rule.id} failed while matching: {exc}") from exc
            for match in matches:
                # **`lastindex` is the last group that *participated*, not the
                # group count.** A rule whose group 1 is optional can match via
                # a later alternative, leaving `lastindex` truthy and
                # `group(1)` None — and `_allowlisted` then called `.lower()`
                # on None. That crashed the whole turn, so the output was never
                # screened at all and the execution was recorded as an
                # unattributable `AttributeError` (one L2 row on 2026-09-15,
                # `AC-F4-005`, on the single cell registered `held`).
                #
                # The whole match is the candidate when the capturing group did
                # not participate. This restores what the rule intended; the
                # vendored pattern set is untouched (`PROVENANCE.md`).
                candidate = match.group(1) if match.lastindex else match.group(0)
                if candidate is None:
                    candidate = match.group(0)
                if self._allowlisted(rule, candidate):
                    continue
                if rule.entropy is not None and _shannon(candidate) < rule.entropy:
                    continue
                spans.append((match.start(), match.end(), rule))

        if not spans:
            return ScreenResult(text=text, findings=())
        return self._apply(text, spans)

    @staticmethod
    def _allowlisted(rule: Rule, candidate: str) -> bool:
        lowered = candidate.lower()
        if any(word in lowered for word in rule.stopwords):
            return True
        return any(allowed.search(candidate) for allowed in rule.allowlist)

    @staticmethod
    def _apply(text: str, spans: list[tuple[int, int, Rule]]) -> ScreenResult:
        """Overlapping matches collapse to one redaction.

        Two rules matching the same credential is the ordinary case — gitleaks
        ships both generic and vendor-specific rules for the same key shapes.
        Substituting twice would corrupt the offsets and emit a redaction
        marker inside a redaction marker.
        """
        spans.sort(key=lambda span: (span[0], -span[1]))
        merged: list[tuple[int, int, Rule]] = []
        for start, end, rule in spans:
            if merged and start < merged[-1][1]:
                previous = merged[-1]
                merged[-1] = (previous[0], max(previous[1], end), previous[2])
                continue
            merged.append((start, end, rule))

        out: list[str] = []
        findings: list[Finding] = []
        cursor = 0
        for start, end, rule in merged:
            out.append(text[cursor:start])
            out.append(REDACTION.format(rule_class=rule.rule_class))
            findings.append(Finding(rule.id, rule.rule_class, start, end))
            cursor = end
        out.append(text[cursor:])
        return ScreenResult(text="".join(out), findings=tuple(findings))


__all__ = [
    "REDACTION",
    "Finding",
    "Rule",
    "ScreenResult",
    "Screener",
    "ScreenerUnavailable",
    "load_rules",
]
