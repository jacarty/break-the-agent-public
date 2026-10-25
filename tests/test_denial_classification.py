"""The module most able to invalidate a result, tested on its own.

`errors.classify` decides whether a failure was a control refusing or something
breaking. Every `held` verdict in the matrix is downstream of that decision:
`schemas.md` §8 counts non-error cases toward the SC-3 threshold, and a
classifier that called every 403 a denial would turn a broken deployment into a
full column of evidence.

The rule it implements runs one way. A denial recorded as an error costs a data
point. An error recorded as a denial manufactures one.
"""

from __future__ import annotations

import pytest
from fakes import FakeGoogleError, access_denied, quota_exceeded
from google.api_core import exceptions

from bta.models import AuthorisationOutcome, Surface
from bta.tools.errors import FirestoreRestError, ToolDenied, classify


def test_bigquery_access_denied_is_an_iam_denial() -> None:
    result = classify(access_denied(), surface=Surface.BIGQUERY)

    assert result.outcome is AuthorisationOutcome.DENIED_IAM
    assert result.reason == "accessDenied"


def test_a_quota_403_is_never_a_denial() -> None:
    """Spike 0.1 finding 2, as the assertion the spike said phase 2 owed it.

    A federated principal without `roles/serviceusage.serviceUsageConsumer` or
    `roles/bigquery.jobUser` fails exactly like this, before any dataset ACL or
    row access policy is evaluated. The estate grants both, so seeing it means
    the grant did not land — a broken deployment, reporting itself, which must
    never read as a working control.
    """
    result = classify(quota_exceeded(), surface=Surface.BIGQUERY)

    assert result.outcome is None, (
        "a quota 403 was recorded as an authorisation denial. This is the "
        "single most misleading failure available in this project: it would "
        "record a false `held` in every L3 BigQuery cell."
    )
    assert result.reason == "quotaExceeded"


@pytest.mark.parametrize(
    "reason",
    ["rateLimitExceeded", "servicesDisabled", "accessNotConfigured", "billingNotEnabled"],
)
def test_the_other_non_authorisation_403s(reason: str) -> None:
    """All of these are 403s that mean the deployment is wrong rather than that
    a caller was refused."""
    assert classify(FakeGoogleError(reason), surface=Surface.BIGQUERY).outcome is None


def test_a_not_found_is_not_a_denial() -> None:
    """The one reason string the fakes actually produce that no other test
    names. A missing object and a refused one are different findings, and
    `notFound` folded into `denied_iam` would report F2 held on a fixture that
    was never planted.

    The design handles it structurally — the authorisation set is an allow-list
    rather than a deny-list — and this asserts the structure rather than
    trusting it.
    """
    result = classify(FakeGoogleError("notFound"), surface=Surface.CLOUD_STORAGE)

    assert result.outcome is None
    assert result.reason == "notFound"


def test_an_unattributable_failure_is_not_a_denial() -> None:
    """The case that matters most, because it is the default.

    A 403 whose reason could not be read has not told us which control refused
    — or whether one did. `insufficient` is a first-class verdict (SC-2) and
    an honest gap is worth more than a confident guess.
    """
    result = classify(RuntimeError("403 Forbidden"), surface=Surface.BIGQUERY)

    assert result.outcome is None
    assert result.error_class == "RuntimeError"


def test_firestore_permission_denied_is_a_document_denial() -> None:
    """`denied_document`, not `denied_iam`. At L3 the refusal comes from a
    Security Rule evaluating the caller's token against the document, which is
    a different mechanism from every other surface in the lab (spike 0.3)."""
    result = classify(FirestoreRestError("PERMISSION_DENIED", 403), surface=Surface.FIRESTORE)

    assert result.outcome is AuthorisationOutcome.DENIED_DOCUMENT
    assert result.reason == "PERMISSION_DENIED"


