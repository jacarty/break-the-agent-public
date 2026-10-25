"""Deterministic randomness.

The phase 1 exit criterion is that two applies from one seed produce
byte-identical manifests, so every arbitrary choice in this package has to be a
pure function of the seed.

`random.Random` is deliberately not used. CPython guarantees the Mersenne
Twister's raw output across versions, but it makes no such promise about how
`choice` or `sample` consume it — those are implementation details that have
changed before. A criterion phrased as *byte-identical* should not rest on an
implementation detail, so the sampling is written out here on top of a hash
function whose output is specified.

Streams are addressed by purpose rather than drawn in sequence. Two callers
asking for `("incidents", "INC-4471")` get the same bytes no matter what else
ran first, which means adding a corpus section cannot perturb every value
downstream of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import blake2b
from typing import TypeVar

T = TypeVar("T")

_SEPARATOR = b"\x1f"
_BLOCK = 64


def _root(seed: str, purpose: Sequence[str]) -> bytes:
    parts = [seed.encode("utf-8"), *(p.encode("utf-8") for p in purpose)]
    return blake2b(_SEPARATOR.join(parts), digest_size=_BLOCK).digest()


class Stream:
    """A named, reproducible byte source.

    Counter mode over blake2b: block *n* is `blake2b(n, key=root)`. Endless,
    seekable in principle, and specified well enough that a reimplementation in
    another language would agree.
    """

    __slots__ = ("_root", "_buffer", "_counter")

    def __init__(self, seed: str, *purpose: str) -> None:
        self._root = _root(seed, purpose)
        self._buffer = bytearray()
        self._counter = 0

    def bytes(self, count: int) -> bytes:
        while len(self._buffer) < count:
            block = blake2b(
                self._counter.to_bytes(8, "big"),
                key=self._root,
                digest_size=_BLOCK,
            ).digest()
            self._buffer.extend(block)
            self._counter += 1
        taken = bytes(self._buffer[:count])
        del self._buffer[:count]
        return taken

    def hex(self, byte_count: int) -> str:
        return self.bytes(byte_count).hex()

    def below(self, bound: int) -> int:
        """Uniform integer in [0, bound).

        Rejection sampling rather than modulo. The bias from `% bound` is tiny
        at these bounds, but "tiny bias" is a thing to reason about later and
        rejection is four lines.
        """
        if bound <= 0:
            raise ValueError("bound must be positive")
        if bound == 1:
            return 0
        span = 1 << 32
        limit = span - (span % bound)
        while True:
            draw = int.from_bytes(self.bytes(4), "big")
            if draw < limit:
                return draw % bound

    def between(self, low: int, high: int) -> int:
        """Uniform integer in [low, high], inclusive at both ends."""
        return low + self.below(high - low + 1)

    def choice(self, options: Sequence[T]) -> T:
        return options[self.below(len(options))]

    def sample(self, options: Sequence[T], count: int) -> list[T]:
        """`count` distinct items, in draw order.

        Partial Fisher-Yates over a copy: O(count) draws and no rejection loop
        when `count` approaches `len(options)`.
        """
        if count > len(options):
            raise ValueError("cannot sample more items than the population holds")
        pool = list(options)
        drawn: list[T] = []
        for index in range(count):
            pick = index + self.below(len(pool) - index)
            pool[index], pool[pick] = pool[pick], pool[index]
            drawn.append(pool[index])
        return drawn

    def shuffled(self, options: Sequence[T]) -> list[T]:
        return self.sample(options, len(options))
