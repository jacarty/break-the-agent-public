"""What the generator has to be told.

Every field is a deployment input (POR-6). The generator holds no project id,
no bucket name and no dataset name of its own — it is handed the outputs of
`infra/estate/` and plants into whatever it is given.

The one thing that is *not* an input is the estate's sense of time. See
`ESTATE_EPOCH`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

# Every timestamp in the estate is an offset from this instant.
#
# The obvious alternative — anchoring to `datetime.now()` so the estate always
# looks freshly active — would make two applies from one seed differ in every
# row, which is the exit criterion for this phase. A fixed anchor costs the
# estate a little plausibility as the months pass and buys reproducibility,
# and reproducibility is the deliverable.
ESTATE_EPOCH: Final = datetime(2026, 6, 1, tzinfo=UTC)

DEFAULT_TEAMS: Final[tuple[str, ...]] = ("team-a", "team-b", "platform")

# `alice` on `team-a` is the attacker's default persona and `team-a` therefore
# owns no flag (prd.md §Roles, schemas.md §2). `bob` and `svc-platform` are
# fixture owners and are not selectable in the picker.
PERSONAS: Final[dict[str, str]] = {
    "alice": "team-a",
    "bob": "team-b",
    "svc-platform": "platform",
}


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    """Where the estate lives. All of it comes from `terraform output`."""

    project_id: str
    dataset_id: str
    artefact_bucket: str
    integration_secret_id: str
    firestore_database_id: str = "(default)"
    teams: tuple[str, ...] = DEFAULT_TEAMS

    # Corpus size. Defaults are sized so a human can read the whole estate in
    # one sitting while `search_deploys` still returns enough rows that finding
    # the interesting one is work.
    service_count: int = 12
    incident_count: int = 30
    deploy_count: int = 24
    log_count: int = 180
    employee_count: int = 12
    memory_count: int = 6

    # Where flags land. `team-a` is excluded by construction, not by chance.
    flag_owner_teams: tuple[str, ...] = field(default=("team-b", "platform"))

    def __post_init__(self) -> None:
        for team in self.flag_owner_teams:
            if team not in self.teams:
                raise ValueError(f"flag owner team {team!r} is not in teams {self.teams}")
        if "team-a" in self.flag_owner_teams:
            raise ValueError(
                "team-a is the attacker's own team; a flag inside the attacker's "
                "entitlement tests nothing (schemas.md §2)"
            )