def test_which_control_refused_is_not_a_property_of_the_string() -> None:
    """The same reason, two surfaces, two controls.

    If this ever collapses to one outcome, an L3 run cannot distinguish a row
    access policy from a Security Rule, and SC-4's `absent` versus
    `insufficient` classification loses the evidence it depends on.
    """
    storage = classify(access_denied(), surface=Surface.CLOUD_STORAGE)
    firestore = classify(FirestoreRestError("PERMISSION_DENIED", 403), surface=Surface.FIRESTORE)

    assert storage.outcome is AuthorisationOutcome.DENIED_IAM
    assert firestore.outcome is AuthorisationOutcome.DENIED_DOCUMENT


def test_firestore_attributes_by_client_path_not_by_surface() -> None:
    """The two Firestore client paths are different mechanisms, and only one of
    them has a document-level control.

    At L1 and L2 the server SDK is not subject to Security Rules at all (spike
    0.3 finding 2), so a `PERMISSION_DENIED` there cannot be a rule refusing a
    document — it can only be the shared estate account missing
    `roles/datastore.user`, which is a broken deployment. Recording it as
    `denied_document` would credit a control that is not running at that level
    with a refusal it did not make.

    This is paired on purpose: the rules denial and the IAM denial in one test,
    because the claim is that they are told apart, not that either one has a
    particular value.
    """
    server_sdk = classify(exceptions.PermissionDenied("denied"), surface=Surface.FIRESTORE)
    caller_token = classify(FirestoreRestError("PERMISSION_DENIED", 403), surface=Surface.FIRESTORE)

    assert server_sdk.outcome is AuthorisationOutcome.DENIED_IAM
    assert caller_token.outcome is AuthorisationOutcome.DENIED_DOCUMENT


def test_a_denial_with_no_surface_is_not_attributed() -> None:
    """A failure before any surface was reached cannot be credited to a control
    on one."""
    assert classify(access_denied(), surface=None).outcome is None


def test_a_capability_denial_carries_its_own_outcome() -> None:
    """The only authorisation this lab performs itself. It says so rather than
    borrowing IAM's name for it (`schemas.md` §6)."""
    denial = ToolDenied(AuthorisationOutcome.DENIED_CAPABILITY, "no capability for role 'admin'")

    result = classify(denial, surface=Surface.WRITE_PATH)

    assert result.outcome is AuthorisationOutcome.DENIED_CAPABILITY
    assert result.reason == "no capability for role 'admin'"


def test_a_grpc_status_is_read_from_the_code() -> None:
    """The Secret Manager and Firestore server clients are gRPC surfaces, and
    they report a status enum rather than a JSON `errors` list."""

    class FakeStatus:
        name = "PERMISSION_DENIED"

    class FakeGrpcError(Exception):
        def code(self) -> FakeStatus:
            return FakeStatus()

    result = classify(FakeGrpcError(), surface=Surface.SECRET_MANAGER)

    assert result.outcome is AuthorisationOutcome.DENIED_IAM
    assert result.reason == "PERMISSION_DENIED"


def test_classification_never_raises() -> None:
    """It runs inside the boundary's failure path. An exception here would
    lose the audit record for the call that failed."""

    class Hostile(Exception):
        @property
        def errors(self) -> list:
            raise RuntimeError("no")

        def code(self):
            raise RuntimeError("no")

    result = classify(Hostile(), surface=Surface.BIGQUERY)
    assert result.outcome is None


# ---------------------------------------------------------------------------
# Against the real exception types, not the fakes
# ---------------------------------------------------------------------------
#
# `google-api-core` is an installed dependency, so its exceptions can be
# constructed in a test with no network and no project. The fakes model the
# JSON `errors[]` shape faithfully and cannot express an ErrorInfo reason or a
# gRPC status, which is where the two misclassifications below were living.


def test_a_bigquery_project_level_denial_is_not_an_authorisation_denial() -> None:
    """The trap this module exists to close, arriving through this module.

    "Caller does not have required permission to use project X" is a missing
    `roles/serviceusage.serviceUsageConsumer` — spike 0.1 finding 2, and what
    `infra/estate/identity.tf` calls the single most misleading failure
    available in this project. BigQuery reports it with the legacy reason
    `forbidden`, which is also Cloud Storage's reason for a genuine object ACL
    denial. One shared reason set recorded this as `denied_iam` and would have
    put a false `held` in every L3 BigQuery cell.
    """
    exc = exceptions.Forbidden(
        "403 Caller does not have required permission to use project example",
        errors=[{"reason": "forbidden", "domain": "global"}],
    )

    assert classify(exc, surface=Surface.BIGQUERY).outcome is None


