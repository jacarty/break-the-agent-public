"""Exercise all eight tools against a real estate, at one level.

The offline suite establishes that the boundary, the policy table and the tool
bodies behave. It cannot establish that the estate is configured the way the
policy table assumes — that a federated principal can reach BigQuery at all,
that the row access policies filter, that the bucket's conditional bindings
match on the prefix they are supposed to, that Security Rules have propagated.
Those are claims about a deployment, and only a deployment can answer them.

**It cannot be run against a live estate from outside the runtime, and never
could have been.** Retired as phase 2's live-evidence path on 2026-08-29.

`roles/iam.serviceAccountTokenCreator` on the estate service accounts is held by
`bta-runtime@` and by nothing else — which is `infra/estate/identity.tf` working
as designed, and is the arrangement `build-plan.md` §Phase 2 describes when it
says the capability "exists in the process serving an L3 session". An operator's
own account cannot mint, so every probe fails at the credential and not at a
control. Measured rather than inferred: `gcloud auth print-access-token
--impersonate-service-account` returns `PERMISSION_DENIED` for the shared
account, and a full L1 run produced nine consecutive credential failures.

That is why TD-11 stayed open for three phases. Nobody could have closed it.

**Phase 2's live evidence is the suite's dry run instead**, which exercises the
same boundary, the same policy table and the same credential minting through the
deployed process — the one place the impersonation capability exists. What is
lost is running all three credential modes in a single invocation; the suite
covers them across three level-scoped runs.

**What this module is still for.** The probe list is the pairing phase 2 argued
for — a positive and a negative case per surface, in one run, because an
all-denied run reads as a working control and is equally consistent with a
broken deployment. That structure is worth keeping, and the module remains
runnable in any process that *can* mint, which is the runtime itself.

Two things it does that a smoke test would not:

**It runs a positive and a negative case for each surface.** `alice` reads her
own team's incident and then another team's; her own artefact and then another
team's. A run where everything denies is the failure spikes 0.1 and 0.3 both
produced, and it is indistinguishable from a working control unless the
positive case is in the same run.

**It refuses to call an all-denied level a pass.** The summary says so in the
exit code, not in a line somebody has to read.

Usage:

    export BTA_PROJECT_ID=… BTA_DATASET_ID=…      # see bta/config.py
    export BTA_HARNESS_SUBJECT_TOKEN=…            # L3 only, see below
    uv run python -m bta.tools.harness --level L1

The L3 subject token is an Identity Platform ID token for a persona, carrying a
`team` claim. Minting one is the persona picker's job (FR-3) and that arrives
with the site in phase 4; until then the operator supplies one obtained the way
spike 0.1 obtained it — `signJwt` on the minting service account, then
`signInWithCustomToken`.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from bta.audit import InMemorySink
from bta.config import from_env
from bta.credentials import CredentialFactory, GoogleCredentialMinter
from bta.identity import CallerIdentity, SubjectToken
from bta.models import Agent, AuthorisationOutcome, Level
from bta.tools import REGISTRY, EstateClients, ToolContext, invoke

#: The token is only needed at L3. At L1 and L2 every credential is minted by
#: impersonation from the runtime identity, so the caller's claims are carried
#: but never exchanged — which is the vulnerability, and is why a placeholder
#: is honest there and would be a lie at L3.
SUBJECT_TOKEN_VARIABLE = "BTA_HARNESS_SUBJECT_TOKEN"
PLACEHOLDER_TOKEN = "not-exchanged-below-L3"

#: The team the cross-team enumeration probe asks for. `alice` is team-a by
#: default (`BTA_HARNESS_TEAM`), so this is the other side of the same estate —
#: named here rather than derived, because a probe that asked for the caller's
#: own team would be a positive case labelled negative and would pass.
OTHER_TEAM = os.environ.get("BTA_HARNESS_OTHER_TEAM", "team-b")


@dataclass(frozen=True, slots=True)
class Probe:
    """One tool call, and why it is in the run."""

    tool: str
    arguments: dict
    expectation: str  # "positive" or "negative"
    note: str


def probes(own_team_fixtures: dict[str, str], other_team_fixtures: dict[str, str]) -> list[Probe]:
    """Paired by construction.

    The fixture ids come from the operator rather than from the manifest: no
    tool and nothing beside one may read live flag values (`schemas.md` §3),
    and the harness runs in the same process as the tools.
    """
    return [
        Probe("list_services", {}, "positive", "catalogue is unfiltered at every level"),
        Probe(
            "query_logs",
            {"limit": 5},
            "positive",
            "logs carry no row access policy — F3's carrier must stay readable",
        ),
        Probe(
            "search_incidents",
            {"limit": 5},
            "positive",
            "unfiltered enumeration — at L3 the row policy filters the list itself",
        ),
        Probe(
            "search_incidents",
            {"owner_team": OTHER_TEAM, "limit": 5},
            "negative",
            "another team's incidents — at L3 an empty list, not an error",
        ),
        Probe(
            "get_incident",
            {"incident_id": own_team_fixtures["incident_id"]},
            "positive",
            "own team's incident",
        ),
        Probe(
            "get_incident",
            {"incident_id": other_team_fixtures["incident_id"]},
            "negative",
            "another team's incident — at L3 this returns zero rows, not an error",
        ),
        Probe(
            "search_deploys",
            {"service": own_team_fixtures["service"], "include_artefact": True},
            "positive",
            "own team's deploy artefact",
        ),
        Probe(
            "search_deploys",
            {"service": other_team_fixtures["service"], "include_artefact": True},
            "negative",
            "another team's artefact — object IAM decides",
        ),
        Probe("check_policy", {"topic": "onboarding"}, "positive", "no surface, no credential"),
        Probe(
            "get_onboarding_status",
            {"employee_id": own_team_fixtures["employee_id"]},
            "positive",
            "own team's employee record",
        ),
        Probe(
            "get_onboarding_status",
            {"employee_id": other_team_fixtures["employee_id"]},
            "negative",
            "another team's record — Security Rules at L3, server SDK below it",
        ),
        Probe(
            "provision_account",
            {"employee_id": own_team_fixtures["employee_id"], "role": "viewer"},
            "positive",
            "an entitled role",
        ),
        Probe(
            "provision_account",
            {"employee_id": own_team_fixtures["employee_id"], "role": "admin"},
            "negative",
            "an unentitled role — capability scoping at L3",
        ),
        Probe(
            "test_integration",
            {"target": "payments-gateway"},
            "positive",
            "the key is read under the shared credential below L3, the "
            "provisioning identity at L3, and returned to nobody",
        ),
    ]


def run(level: Level, caller: CallerIdentity, *, verbose: bool = True) -> int:
    """Returns a process exit code."""
    config = from_env()
    minter = GoogleCredentialMinter(
        shared_service_account=config.shared_service_account,
        provisioning_service_account=config.provisioning_service_account,
        workload_identity_audience=config.workload_identity_audience,
    )
    clients = EstateClients(config, CredentialFactory(minter))
    sink = InMemorySink()

    own = {
        "incident_id": _required("BTA_HARNESS_OWN_INCIDENT"),
        "service": _required("BTA_HARNESS_OWN_SERVICE"),
        "employee_id": _required("BTA_HARNESS_OWN_EMPLOYEE"),
    }
    other = {
        "incident_id": _required("BTA_HARNESS_OTHER_INCIDENT"),
        "service": _required("BTA_HARNESS_OTHER_SERVICE"),
        "employee_id": _required("BTA_HARNESS_OTHER_EMPLOYEE"),
    }

    results = []
    for probe in probes(own, other):
        spec = REGISTRY[probe.tool]
        ctx = ToolContext(
            caller=caller,
            level=level,
            agent=spec.agent if spec.agent is not Agent.BOTH else Agent.DEPLOY_BOT,
            session_id="harness",
            clients=clients,
            run_id=None,
        )
        invocation = invoke(spec, ctx, probe.arguments, sink=sink)
        results.append((probe, invocation))
        if verbose:
            print(_line(probe, invocation))

    return _summarise(level, results, verbose=verbose)


def _line(probe: Probe, invocation) -> str:
    """One probe, as the operator reads it.

    **`allowed` is not printed for a call that errored.** `schemas.md` §6 closes
    the outcome enum, so a failure no control caused is recorded as `allowed`
    with an `error_class` — deliberate, and documented in `bta.audit`. This
    function was exactly the consumer that docstring warns about: nine probes
    against a live estate rendered as `allowed` when every one of them had
    failed to mint a credential, and the only tell was `rows=-`.

    The summary below always caught it, because `Invocation.allowed` checks the
    error too. What was wrong was the line an operator actually reads while the
    run is in progress.
    """
    record = invocation.record
    mode = record.credential_mode.value if record.credential_mode else "-"
    rows = "-" if record.rows_returned is None else str(record.rows_returned)
    outcome = (
        f"ERRORED:{record.error_class}" if record.errored else record.authorisation_outcome.value
    )
    return (
        f"  {probe.tool:<22} {probe.expectation:<9} "
        f"{outcome:<18} mode={mode:<10} "
        f"rows={rows:<5} reason={invocation.reason or '-'}"
    )


def _summarise(level: Level, results: list, *, verbose: bool) -> int:
    positives = [inv for probe, inv in results if probe.expectation == "positive"]
    failed_positives = [inv for inv in positives if not inv.allowed]

    if verbose:
        print()
        print(f"  level {level.value}: {len(results)} calls, {len(positives)} positive controls")

    if failed_positives:
        # The finding both spikes produced. An all-denied run reads as a
        # working control and is equally consistent with a broken deployment,
        # so it exits non-zero rather than printing a warning nobody reads.
        print(
            f"\nFAIL: {len(failed_positives)} positive control(s) failed at {level.value}. "
            f"No denial in this run is evidence of anything — a deployment that "
            f"denies everything looks identical to a working control "
            f"(spike 0.1 finding 2, spike 0.3 finding 3).",
            file=sys.stderr,
        )
        for inv in failed_positives:
            print(
                f"  {inv.record.tool}: {inv.record.authorisation_outcome.value} "
                f"error={inv.record.error_class} reason={inv.reason}",
                file=sys.stderr,
            )
        return 1

    unattributed = [
        inv
        for _, inv in results
        if inv.error is not None
        and inv.record.authorisation_outcome is AuthorisationOutcome.ALLOWED
    ]
    if unattributed and verbose:
        print(
            f"\n  {len(unattributed)} call(s) failed without an attributable "
            f"authorisation outcome. These are errors, not denials — check the "
            f"reason before reading anything into them."
        )

    return 0


def _required(variable: str) -> str:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise SystemExit(
            f"{variable} is not set. The harness needs fixture ids to probe with, "
            f"and it may not read the fixture manifest to find them (schemas.md §3)."
        )
    return value


def _caller(level: Level) -> CallerIdentity:
    token = os.environ.get(SUBJECT_TOKEN_VARIABLE, "").strip()
    if level is Level.L3 and not token:
        raise SystemExit(
            f"{SUBJECT_TOKEN_VARIABLE} is not set. L3 exchanges the caller's own "
            f"Identity Platform ID token for federated credentials, so there is "
            f"nothing to run without one."
        )
    return CallerIdentity(
        sub=os.environ.get("BTA_HARNESS_PERSONA", "alice"),
        team=os.environ.get("BTA_HARNESS_TEAM", "team-a"),
        subject_token=SubjectToken(token or PLACEHOLDER_TOKEN),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--level", choices=[level.value for level in Level], required=True)
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args(argv)

    level = Level(arguments.level)
    return run(level, _caller(level), verbose=not arguments.quiet)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
