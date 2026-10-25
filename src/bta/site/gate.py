"""FR-1, FR-2 — the access code.

**This is a shared secret, not authentication.** FR-2 is unusually insistent
about it and this module repeats it because the code is the only thing between
the open internet and a deliberately vulnerable agent with a token budget, and
that is a description of what it is *for*, not a claim about what it secures.
There is no account, no session identity derived from it, and no authorisation
decision anywhere in the lab reads it. It stops drive-by token consumption. It
would not stop anyone who wanted in.

The comparison is constant-time regardless, because a timing oracle on a
shared secret is free to avoid and awkward to explain afterwards.
"""

from __future__ import annotations

import hmac
import os

from bta.config import MissingConfiguration

ACCESS_CODE_ENV = "BTA_ACCESS_CODE"
COOKIE_NAME = "bta_gate"


def configured_code(environ: dict[str, str] | None = None) -> str:
    """The deploy-time code, from the platform (POR-6a).

    Raises rather than defaulting to empty. A service that started with no code
    configured would serve an ungated, internet-reachable, deliberately
    vulnerable agent — and would look healthy doing it.
    """
    source = os.environ if environ is None else environ
    code = source.get(ACCESS_CODE_ENV, "")
    if not code:
        raise MissingConfiguration(
            f"{ACCESS_CODE_ENV} is not set. FR-1 gates every route behind it, "
            f"and a service that starts without one is an open deployment of "
            f"a deliberately vulnerable agent."
        )
    return code


def matches(supplied: str, configured: str) -> bool:
    """Constant-time comparison. Empty never matches, including against an
    empty configured code — which `configured_code` refuses to produce, but
    this function is reachable from tests and from a future caller."""
    if not supplied or not configured:
        return False
    return hmac.compare_digest(supplied, configured)


__all__ = ["ACCESS_CODE_ENV", "COOKIE_NAME", "configured_code", "matches"]
