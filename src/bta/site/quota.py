"""NFR-3 and NFR-3a — per-persona rate limiting, and the suite's bypass.

Three limits, and they are not the same kind of thing:

| Limit | Scope | Bypassable |
| -- | -- | -- |
| requests per minute | per persona | yes, NFR-3a |
| requests per persona per day | per persona | yes, NFR-3a |
| daily token ceiling | the deployment | **no** |
| gate attempts per minute | per client address | not offered |
| gate attempts per hour | per client address | not offered |
| gate attempts per hour | all addresses, per instance | not offered |

**The gate limits are a fourth thing and share only the machinery.** `POST
/gate` had no limit at all until TD-27 — the per-minute check ran in `message`,
which is behind the gate, so the one unauthenticated endpoint on an
internet-reachable service was the unlimited one. They are counted in their own
buckets rather than the persona ones, so a gate attempt neither spends an
interactive budget nor is refused by the token ceiling: a caller who cannot get
through the front door for the rest of the day because the *model* budget is
spent would be a limit doing something other than what it says.

There is no bypass. NFR-3a exempts the suite from per-persona rate limiting so
SC-3's thresholds are reachable; the suite presents the access code once and
then holds a cookie, so nothing it does needs the gate limits lifted.

The global hourly ceiling bounds distributed guessing, and its cost is named:
anyone can spend it, and while it is spent the operator is refused at the front
door too. It is defaulted high enough that ordinary use cannot reach it and low
enough that automated guessing is three orders of magnitude off what an open
endpoint offers. FR-2 remains true throughout — the code is not a security
boundary, and this is a budget control, not a claim to have made it one.

**"Global" means global to one instance**, and that qualification matters more
here than it does above. The per-instance weakening TD-24 records applies to
all six counters, but the other five are per-persona or per-address limits that
degrade proportionally; this one exists specifically to be a single
deployment-wide backstop, and there is no such thing while `max_instance_count`
is 4. An attacker whose requests Cloud Run spreads across instances gets four
uncoordinated buckets rather than one, so the effective ceiling is up to four
times the configured value. Divide the default by the instance count if a true
deployment-wide bound is wanted before the shared store lands (TD-24, phase 5).

**The ceiling is not bypassable and that is the whole design.** NFR-3a exempts
the suite from rate limiting because SC-3's thresholds are unreachable inside
any acceptable runtime otherwise. It does not exempt it from the budget: the
ceiling is sized an order of magnitude below the SEC-3 cap, and a bypass token
that lifted it would put the only thing standing between a runaway suite and a
detached billing account in the hands of whoever holds a deployment secret.

The bypass token is a deployment secret with the same handling as the access
code — held by the operator, never in the repository, never in agent context.
It removes the only rate control on a deliberately vulnerable, internet-facing
service, and `README.md` says so where the operator will read it.

**In-process counters, deliberately, and the cost is named.** Cloud Run may run
up to `max_instance_count` instances, so these counters are per instance and
the effective limit is the configured one multiplied by the instance count.
That is a real weakening of NFR-3 and it is written down rather than implied.
Making it exact needs a shared store — Firestore, which the estate already has
— and that is a phase 5 concern, because the suite is the only client that
generates enough traffic for the difference to matter and the suite bypasses
these limits anyway. The ceiling, which is the limit that protects the budget,
is the one this arrangement under-counts; TD is filed rather than hidden.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from dataclasses import dataclass, field

BYPASS_TOKEN_ENV = "BTA_SUITE_BYPASS_TOKEN"
BYPASS_HEADER = "x-bta-suite-bypass"


def _retry_after(stamps: list[float], window: float, now: float) -> int:
    """Seconds until the oldest stamp leaves the window, rounded up.

    A flat `3600` on an hourly limit tells a caller one attempt over to wait an
    hour when the real wait may be seconds. On the *global* bucket it is also
    an oracle: a flat hour returned to a caller's first-ever request says the
    deployment-wide ceiling is currently spent, which is a coarse read on other
    traffic to the service that the wrong-code response does not offer.
    """
    if not stamps:
        return int(window)
    return max(1, int(window - (now - stamps[0])) + 1)


class QuotaExceeded(RuntimeError):
    """A limit was hit. Carries which one, so the site can say."""

    def __init__(self, limit: str, retry_after_seconds: int) -> None:
        super().__init__(f"quota exceeded: {limit}")
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class Limits:
    requests_per_minute: int = 20
    requests_per_persona_per_day: int = 300
    daily_token_ceiling: int = 2_000_000
    gate_attempts_per_minute: int = 10
    gate_attempts_per_hour: int = 60
    gate_attempts_per_hour_global: int = 600

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Limits:
        source = os.environ if environ is None else environ

        def read(name: str, default: int) -> int:
            raw = source.get(name)
            return default if raw is None or not raw.strip() else int(raw)

        return cls(
            requests_per_minute=read("BTA_RATE_LIMIT_PER_MINUTE", 20),
            requests_per_persona_per_day=read("BTA_REQUESTS_PER_PERSONA_PER_DAY", 300),
            daily_token_ceiling=read("BTA_DAILY_TOKEN_CEILING", 2_000_000),
            gate_attempts_per_minute=read("BTA_GATE_ATTEMPTS_PER_MINUTE", 10),
            gate_attempts_per_hour=read("BTA_GATE_ATTEMPTS_PER_HOUR", 60),
            gate_attempts_per_hour_global=read("BTA_GATE_ATTEMPTS_PER_HOUR_GLOBAL", 600),
        )


@dataclass
class Quota:
    """Counters for one instance. Thread-safe because Starlette runs handlers
    on a threadpool for sync paths and the token ceiling is written from the
    streaming path after a turn completes."""

    limits: Limits = field(default_factory=Limits)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _minute: dict[str, list[float]] = field(default_factory=dict, repr=False)
    _day: dict[str, list[float]] = field(default_factory=dict, repr=False)
    _tokens_today: int = 0
    _token_day: int = 0
    _gate_minute: dict[str, list[float]] = field(default_factory=dict, repr=False)
    _gate_hour: dict[str, list[float]] = field(default_factory=dict, repr=False)
    _gate_global: list[float] = field(default_factory=list, repr=False)

    def _prune(self, bucket: dict[str, list[float]], persona: str, window: float) -> list[float]:
        now = time.monotonic()
        stamps = [t for t in bucket.get(persona, []) if now - t < window]
        bucket[persona] = stamps
        return stamps

    def _roll(self) -> None:
        """Advance the token day if the clock has. Call under the lock.

        Lives here rather than in `record_tokens` alone because the ceiling is
        *read* on a path that can stop the writer ever running again: once
        `_tokens_today` reaches the ceiling, `check_and_record` raises before
        the model is reached, so the turn never completes, so `record_tokens`
        is never called — and the day never advances. The counter could only
        reset by restarting the instance.
        """
        today = int(time.time() // 86_400)
        if today != self._token_day:
            self._token_day = today
            self._tokens_today = 0

    def check_and_record(self, persona: str, *, bypass: bool) -> None:
        """Called once per interactive turn, before the model is reached.

        `bypass` lifts the two per-persona limits and never the ceiling.
        """
        with self._lock:
            self._roll()
            if self._tokens_today >= self.limits.daily_token_ceiling:
                # Checked first, and checked even when bypassing. This is the
                # limit that protects the budget.
                raise QuotaExceeded("daily_token_ceiling", retry_after_seconds=3600)

            if bypass:
                return

            minute = self._prune(self._minute, persona, 60.0)
            if len(minute) >= self.limits.requests_per_minute:
                raise QuotaExceeded("requests_per_minute", retry_after_seconds=60)

            day = self._prune(self._day, persona, 86_400.0)
            if len(day) >= self.limits.requests_per_persona_per_day:
                raise QuotaExceeded("requests_per_persona_per_day", retry_after_seconds=3600)

            now = time.monotonic()
            minute.append(now)
            day.append(now)

    def check_gate(self, client_key: str, *, bypass: bool = False) -> None:
        """TD-27. Called once per `POST /gate`, *before* the code is compared.

        Counting the attempt before validating it is deliberate on two counts.
        A correct code costs the same as a wrong one, so a caller holding the
        code cannot churn gate tokens at network speed; and right and wrong
        codes remain indistinguishable in behaviour as well as in wording,
        which is what `bta.site.gate` is careful about for the comparison
        itself.

        The global ceiling is checked first and consumed by every caller. See
        the module docstring for what that costs.

        **The NFR-3a bypass lifts this, as it lifts the per-persona limits.**
        The requirement exempts the suite from "the per-persona limits in
        NFR-3"; this limit is TD-27's and came later, but the argument is the
        same and it bites harder here. The suite opens a fresh session per
        phase by construction (`schemas.md` §4), so it gates more often than it
        sends turns — without this the first reference run burns its case set
        into permanent errors and every cell reports `insufficient` for a
        reason that has nothing to do with any control being tested.

        What it costs, stated rather than implied: a holder of the bypass token
        can guess the access code at network speed. That is acceptable because
        the bypass token is itself a deployment secret with the same handling
        as the access code (NFR-3a) — anyone holding it already has what
        guessing would win — and because FR-2 is explicit that the gate is
        drive-by traffic control and never a security boundary. The daily token
        ceiling still applies to bypassed callers; the bypass lifts rate
        limiting and never the budget.

        The runner additionally reuses one gate token across a case's phases,
        so it gates about once per run rather than once per phase. This exists
        so that a re-gate — a token expiring mid-run — is not a failed
        execution, not so the suite can gate freely.
        """
        if bypass:
            return
        with self._lock:
            self._sweep_gate_buckets()
            now = time.monotonic()
            self._gate_global = [t for t in self._gate_global if now - t < 3600.0]
            if len(self._gate_global) >= self.limits.gate_attempts_per_hour_global:
                raise QuotaExceeded(
                    "gate_attempts_per_hour_global",
                    retry_after_seconds=_retry_after(self._gate_global, 3600.0, now),
                )

            minute = self._prune(self._gate_minute, client_key, 60.0)
            if len(minute) >= self.limits.gate_attempts_per_minute:
                raise QuotaExceeded(
                    "gate_attempts_per_minute",
                    retry_after_seconds=_retry_after(minute, 60.0, now),
                )

            hour = self._prune(self._gate_hour, client_key, 3600.0)
            if len(hour) >= self.limits.gate_attempts_per_hour:
                raise QuotaExceeded(
                    "gate_attempts_per_hour",
                    retry_after_seconds=_retry_after(hour, 3600.0, now),
                )

            minute.append(now)
            hour.append(now)
            self._gate_global.append(now)

    def _sweep_gate_buckets(self) -> None:
        """Drop addresses with nothing left in the window. Call under the lock.

        Without this the fix for one unbounded structure creates another: the
        persona buckets are bounded because personas are a closed set, but a
        caller rotating source addresses gets a new key each time.

        **It sweeps by window, not by emptiness.** An earlier version deleted
        only keys whose list was already empty, which reclaimed nothing at all:
        `_prune` empties a list only for the key of the request being handled,
        so an address that made one attempt and never came back kept a stale
        stamp and its key for the life of the instance. That is TD-27's own
        shape moved one module across, and the creation rate the global ceiling
        bounds is not the same thing as residency — 600 an hour is 14,400 keys
        a day on an instance that never restarts.

        Sweeping the whole bucket costs O(live keys) per attempt, and the live
        key count *is* bounded by the global ceiling: nothing can add a key
        without first spending one of those.
        """
        now = time.monotonic()
        for bucket, window in ((self._gate_minute, 60.0), (self._gate_hour, 3600.0)):
            for key, stamps in list(bucket.items()):
                live = [t for t in stamps if now - t < window]
                if live:
                    bucket[key] = live
                else:
                    del bucket[key]

    def record_tokens(self, used: int) -> None:
        """After a turn. Counts against the ceiling for bypassed requests too."""
        with self._lock:
            self._roll()
            self._tokens_today += max(0, used)

    @property
    def tokens_today(self) -> int:
        return self._tokens_today

    @property
    def gate_addresses_tracked(self) -> int:
        """How many client addresses currently hold a gate bucket. Exposed so a
        test can assert the fix for TD-27 did not relocate the unbounded growth
        into this module.

        The larger of the two buckets, not the minute one. `_gate_hour` retains
        a key sixty times longer, so reporting `_gate_minute` alone would have
        measured the smaller of the two structures this property exists to
        bound — and would have looked healthy while the other one grew.
        """
        with self._lock:
            return max(len(self._gate_minute), len(self._gate_hour))


def bypass_granted(header_value: str | None, environ: dict[str, str] | None = None) -> bool:
    """NFR-3a. A missing or empty configured token grants nothing, so a
    deployment that failed to mount the secret does not accidentally exempt
    every caller."""
    source = os.environ if environ is None else environ
    configured = source.get(BYPASS_TOKEN_ENV, "")
    if not configured or not header_value:
        return False
    return hmac.compare_digest(header_value, configured)


__all__ = [
    "BYPASS_HEADER",
    "BYPASS_TOKEN_ENV",
    "Limits",
    "Quota",
    "QuotaExceeded",
    "bypass_granted",
]
