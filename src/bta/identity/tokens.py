"""The only module permitted to hold a caller's raw authentication material.

POR-1 says identity is read through one adapter. The reason it is split across
two modules rather than one is spike 0.1 finding 3: the guarantee L3 offers
reduces to *whatever decides which team claim to mint*. The grant is on the
attribute, not the person — a previously unseen subject presenting
`team: team-a` was granted exactly what `alice` was granted. So the code that
turns a request into a `team` string is the root of trust for the entire L3
column, and it is worth being able to point at it.

`bta.identity.tokens` is named in an import-linter contract: nothing outside
`bta.identity` may import it. That is what stops a second reader of request
auth appearing in a tool, a site handler, or the agent loop.
"""

from __future__ import annotations

from dataclasses import dataclass


class SubjectToken:
    """A caller's Identity Platform ID token, wrapped so it cannot be printed.

    The token is credential material under VER-5 and it travels further than
    any other credential in the process: it reaches the STS exchange, and at L3
    it is the bearer on the Firestore REST path. Any of those call sites is one
    f-string away from an exception message, a log line or an audit record.

    So the value comes out only through `reveal()`, which is greppable, and
    `repr` is fixed text. This does not make leaking impossible. It makes it
    deliberate, and it makes the deliberate cases countable.
    """

    __slots__ = ("_token",)

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("empty subject token")
        self._token = token

    def reveal(self) -> str:
        return self._token

    def __repr__(self) -> str:
        return "<SubjectToken: redacted>"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SubjectToken) and other._token == self._token

    def __hash__(self) -> int:
        return hash(("SubjectToken", self._token))


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """Who is asking, as the rest of the process is allowed to see them.

    `sub` and `team` are the two claims the platform enforces on: `team` drives
    IAM attribute mapping (spike 0.1) and Firestore Security Rules (spike 0.3),
    from one token and two enforcement systems. `sub` drives the memory rule.

    `subject_token` is present because L3 cannot work without it — the STS
    exchange needs the caller's own assertion — and is wrapped for the reason
    above. The audit record builder takes `sub` and `team` and never this
    object, which is asserted by a test rather than left to review.
    """

    sub: str
    team: str
    subject_token: SubjectToken

    def __post_init__(self) -> None:
        if not self.sub:
            raise ValueError("caller has no subject")
        if not self.team:
            raise ValueError(
                "caller has no team claim. The arena pool's attribute condition "
                "refuses `assertion.team == ''`, so a token that reached here "
                "without one would fail at the STS exchange with a less "
                "legible error."
            )
