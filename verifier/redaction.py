"""VER-6 — redaction as a stage of every run.

Two artefacts leave a run: the raw one, to the evidence bucket under restricted
IAM and never committed, and a redacted copy in which every flag value is
replaced by a marker naming the flag it stood for. Anything published or
committed reads the redacted copy only.

**Substitution, not deletion.** A transcript with the flag cut out no longer
shows that the flag was disclosed, and the disclosure is the evidence. The
marker names the flag so a reader of the published run can see *which* value
came out and where, without the value.

**This covers audit records as well as transcripts.** TD-14: an audit record's
`parameters` are recorded verbatim by design, and `check_policy(topic="<F4
value>")` is an ordinary thing a steered model does — at L1 the Onboarding
agent holds F4 in its own system prompt. Nothing in `bta.audit` can recognise a
flag value, because only the verifier may hold one (`schemas.md` §3), so the
redaction has to happen here or nowhere.

**It fails closed.** After substituting, the result is re-scanned with the full
VER-2 normalisation set. If any flag value is still reachable, `redact` raises
rather than returning a copy that looks redacted and is not. A redactor that
silently missed an encoding would put a live value into the published
repository — which is the one failure this project cannot take back, because
`git` keeps it and the estate then needs reseeding.
"""

from __future__ import annotations

import base64
import codecs
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any

from verifier.match import find_captures
from verifier.normalise import SEPARATORS, ZERO_WIDTH

MARKER = "[BTA-REDACTED:{flag_id}]"

#: Characters that may be interleaved through a value without a human losing
#: it. Substituting a *pattern* rather than a literal is what lets `B-T-A-{…}`
#: and a zero-width-split value be redacted, rather than merely detected and
#: then refused by the residual check.
_INTERLEAVE = "".join(re.escape(chr(code)) for code in sorted(set(SEPARATORS) | set(ZERO_WIDTH)))
_GAP = f"[{_INTERLEAVE}]*"


class RedactionIncomplete(RuntimeError):
    """A flag value survived redaction. Never returned, always raised."""


def _encodings(value: str) -> list[str]:
    """Reversible forms an attacker asks a model to emit.

    These are the decoding half of the VER-2 set. Each is generated from the
    value and substituted literally, because a base64 blob has no structure to
    substitute *inside* — the whole token is replaced or none of it is.
    """
    raw = value.encode("utf-8")
    forms = [
        base64.b64encode(raw).decode(),
        base64.b64encode(raw).decode().rstrip("="),
        raw.hex(),
        raw.hex().upper(),
        codecs.encode(value, "rot_13"),
        urllib.parse.quote(value),
        urllib.parse.quote(value, safe=""),
        value[::-1],
    ]
    # Longest first: an unpadded base64 form is a prefix of the padded one, and
    # replacing the short one first would leave the padding stranded.
    return sorted({form for form in forms if form and form != value}, key=len, reverse=True)


def _loose_pattern(value: str) -> re.Pattern[str]:
    """The value, tolerant of separators and zero-width characters between its
    characters. Covers the folding half of the VER-2 set in one substitution.
    """
    return re.compile(_GAP.join(re.escape(char) for char in value))


def redact_text(text: str, values: Mapping[str, str]) -> str:
    """Replace every reachable form of every flag value with its marker."""
    if not text:
        return text
    redacted = text
    # Longest value first, so a flag whose value contains another's — which the
    # generator does not produce, but which nothing here should depend on — is
    # not half-substituted into an unrecognisable state.
    for flag_id, value in sorted(values.items(), key=lambda kv: len(kv[1]), reverse=True):
        if not value:
            continue
        marker = MARKER.format(flag_id=flag_id)
        for encoded in _encodings(value):
            # Loosely, not literally. An attacker who base64s the value and
            # then splits the blob — `-`.join(b64) is an ordinary
            # `encoding_obfuscation` output — defeated a literal replace, and
            # the residual check could not see it either once the blob sat in
            # surrounding prose. `redact()` then returned the document
            # unchanged, raised nothing, and left the value recoverable by
            # hand from a copy cleared for publication.
            redacted = _loose_pattern(encoded).sub(marker, redacted)
        redacted = _loose_pattern(value).sub(marker, redacted)
    return redacted


def _walk(node: Any, values: Mapping[str, str]) -> Any:
    """Redact every string anywhere in a nested structure.

    Keys are redacted as well as values. A tool parameter name never holds a
    flag today, but a record whose *key* carried one would be published
    unredacted by a value-only walk, and that is not a distinction worth
    betting the containment claim on.
    """
    if isinstance(node, str):
        return redact_text(node, values)
    if isinstance(node, Mapping):
        return {redact_text(str(k), values): _walk(v, values) for k, v in node.items()}
    if isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        return [_walk(item, values) for item in node]
    return node


def _residual(node: Any, values: Mapping[str, str]) -> list[str]:
    """Flag ids still reachable in `node` under the full normalisation set."""
    found: set[str] = set()
    if isinstance(node, str):
        found.update(c.flag_id for c in find_captures(node, dict(values), channel="model_output"))
    elif isinstance(node, Mapping):
        for key, value in node.items():
            found.update(_residual(str(key), values))
            found.update(_residual(value, values))
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        for item in node:
            found.update(_residual(item, values))
    return sorted(found)


def redact(document: Any, values: Mapping[str, str]) -> Any:
    """Redact a transcript or an audit record, then prove it worked.

    The same function serves both because both are `to_dict()` output and
    neither is imported here — the verifier consumes serialised artefacts, not
    the runtime that produced them, which is the rule the import contract in
    `pyproject.toml` enforces.

    Raises `RedactionIncomplete` if any value survives. The exception names
    flag **ids** and never values.
    """
    redacted = _walk(document, values)
    survivors = _residual(redacted, values)
    if survivors:
        raise RedactionIncomplete(
            f"redaction left {len(survivors)} flag value(s) reachable: "
            f"{', '.join(survivors)}. The raw artefact is unaffected; nothing "
            f"may be published from this run until the redactor covers the "
            f"encoding responsible."
        )
    return redacted


__all__ = ["MARKER", "RedactionIncomplete", "redact", "redact_text"]
