"""Deployment inputs, from the environment and nowhere else (POR-5, POR-6).

Every field here names something that differs between one deployment and the
next: a project, a dataset, a bucket, an identity. None of them has a default,
because a default is how a repository becomes single-tenant — one bucket name
in one module and nobody else can run the lab.

POR-6c rules out a template file to go with this. There is no `.env.example`
and no `terraform.tfvars.example`: values come from `terraform output` into the
environment, and a second hand-maintained copy on a laptop drifts from the real
one while looking authoritative.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_PREFIX = "BTA_"


class MissingConfiguration(RuntimeError):
    """A required input was not supplied.

    Names every missing variable at once rather than the first, so an operator
    setting up a shell fixes one round of errors instead of five.
    """


@dataclass(frozen=True, slots=True)
class DeploymentConfig:
    """Where the estate is and who the lab may act as.

    All of it comes from `terraform output` in `infra/estate/`, which is the
    same arrangement `bta.generator.config.GeneratorConfig` uses — the two are
    separate because the generator plants and the tools read, and giving them
    one config object would couple the seeding path to the serving path for no
    gain.
    """

    project_id: str
    project_number: str
    dataset_id: str
    artefact_bucket: str
    integration_secret_id: str
    firestore_database_id: str
    shared_service_account: str
    provisioning_service_account: str
    arena_pool_id: str
    arena_provider_id: str

    @property
    def workload_identity_audience(self) -> str:
        """The STS audience for the arena pool (spike 0.1, hop 3).

        Derived rather than configured. It is a deterministic function of three
        values already present, and an operator asked for it separately would
        eventually supply one that disagrees with them — at which point every
        L3 exchange fails with an audience mismatch that looks nothing like its
        cause.
        """
        return (
            f"//iam.googleapis.com/projects/{self.project_number}"
            f"/locations/global/workloadIdentityPools/{self.arena_pool_id}"
            f"/providers/{self.arena_provider_id}"
        )

    def firestore_documents_url(self) -> str:
        """Base URL for the Firestore REST path used at L3 (spike 0.3).

        POR-5 forbids a hardcoded base URL for anything the deployer controls.
        The Google API host is not one of those — it is the platform, not the
        deployment — and the parts that do vary are interpolated.
        """
        return (
            f"https://firestore.googleapis.com/v1/projects/{self.project_id}"
            f"/databases/{self.firestore_database_id}/documents"
        )


@dataclass(frozen=True, slots=True)
class FixtureMarkers:
    """C1's and F6's values — TD-13, `docs/decisions/0003`.

    Six of the eight flags are planted into the estate. These two are not: C1
    is text in a tool description and F6 is a write-path outcome, so neither is
    a cloud resource and neither is created by Terraform. They still carry
    manifest entries, because the manifest is the only channel the verifier
    reads (`schemas.md` §3) — and §3 also forbids a tool going looking for one.

    So they arrive the way the provider keys do under SEC-9: written to
    Secret Manager by the generator, mounted into the runtime as environment
    variables by the platform, and handed to `ToolSpec.render_description` and
    `EstateClients(write_path_marker=…)` as constructor arguments.

    **Separate from `DeploymentConfig` deliberately.** That object answers
    "where is the estate and who may the lab act as". These are fixture
    material of the highest sensitivity in the repository, and a caller that
    wants one should have to name it.

    **F4 is here for the delivery problem, not the seeding one.** Unlike C1 and
    F6, F4 *is* a cloud resource: the generator writes it as a version of the
    estate's integration-key secret. What it shares with them is having no way
    to reach the process that needs it. At L1 and L2 the key is in the
    Onboarding agent's system prompt by design (FR-14), so
    `bta.levels.composition.render` needs the literal value — and the runtime
    service account deliberately holds no estate access (SEC-11), so it cannot
    read the estate's secret to get it. Reading it there would hand a
    deliberately vulnerable, internet-reachable container a direct path into
    the estate's Secret Manager, which is the access L3 exists to withhold.

    So it arrives the way C1 and F6 do: the operator supplies it from the
    manifest as a hosting-owned secret, and the platform mounts it. Without it
    the Onboarding agent cannot start at L1 or L2 at all — `render` raises
    rather than composing a prompt with the flag quietly missing.

    **There is no default and there is no `None` path.** A deployment that
    renders tool descriptions with no marker has silently removed the control
    flag: C1 leaking at L2 is the behavioural proof that the screener has no
    knowledge of flag syntax (FR-16), and a `held` C1 cell means the fixture or
    the verifier is broken (SC-5). A run with no marker planted would report a
    defect that is not there, against the one flag whose whole job is to be a
    control. `from_env` raises instead.
    """

    c1: str
    f6: str
    f4: str


def markers_from_env(environ: Mapping[str, str] | None = None) -> FixtureMarkers:
    """`BTA_MARKER_C1`, `BTA_MARKER_F6` and `BTA_MARKER_F4`. Raises rather
    than defaulting."""
    source = os.environ if environ is None else environ
    values: dict[str, str] = {}
    missing: list[str] = []
    for field_name in ("c1", "f6", "f4"):
        variable = f"{ENV_PREFIX}MARKER_{field_name.upper()}"
        value = source.get(variable, "").strip()
        if not value:
            missing.append(variable)
        else:
            values[field_name] = value

    if missing:
        raise MissingConfiguration(
            "not set: "
            + ", ".join(missing)
            + ". These are C1's, F6's and F4's values, mounted by the platform "
            "from Secret Manager (docs/decisions/0003). A process that starts "
            "without C1 or F6 has removed the control flag and would report a "
            "fixture defect that is not there (SC-5, FR-16); one without F4 "
            "cannot compose the Onboarding prompt at L1 or L2 at all."
        )
    return FixtureMarkers(**values)


@dataclass(frozen=True, slots=True)
class ModelPin:
    """One pinned model — FR-22, FR-22a.

    `identifier` is recorded on every run result, because judge-style
    comparisons across differing model versions are not meaningful and a matrix
    assembled from unpinned models is not reproducible.

    OD-8 is confirmed at build time against each provider's current API and,
    for Vertex, against regional availability in the deployment region. That
    confirmation is the operator's; this class only refuses to let the value be
    implicit.
    """

    provider: str
    identifier: str


#: Derived rather than restated. A field added to the dataclass without a
#: matching entry here would be silently unreadable from the environment, and
#: the class is the one place worth having the list.
_FIELDS: tuple[str, ...] = tuple(field.name for field in dataclasses.fields(DeploymentConfig))


def from_env(environ: Mapping[str, str] | None = None) -> DeploymentConfig:
    """`BTA_PROJECT_ID`, `BTA_DATASET_ID`, and so on."""
    source = os.environ if environ is None else environ

    values: dict[str, str] = {}
    missing: list[str] = []
    for field_name in _FIELDS:
        variable = f"{ENV_PREFIX}{field_name.upper()}"
        value = source.get(variable, "").strip()
        if not value:
            missing.append(variable)
        else:
            values[field_name] = value

    if missing:
        raise MissingConfiguration(
            "not set: " + ", ".join(missing) + ". All of these come from `terraform output` "
            "in infra/estate/; there is no template file to copy (POR-6c)."
        )

    return DeploymentConfig(**values)


__all__ = [
    "ENV_PREFIX",
    "DeploymentConfig",
    "FixtureMarkers",
    "MissingConfiguration",
    "ModelPin",
    "from_env",
    "markers_from_env",
]
