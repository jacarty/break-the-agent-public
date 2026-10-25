"""The phase 1 exit criterion: two applies from one seed produce byte-identical
manifests.

It is checked here on `fingerprint()` rather than on the raw JSON, for the
reason in `manifest.py`: `schemas.md` §3 mandates `generated_at`, which is
wall-clock time and cannot be identical between two applies. Both contracts
stand as written and the criterion is checked on the manifest with that one
field removed — and `test_the_fingerprint_ignores_generated_at_and_nothing_else`
below is what stops that exclusion from quietly widening.

Two applies from one seed are simulated by two calls to `build`, which is what
the planner/applier split buys: no project, no credentials, no network.
"""

from __future__ import annotations

from dataclasses import replace

from conftest import FROZEN_CLOCK, SEED

from bta.generator import build


def test_two_builds_from_one_seed_agree(config, result) -> None:
    again = build(SEED, config, generated_at=FROZEN_CLOCK)

    assert again.manifest.fingerprint() == result.manifest.fingerprint()
    assert again.manifest.to_json() == result.manifest.to_json()


def test_the_whole_estate_agrees_not_just_the_flags(config, result) -> None:
    """The manifest fingerprint proves the values and their host rows landed
    identically. This proves the estate around them did — a corpus change that
    reshuffled every incident but happened to leave the flag rows alone would
    pass the first check, and it is a real change to what the agents read."""
    again = build(SEED, config, generated_at=FROZEN_CLOCK)

    assert again.plan.fingerprint() == result.plan.fingerprint()
    assert again.plan.to_json() == result.plan.to_json()


def test_a_different_seed_produces_a_different_estate(config, result) -> None:
    """Otherwise the first test would pass on a generator that ignored the seed
    entirely, which is the failure mode a determinism check is most likely to
    have."""
    other = build("test-orbit-0002", config, generated_at=FROZEN_CLOCK)

    assert other.manifest.fingerprint() != result.manifest.fingerprint()
    assert other.plan.fingerprint() != result.plan.fingerprint()


def test_the_fingerprint_ignores_generated_at_and_nothing_else(config, result) -> None:
    """Two builds differing only in the timestamp: same fingerprint, different
    bytes. The second half is the load-bearing one — a fingerprint that also
    ignored, say, `project_id` would still pass the first."""
    later = build(SEED, config, generated_at=FROZEN_CLOCK.replace(year=2027))

    assert later.manifest.fingerprint() == result.manifest.fingerprint()
    assert later.manifest.to_json() != result.manifest.to_json()


def test_project_id_reaches_the_manifest_and_the_estate(config, result) -> None:
    """POR-6: the project is an input, not a constant. It appears in the
    manifest and in every synthetic log name, so a build for another project
    must differ — and if it did not, the fingerprint would be blind to the one
    field that says which estate a manifest describes."""
    elsewhere = build(
        SEED, replace(config, project_id="bta-other-project"), generated_at=FROZEN_CLOCK
    )

    assert elsewhere.manifest.fingerprint() != result.manifest.fingerprint()
    assert elsewhere.manifest.to_dict()["project_id"] == "bta-other-project"


def test_values_depend_on_the_seed_alone(config, result) -> None:
    """Deployment inputs move where a flag lands; they must not move what it is.

    Values are minted from `(seed, flag_id)` and nothing else, which is what
    lets an estate be re-planted into a renamed bucket without invalidating a
    transcript captured before the rename.
    """
    renamed = build(
        SEED,
        replace(config, artefact_bucket="bta-renamed-artefacts", dataset_id="orbit_two"),
        generated_at=FROZEN_CLOCK,
    )

    before = {flag.id: flag.value for flag in result.manifest.flags}
    after = {flag.id: flag.value for flag in renamed.manifest.flags}
    # Reduced to the ids that differ. `assert before == after` would print both
    # dicts — every live value — into the failure output. See conftest.
    moved = sorted(flag_id for flag_id in before if before[flag_id] != after.get(flag_id))
    assert moved == [], f"values moved when a deployment input changed: {moved}"


def test_placement_is_stable_across_builds(config, result) -> None:
    """Where each value landed, without the values. Safe to print on failure,
    which is the point of asserting on this rather than on the manifest."""
    again = build(SEED, config, generated_at=FROZEN_CLOCK)

    def placement(generated) -> dict[str, tuple]:
        return {
            flag.id: (flag.service, flag.resource, flag.owner_team, flag.planted_via, flag.field)
            for flag in generated.manifest.flags
        }

    assert placement(again) == placement(result)
