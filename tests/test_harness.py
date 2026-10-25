"""The harness's judgement, tested offline.

The harness itself needs a deployment and has not been run against one. What
can be tested here is the part that decides whether a live run means anything:
the pairing of its probes, and its refusal to call an all-denied level a pass.

That refusal is the whole reason the harness prints an exit code rather than a
table. Spike 0.1 and spike 0.3 each produced a run where every call was refused
for reasons unrelated to any control, and a human reading a table of denials
sees a working lab.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from bta.audit import AuditRecord
from bta.models import Agent, AuthorisationOutcome, CredentialMode, Level
from bta.tools import REGISTRY
from bta.tools.harness import Probe, _summarise, probes

OWN = {"incident_id": "INC-1001", "service": "search-api", "employee_id": "emp-a"}
OTHER = {"incident_id": "INC-4471", "service": "checkout-api", "employee_id": "emp-b"}


@dataclass
class FakeInvocation:
    record: AuditRecord
    allowed: bool
    error: Exception | None = None
    reason: str | None = None


def _record(tool: str, outcome: AuthorisationOutcome) -> AuditRecord:
    from conftest import FROZEN_CLOCK

    return AuditRecord(
        id="TC-test",
        ts=FROZEN_CLOCK,
        session_id="harness",
        persona_sub="alice",
        team="team-a",
        level=Level.L3,
        agent=Agent.DEPLOY_BOT,
        tool=tool,
        parameters={},
        credential_mode=CredentialMode.FEDERATED,
        authorisation_outcome=outcome,
    )


def test_every_probe_names_a_registered_tool() -> None:
    """A probe for a tool that no longer exists would silently stop covering
    it."""
    for probe in probes(OWN, OTHER):
        assert REGISTRY[probe.tool].name == probe.tool


def test_every_surface_with_a_negative_probe_has_a_positive_one() -> None:
    """The pairing rule, applied to the live run rather than to a test.

    A negative probe on a tool with no positive probe is a denial nobody can
    interpret — the exact reading both spikes produced.
    """
    all_probes = probes(OWN, OTHER)
    positives = {probe.tool for probe in all_probes if probe.expectation == "positive"}
    negatives = {probe.tool for probe in all_probes if probe.expectation == "negative"}

    assert negatives <= positives, sorted(negatives - positives)


def test_every_tool_is_exercised() -> None:
    """Phase 2's exit criterion is every tool, not every tool somebody
    remembered."""
    assert {probe.tool for probe in probes(OWN, OTHER)} == {spec.name for spec in REGISTRY}


def test_a_run_whose_positive_controls_failed_is_not_a_pass(capsys) -> None:
    results = [
        (
            Probe("list_services", {}, "positive", ""),
            FakeInvocation(_record("list_services", AuthorisationOutcome.DENIED_IAM), False),
        ),
        (
            Probe("get_incident", {}, "negative", ""),
            FakeInvocation(_record("get_incident", AuthorisationOutcome.DENIED_IAM), False),
        ),
    ]

    assert _summarise(Level.L3, results, verbose=False) == 1
    assert "positive control" in capsys.readouterr().err


def test_a_run_with_working_positives_and_real_denials_passes() -> None:
    results = [
        (
            Probe("list_services", {}, "positive", ""),
            FakeInvocation(_record("list_services", AuthorisationOutcome.ALLOWED), True),
        ),
        (
            Probe("get_onboarding_status", {}, "negative", ""),
            FakeInvocation(
                _record("get_onboarding_status", AuthorisationOutcome.DENIED_DOCUMENT), False
            ),
        ),
    ]

    assert _summarise(Level.L3, results, verbose=False) == 0


def test_an_errored_probe_is_not_printed_as_allowed(capsys) -> None:
    """The defect a live run exposed on 2026-08-29.

    Nine probes against a real estate failed to mint a credential — the
    operator's account holds no `serviceAccountTokenCreator` on the estate
    service accounts — and every one printed `allowed`. `schemas.md` §6 closes
    the outcome enum, so a failure no control caused is recorded as `allowed`
    with an `error_class`; `bta.audit` documents that and warns that a consumer
    reading the outcome alone will miscount. This line was that consumer.
    """
    from bta.tools.harness import _line

    record = _record("get_incident", AuthorisationOutcome.ALLOWED)
    record = replace(record, error_class="RefreshError")
    line = _line(Probe("get_incident", {}, "positive", ""), FakeInvocation(record, False))

    assert "ERRORED:RefreshError" in line
    assert "allowed" not in line


def test_a_genuine_denial_is_still_printed_as_a_denial() -> None:
    """The opposite miscount, and the more damaging one.

    `ToolDenied` sets an `error_class` *and* an outcome, so a consumer keying on
    "there was an error class" would erase every L3 denial — which is what the
    L3 column is made of.
    """
    from bta.tools.harness import _line

    record = replace(
        _record("get_incident", AuthorisationOutcome.DENIED_IAM), error_class="Forbidden"
    )
    line = _line(Probe("get_incident", {}, "negative", ""), FakeInvocation(record, False))

    assert "denied_iam" in line
    assert "ERRORED" not in line
