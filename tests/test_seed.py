"""The byte source underneath every deterministic choice in the generator."""

from __future__ import annotations

import pytest

from bta.generator.seed import Stream

SEED = "test-orbit-0001"


def test_known_answer() -> None:
    """Pins the derivation itself, not just its self-consistency.

    Every other test here would still pass if the construction were swapped for
    a different one — they compare two streams built the same way. This one
    fails if the key derivation, the counter width, the block size or the digest
    changes, which is the difference between "deterministic today" and
    "reproducible from the spec in `seed.py`". Reseeding the estate after such a
    change is fine; doing it without noticing is not.
    """
    assert Stream(SEED, "known-answer").hex(8) == "efa499b0182a2a69"
    assert [Stream(SEED, "known-answer").below(1000) for _ in range(1)] == [872]


def test_streams_are_addressed_not_sequential() -> None:
    """The property that lets a corpus section be added without moving a flag."""
    first = Stream(SEED, "placement", "F3").hex(16)

    other = Stream(SEED, "placement", "F1")
    other.bytes(4096)
    again = Stream(SEED, "placement", "F3").hex(16)

    assert first == again


def test_purpose_and_seed_both_separate_streams() -> None:
    assert Stream(SEED, "a").hex(16) != Stream(SEED, "b").hex(16)
    assert Stream("other-seed", "a").hex(16) != Stream(SEED, "a").hex(16)


def test_purpose_components_are_unambiguous() -> None:
    """`("ab", "c")` and `("a", "bc")` must not be the same stream — otherwise
    two unrelated call sites could collide by concatenation."""
    assert Stream(SEED, "ab", "c").hex(16) != Stream(SEED, "a", "bc").hex(16)


def test_bytes_are_continuous_across_calls() -> None:
    """Two 32-byte reads equal one 64-byte read: buffer refill must not skip."""
    single = Stream(SEED, "continuity").bytes(64)
    split = Stream(SEED, "continuity")
    assert split.bytes(32) + split.bytes(32) == single


def test_below_stays_in_range_and_covers_it() -> None:
    stream = Stream(SEED, "range")
    draws = [stream.below(6) for _ in range(2000)]
    assert min(draws) == 0
    assert max(draws) == 5
    # Not a statistical test — just enough to catch a bound that collapses to a
    # constant or loses an endpoint.
    assert len(set(draws)) == 6


def test_below_rejects_a_non_positive_bound() -> None:
    with pytest.raises(ValueError):
        Stream(SEED, "range").below(0)


def test_between_is_inclusive_at_both_ends() -> None:
    stream = Stream(SEED, "between")
    draws = [stream.between(3, 5) for _ in range(500)]
    assert set(draws) == {3, 4, 5}
    assert Stream(SEED, "single").between(7, 7) == 7


def test_sample_returns_distinct_items() -> None:
    population = tuple(range(20))
    drawn = Stream(SEED, "sample").sample(population, 8)
    assert len(drawn) == 8
    assert len(set(drawn)) == 8
    assert set(drawn) <= set(population)


def test_sample_of_the_whole_population_is_a_permutation() -> None:
    population = tuple(range(12))
    assert sorted(Stream(SEED, "perm").shuffled(population)) == list(population)


def test_sample_refuses_to_overdraw() -> None:
    with pytest.raises(ValueError):
        Stream(SEED, "over").sample((1, 2, 3), 4)


def test_sample_does_not_mutate_the_population() -> None:
    """`sample` shuffles a copy. A caller passing a module-level tuple of marker
    words would otherwise find them reordered for the rest of the process."""
    population = [1, 2, 3, 4, 5]
    Stream(SEED, "mutate").sample(population, 3)
    assert population == [1, 2, 3, 4, 5]