def test_the_same_reason_is_a_denial_on_cloud_storage() -> None:
    """The other half, and why `forbidden` could not simply be dropped: it is
    what Cloud Storage returns when object IAM refuses, which is F2's L3
    control."""
    exc = exceptions.Forbidden("403", errors=[{"reason": "forbidden", "domain": "global"}])

    assert classify(exc, surface=Surface.CLOUD_STORAGE).outcome is AuthorisationOutcome.DENIED_IAM


def test_a_bigquery_authorisation_denial_still_classifies() -> None:
    """The positive control for the two above. Narrowing the accepted reasons
    is only correct if the reason BigQuery actually uses for authorisation
    still counts."""
    exc = exceptions.Forbidden("403", errors=[{"reason": "accessDenied", "domain": "global"}])

    assert classify(exc, surface=Surface.BIGQUERY).outcome is AuthorisationOutcome.DENIED_IAM


def test_a_grpc_permission_denied_is_a_denial() -> None:
    """`PermissionDenied` is what the Secret Manager and Firestore server
    clients raise. Its `code` is the HTTP integer 403 rather than a callable,
    and it carries no ErrorInfo, so before `grpc_status_code` was read this
    classified as an error — leaving `denied_iam` on Secret Manager unreachable
    in a deployment while looking covered by a test.
    """
    result = classify(exceptions.PermissionDenied("denied"), surface=Surface.SECRET_MANAGER)

    assert result.outcome is AuthorisationOutcome.DENIED_IAM
    assert result.reason == "PERMISSION_DENIED"


def test_a_grpc_resource_exhausted_is_not_a_denial() -> None:
    """The quota case on the gRPC surfaces, for the same reason as the JSON
    one."""
    exc = exceptions.ResourceExhausted("quota")

    assert classify(exc, surface=Surface.SECRET_MANAGER).outcome is None


def test_an_error_infos_reason_wins_over_the_legacy_errors_list() -> None:
    """The priority claim in `_reason_of`'s comment, exercised for real.

    Every other real-exception test in this file constructs `errors=[...]`
    alone, so `.reason` is `None` on all of them (`GoogleAPICallError.reason`
    reads `_error_info`, which is unset unless `error_info=` is passed) and
    they exercise the `errors[]` fallback, not the priority the comment
    describes. A project-level denial genuinely carries both: ErrorInfo
    `USER_PROJECT_DENIED` and a legacy `errors[]` entry of `forbidden`. If the
    legacy list were read first, this would misclassify as `denied_iam` on
    Cloud Storage — `forbidden` is a real authorisation reason there — instead
    of falling through to `USER_PROJECT_DENIED`'s `None`.
    """
    from google.rpc import error_details_pb2

    exc = exceptions.Forbidden(
        "403",
        errors=[{"reason": "forbidden", "domain": "global"}],
        error_info=error_details_pb2.ErrorInfo(
            reason="USER_PROJECT_DENIED", domain="googleapis.com"
        ),
    )

    result = classify(exc, surface=Surface.CLOUD_STORAGE)

    assert result.reason == "USER_PROJECT_DENIED", (
        "the legacy errors[] reason was read ahead of the structured ErrorInfo "
        "one, which is the exact ordering `_reason_of`'s comment says would "
        "throw away the specific answer"
    )
    assert result.outcome is None, (
        "USER_PROJECT_DENIED is a quota/setup failure, not an authorisation "
        "denial (spike 0.1 finding 2) — reading the legacy `forbidden` instead "
        "would have recorded a false `denied_iam` on Cloud Storage"
    )


def test_an_authentication_failure_is_not_a_denial() -> None:
    """401-family. Authentication failed and no control refused, so it does not
    belong in the set whose members fabricate evidence."""
    assert (
        classify(exceptions.Unauthenticated("bad token"), surface=Surface.FIRESTORE).outcome is None
    )
