"""Bounded in-process state — TD-27, and the half of TD-24 that closes here.

`Arena` holds gate tokens and browser sessions in dictionaries keyed by a
cookie. FR-7 wants no persistence between browser sessions beyond agent memory
(FR-12), so there is nothing to *store*; what there was, until this module, was
nothing to stop the dictionaries growing for as long as the instance lives.
`min_instance_count = 1` with `cpu_idle = false` means the instance is
long-lived, so growth accumulated across a whole session of attack traffic
rather than being reset by a scale to zero, and `POST /gate` — the one
unauthenticated endpoint on the service — was the endpoint that added a
permanent entry.

**Bounded, not shared.** ADR 0011 records the decision taken with TD-24 rather
than around it: site state stays in process and acquires a ceiling, instead of
moving to Firestore. What remains of TD-24 afterwards is unchanged and still
written down — a browser session is only coherent if it lands on the same
instance, and NFR-3's per-instance counters multiply by `max_instance_count`,
which is 4. Neither of those is memory growth, which is what this fixes.

**Two TTL shapes, because the two maps are not the same kind of thing.**

- Gate tokens expire *absolutely*, at the same age as the cookie's `max_age`.
  The server-side entry outliving the cookie it was issued for is pure residue:
  nothing can present it.
- Browser sessions expire on *idleness*, refreshed on every access. A person
  reading a long transcript between turns must not have their session collected
  underneath them, and a session nobody has touched for an hour is a tab that
  was closed.

**A full map evicts its oldest entry rather than refusing the new one.**
Refusing would keep a legitimate operator from ever being evicted mid-use, at
the cost of letting anyone who fills the map lock everyone out of the gate
until entries expire — converting bounded memory into a clean denial of
service. Evicting means sustained churn can cost an operator their token, and
the recovery for that is entering the access code again.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Entry[V]:
    value: V
    expires_at: float


@dataclass(eq=False)
class TTLMap[V]:
    """A dict with an age limit and a size limit.

    Insertion-ordered, and a sliding map reinserts on read, so the first key is
    the oldest entry under both policies and eviction is a `popitem` from the
    front. Locked because Starlette runs the sync paths on a threadpool and the
    SSE path touches the session map from the event loop.

    `eq=False` because the generated `__eq__` would compare `_lock` and reach
    into `_entries` outside the lock. Two of these are never equal to each
    other; identity is the only comparison that means anything.
    """

    ttl_seconds: float
    max_entries: int
    sliding: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _entries: dict[str, _Entry[V]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Refuse a configuration that would fail as a crash or as silence.

        `max_entries` below 1 makes `put`'s eviction loop run on an empty dict
        and `next(iter({}))` raises `StopIteration`, which inside an async
        handler surfaces as a `RuntimeError` and a 500 on the gate. A
        non-positive `ttl_seconds` expires every entry at the moment it is
        written, so the map looks broken rather than misconfigured — nobody can
        hold a session and no cause is visible. Both are reachable:
        `max_gate_tokens` and `max_sessions` are ordinary `Arena` fields.
        """
        if self.max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

    def put(self, key: str, value: V) -> None:
        with self._lock:
            self._sweep()
            self._entries.pop(key, None)
            while len(self._entries) >= self.max_entries:
                # `next(iter(...))` is the oldest key: insertion order for an
                # absolute map, least-recently-used for a sliding one.
                del self._entries[next(iter(self._entries))]
            self._entries[key] = _Entry(value, time.monotonic() + self.ttl_seconds)

    def get(self, key: str) -> V | None:
        """Expired is indistinguishable from absent, deliberately. Every caller
        of this treats a miss as 'not gated' or 'no session', which is the
        behaviour an expired entry should also produce."""
        if not key:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if time.monotonic() >= entry.expires_at:
                del self._entries[key]
                return None
            if self.sliding:
                del self._entries[key]
                entry.expires_at = time.monotonic() + self.ttl_seconds
                self._entries[key] = entry
            return entry.value

    def values(self) -> list[V]:
        """Live values only. A list rather than a view, because the caller is
        outside the lock by the time it iterates."""
        with self._lock:
            self._sweep()
            return [entry.value for entry in self._entries.values()]

    def __len__(self) -> int:
        with self._lock:
            self._sweep()
            return len(self._entries)

    def _sweep(self) -> None:
        """Drop what has expired. Call under the lock.

        On write and on the size-reporting paths only: there is no background
        task, so a map nobody touches keeps its expired entries — bounded by
        `max_entries` regardless, which is the property that matters.
        """
        now = time.monotonic()
        expired = [key for key, entry in self._entries.items() if now >= entry.expires_at]
        for key in expired:
            del self._entries[key]


__all__ = ["TTLMap"]
