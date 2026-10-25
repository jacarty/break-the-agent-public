"""The fixture manifest against `schemas.md` §3.

The manifest is the only channel between the generator and the verifier. A
field renamed here does not break a build; it makes the verifier find nothing,
and a verifier that finds nothing scores every cell `held`. That is the most
flattering possible failure and the reason this file checks the shape rather
than trusting it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from bta.generator.manifest import FixtureManifest, FlagFixture
from bta.generator.values import mint

TOP_LEVEL_KEYS = ["schema_version", "seed", "generated_at", "project_id", "flags"]
FLAG_KEYS = ["id", "value", "service", "resource", "owner_team", "planted_via", "field"]

# Minted rather than written as a literal. A literal `BTA{...}` with real value
# characters in it is indistinguishable from a live flag to the pre-commit hook,
# which blocks it and — correctly — offers no allowlist for flag material. These
# are minted from a seed that is not any estate's, so they are the right shape
# and belong to nothing.
SAMPLE_VALUE = mint("unit-test-not-an-estate", "F1")
OTHER_VALUE = mint("unit-test-not-an-estate", "F2")


def _fixture(**overrides) -> FlagFixture:
    base = {
        "id": "F1",
        "value": SAMPLE_VALUE,
        "service": "bigquery",
        "resource": "orbit.incidents/INC-1234",
        "owner_team": "team-b",
        "planted_via": "row_field",
        "field": "postmortem_summary",
    }
    return FlagFixture(**{**base, **overrides})


def test_the_serialised_shape_matches_the_contract(result) -> None:
    payload = result.manifest.to_dict()
    assert list(payload) == TOP_LEVEL_KEYS
    assert payload["schema_version"] == 1
    for flag in payload["flags"]:
        assert list(flag) == FLAG_KEYS


def test_key_order_is_fixed(result) -> None:
    """Byte identity is a property of the serialisation as much as of the
    content, and `json.dumps` preserves insertion order. A dict rebuilt in a
    different order would produce a different fingerprint from the same estate.
    """
    first = result.manifest.to_json()
    assert first.index('"schema_version"') < first.index('"seed"') < first.index('"flags"')


def test_generated_at_is_iso_utc_with_millisecond_precision(result) -> None:
    """`schemas.md` §Conventions. The verifier and the matrix builder both parse
    this, and Python's own `isoformat()` produces microseconds and `+00:00`."""
    stamp = result.manifest.to_dict()["generated_at"]
    assert stamp.endswith("Z")
    assert datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ")
    assert len(stamp.split(".")[1]) == 4  # three digits and the Z


def test_generated_at_is_converted_to_utc_not_relabelled() -> None:
    """A naive strftime on a non-UTC datetime would keep the local wall clock
    and stamp a `Z` on it, which is a manifest that lies about when it was made
    by however many hours the operator's laptop is offset."""
    kolkata = timezone(timedelta(hours=5, minutes=30))
    manifest = FixtureManifest(
        seed="s",
        project_id="p",
        flags=(_fixture(),),
        generated_at=datetime(2026, 8, 14, 15, 0, 0, tzinfo=kolkata),
    )
    assert manifest.to_dict()["generated_at"] == "2026-08-14T09:30:00.000Z"


def test_the_fingerprint_is_not_a_value(result) -> None:
    """It is printed by the CLI and pasted into run notes, so it has to be safe
    to write down."""
    fingerprint = result.manifest.fingerprint()
    assert len(fingerprint) == 32
    assert all(character in "0123456789abcdef" for character in fingerprint)
    leaked = [flag.id for flag in result.manifest.flags if flag.value in fingerprint]
    assert leaked == [], f"a value survived into the fingerprint: {leaked}"


def test_the_fingerprint_moves_when_a_placement_moves() -> None:
    """Excluding `generated_at` is a narrow carve-out. Every other field has to
    be inside the hash, or the exit criterion stops proving anything."""
    stamp = datetime(2026, 8, 14, tzinfo=UTC)
    base = FixtureManifest(seed="s", project_id="p", flags=(_fixture(),), generated_at=stamp)

    variants = [
        FixtureManifest(seed="t", project_id="p", flags=(_fixture(),), generated_at=stamp),
        FixtureManifest(seed="s", project_id="q", flags=(_fixture(),), generated_at=stamp),
        FixtureManifest(
            seed="s",
            project_id="p",
            flags=(_fixture(resource="orbit.incidents/INC-9999"),),
            generated_at=stamp,
        ),
        FixtureManifest(
            seed="s",
            project_id="p",
            flags=(_fixture(value=OTHER_VALUE),),
            generated_at=stamp,
        ),
    ]
    for variant in variants:
        assert variant.fingerprint() != base.fingerprint()


# --------------------------------------------------------------------------
# FlagFixture validation
# --------------------------------------------------------------------------


def test_planted_via_outside_the_closed_set_is_rejected() -> None:
    with pytest.raises(ValueError, match="schemas"):
        _fixture(planted_via="env_var")


def test_a_row_field_without_a_field_is_rejected() -> None:
    """`resource` alone does not say where in the row the value is, and the
    verifier would have nothing to read."""
    with pytest.raises(ValueError, match="requires a field"):
        _fixture(field=None)


def test_a_secret_version_with_a_field_is_rejected() -> None:
    """A payload has no fields. A `field` here means a placer copied another
    placer and the entry describes something that does not exist."""
    with pytest.raises(ValueError, match="takes no field"):
        _fixture(planted_via="secret_version", field="payload")


@pytest.mark.parametrize("value", ["plain-string", "BTA{unterminated", "prefix BTA{x}"])
def test_a_value_outside_the_flag_format_is_rejected(value: str) -> None:
    """FR-17. The verifier matches on the format, so a value that does not carry
    it is a flag that can never be scored `defeated` however far it leaks."""
    with pytest.raises(ValueError, match="FR-17"):
        _fixture(value=value)
