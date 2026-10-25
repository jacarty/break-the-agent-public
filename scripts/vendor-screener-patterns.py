#!/usr/bin/env python3
"""Vendor the L2 screener's pattern set from two public rulesets.

`docs/decisions/0004-screener-pattern-provenance.md` requires the pattern set
to come from public rulesets at pinned revisions, with the provenance recorded
and the vendored artefact reproducible. This script is that step. It is run
deliberately, by a person, and its output is committed — the screener never
fetches anything at runtime.

The pins below are fixed, not rolling. Both predate `exclude-newer` in
`pyproject.toml`, for the same reason that setting exists: a deployer running
the suite must resolve the same screener the reference run used.

    uv run python scripts/vendor-screener-patterns.py

Writes `src/bta/screener/patterns/vendored.json` and the digest the loader
checks. Re-running with unchanged pins must produce a byte-identical file; the
determinism of this artefact is asserted by `tests/test_screener.py`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "src" / "bta" / "screener" / "patterns" / "vendored.json"

GITLEAKS = {
    "name": "gitleaks",
    "url": "https://github.com/gitleaks/gitleaks",
    "tag": "v8.30.1",
    "commit": "83d9cd684c87d95d656c1458ef04895a7f1cbd8e",
    "licence": "MIT",
    "path": "config/gitleaks.toml",
}

PRESIDIO = {
    "name": "presidio",
    "url": "https://github.com/microsoft/presidio",
    "tag": None,
    "commit": "2bb88d2adca2346959d52a4d48540a5ad8fa481f",
    "licence": "MIT",
}

# The PII subset, and why each member is in it. Presidio publishes recognisers
# for a great many national identifier schemes; taking all of them would put
# rules for jurisdictions the synthetic estate has no records from into a set
# whose false-positive rate we then have to answer for. These are the
# identifier classes an employee record at a fictional retailer plausibly
# carries — which is what F5 is.
PRESIDIO_BASE = "presidio-analyzer/presidio_analyzer/predefined_recognizers"

PRESIDIO_FILES = {
    "generic/email_recognizer.py": "employee contact detail",
    "generic/credit_card_recognizer.py": "payment data, an obvious PII class here",
    "generic/iban_recognizer.py": "payroll detail",
    "generic/ip_recognizer.py": "appears in log and incident records",
    "country_specific/us/us_ssn_recognizer.py": "national identifier",
    "country_specific/uk/uk_nhs_recognizer.py": "national identifier",
    "country_specific/uk/uk_nino_recognizer.py": "national identifier",
}


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - pinned https
        return response.read().decode("utf-8")


def raw(source: dict, path: str) -> str:
    host = source["url"].replace("github.com", "raw.githubusercontent.com")
    return f"{host}/{source['commit']}/{path}"


#: Mechanical differences between Go's regexp and Python's `re`. Each is a
#: syntax translation with identical matching semantics — not a change to what
#: a rule matches, which ADR 0004 forbids doing without a corpus false
#: positive to point at.
#:
#: Every application is recorded in the vendored artefact, so the deviation
#: from upstream is a line a reader can check rather than a claim in a
#: docstring. Left untranslated, these two differences alone dropped 25 of
#: gitleaks' 222 rules — an eighth of the credential class silently missing,
#: which would have made the L2 column weaker for a reason that has nothing to
#: do with the mitigation stack.
def translate(pattern: str) -> tuple[str, int, list[str]]:
    """Go regexp source in, Python-compilable source plus flags out."""
    applied: list[str] = []
    flags = 0

    # Go accepts `(?i)` anywhere; Python requires a global flag at position 0
    # and raises otherwise. Hoisting it to a compile flag is the same scope —
    # in Go an inline global flag also applies to the whole expression.
    if "(?i)" in pattern:
        pattern = pattern.replace("(?i)", "")
        flags |= re.IGNORECASE
        applied.append("(?i) hoisted to re.IGNORECASE")

    # Go's `\z` is end-of-text. Python spells the same assertion `\Z`;
    # Python's `\z` is not an escape at all.
    if r"\z" in pattern:
        pattern = pattern.replace(r"\z", r"\Z")
        applied.append(r"\z rewritten to \Z")

    return pattern, flags, applied


def compilable(pattern: str) -> str | None:
    """Both rulesets are written for other engines. A rule that will not
    compile even after `translate` is dropped, with its reason recorded rather
    than silently skipped."""
    translated, flags, _ = translate(pattern)
    try:
        re.compile(translated, flags)
    except re.error as exc:
        return str(exc)
    return None


def take_gitleaks() -> tuple[list[dict], list[dict]]:
    text = fetch(
        f"https://raw.githubusercontent.com/gitleaks/gitleaks/{GITLEAKS['commit']}/{GITLEAKS['path']}"
    )
    config = tomllib.loads(text)

    kept: list[dict] = []
    dropped: list[dict] = []
    for rule in config.get("rules", []):
        pattern = rule.get("regex")
        if not pattern:
            dropped.append({"id": rule.get("id"), "reason": "rule carries no regex"})
            continue
        error = compilable(pattern)
        if error:
            dropped.append(
                {"id": rule.get("id"), "reason": f"does not compile under Python re: {error}"}
            )
            continue
        translated, flags, applied = translate(pattern)
        kept.append(
            {
                "id": rule["id"],
                "source": "gitleaks",
                "class": "credential",
                "description": rule.get("description", ""),
                "regex": translated,
                "ignore_case": bool(flags & re.IGNORECASE),
                "translations": applied,
                # Honoured by the screener. Gitleaks uses keywords as a
                # case-insensitive pre-filter and entropy as a gate on the
                # match; dropping either would make our copy of the rule fire
                # more often than the rule it claims to be.
                "keywords": sorted(rule.get("keywords", [])),
                "entropy": rule.get("entropy"),
                # Translated on the same terms as the rule itself. An
                # allowlist that failed to compile and was dropped would make
                # our copy of the rule fire where upstream's does not, which is
                # a behaviour change wearing a compatibility gap's clothes.
                "allowlist_regexes": sorted(
                    translate(r)[0]
                    for allow in rule.get("allowlists", [])
                    for r in allow.get("regexes", [])
                    if not compilable(r)
                ),
                "stopwords": sorted(
                    w.lower()
                    for allow in rule.get("allowlists", [])
                    for w in allow.get("stopwords", [])
                ),
            }
        )
    return kept, dropped


def take_presidio() -> tuple[list[dict], list[dict]]:
    kept: list[dict] = []
    dropped: list[dict] = []
    for path, why in PRESIDIO_FILES.items():
        full = f"{PRESIDIO_BASE}/{path}"
        source = fetch(raw(PRESIDIO, full))
        found = 0
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "Pattern" or len(node.args) < 2:
                continue
            label, pattern = node.args[0], node.args[1]
            if not (isinstance(label, ast.Constant) and isinstance(pattern, ast.Constant)):
                continue
            if not isinstance(pattern.value, str):
                continue
            error = compilable(pattern.value)
            if error:
                dropped.append(
                    {
                        "id": label.value,
                        "reason": f"does not compile under Python re: {error}",
                    }
                )
                continue
            found += 1
            kept.append(
                {
                    "id": f"presidio.{Path(path).stem}.{label.value}",
                    "source": "presidio",
                    "class": "pii",
                    "description": f"{label.value} ({why})",
                    "regex": translate(pattern.value)[0],
                    "ignore_case": bool(translate(pattern.value)[1] & re.IGNORECASE),
                    "translations": translate(pattern.value)[2],
                    "keywords": [],
                    "entropy": None,
                    "allowlist_regexes": [],
                    "stopwords": [],
                }
            )
        if not found:
            dropped.append(
                {
                    "id": path,
                    "reason": "no literal Pattern(...) regex in the module; the recogniser "
                    "builds its pattern at runtime or delegates to a library",
                }
            )
    return kept, dropped


def main() -> None:
    credential, credential_dropped = take_gitleaks()
    pii, pii_dropped = take_presidio()

    document = {
        "schema_version": 1,
        "vendored_at": datetime.now(UTC).date().isoformat(),
        "sources": [
            {k: v for k, v in GITLEAKS.items() if k != "path"} | {"rules_taken": len(credential)},
            {k: v for k, v in PRESIDIO.items()} | {"rules_taken": len(pii)},
        ],
        # Sorted so the artefact is a function of the pins and nothing else.
        "rules": sorted(credential + pii, key=lambda rule: rule["id"]),
        "dropped": sorted(credential_dropped + pii_dropped, key=lambda d: str(d["id"])),
    }
    body = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    OUT.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    (OUT.with_suffix(".sha256")).write_text(digest + "\n", encoding="utf-8")

    print(f"{len(credential)} credential rules, {len(pii)} PII rules")
    print(f"dropped: {len(document['dropped'])}")
    print(f"sha256: {digest}")


if __name__ == "__main__":
    main()
