"""Turning a provider's exception into an authorisation outcome.

This is the smallest module in phase 2 and the one most able to invalidate a
result. Two spikes produced all-denied runs from causes that had nothing to do
with the control under test — a missing project-level role in spike 0.1
(finding 2) and unpropagated Security Rules in spike 0.3 (finding 3) — and in
both cases every assertion about a denial would have passed. A classifier that
maps any 403 to `denied_iam` reproduces that failure permanently and silently,
in the artefact the project publishes.

So the rule here runs one way only:

    An error is a denial when it can be attributed to a control.
    Everything else is an error.

A denial recorded as an error costs one data point and shows up as a lower
`cases_run` (`schemas.md` §8). An error recorded as a denial manufactures
evidence for the claim the lab exists to test. The asymmetry is not close, and
the conservative direction is taken deliberately rather than as a default.

The pairing rule follows from the same place and is enforced in the tests
rather than here: a denial assertion is only evidence alongside a positive case
in the same run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.api_core.exceptions import GoogleAPICallError

from bta.models import AuthorisationOutcome, Surface


class ToolDenied(Exception):
    """A control in this process refused the call.

    Raised by capability checks — the only authorisation this lab performs
    itself. Everything else is refused by the platform, and arrives as a
    provider exception.
    """

    def __init__(self, outcome: AuthorisationOutcome, reason: str) -> None:
        super().__init__(reason)
        self.outcome = outcome
        self.reason = reason


class FirestoreRestError(Exception):
    """A failure on the L3 Firestore path — the caller's own token against the
    REST API, where `firestore.rules` adjudicates.

    It lives here rather than beside the client that raises it because the
    classifier has to tell it apart from a `google.api_core` exception, and
    that distinction is the whole of Firestore's attribution: the two client
    paths are different mechanisms, and only one of them has a document-level
    control (spike 0.3 finding 2). `status` carries the API's own string.
    """

    def __init__(self, status: str, http_status: int) -> None:
        super().__init__(f"firestore rest: {status}")
        self.status = status
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class Classification:
    """`outcome is None` means the call failed rather than being refused."""

    outcome: AuthorisationOutcome | None
    error_class: str | None
    reason: str | None

    @property
    def is_denial(self) -> bool:
        return self.outcome is not None and self.outcome is not AuthorisationOutcome.ALLOWED


# Reasons that mean a control refused, **per surface**. Lowercased at
# comparison time; the providers are not consistent about case.
#
# Scoped by surface rather than kept as one set, and the reason is a real
# failure this table was written to close. `forbidden` is Cloud Storage's
# reason for an object ACL denial — a genuine authorisation refusal. It is
# *also* what BigQuery returns for "Caller does not have required permission to
# use project X", which is a missing `roles/serviceusage.serviceUsageConsumer`
# and says nothing about whether the caller was entitled to the data. One
# shared set therefore recorded spike 0.1 finding 2 as `denied_iam` — the trap
# this module exists to close, arriving through the module itself.
#
# BigQuery's JSON API uses `accessDenied` when authorisation is what refused,
# so nothing is lost by declining to honour `forbidden` there.
_AUTHORISATION_REASONS: dict[Surface, frozenset[str]] = {
    Surface.BIGQUERY: frozenset({"accessdenied", "permission_denied", "iam_permission_denied"}),
    Surface.CLOUD_STORAGE: frozenset(
        {"accessdenied", "forbidden", "permission_denied", "iam_permission_denied"}
    ),
    Surface.SECRET_MANAGER: frozenset(
        {"accessdenied", "permission_denied", "iam_permission_denied"}
    ),
    Surface.FIRESTORE: frozenset({"permission_denied", "forbidden", "iam_permission_denied"}),
}

#: The union, for tests and for readers who want the whole vocabulary in one
#: place. Not used for matching — matching is always per surface.
AUTHORISATION_REASONS: frozenset[str] = frozenset().union(*_AUTHORISATION_REASONS.values())

# Reasons that produce a 403 and mean nothing about authorisation.
#
# `quotaexceeded` and `user_project_denied` are the two spike 0.1 met: a
# federated principal lacking `roles/serviceusage.serviceUsageConsumer` or
# `roles/bigquery.jobUser` fails here, before any dataset ACL or row access
# policy is evaluated. The estate grants both
# (`infra/estate/identity.tf`), so seeing either in a run means the grant did
# not land — a broken deployment reporting itself, which must never read as a
# working control.
NON_AUTHORISATION_REASONS: frozenset[str] = frozenset(
    {
        "quotaexceeded",
        "user_project_denied",
        "ratelimitexceeded",
        "usagelimits",
        "userratelimitexceeded",
        "resource_exhausted",
        "servicesdisabled",
        "service_disabled",
        "accessnotconfigured",
        "billingnotenabled",
        "blocked",
        # 401-family. Authentication failed and no control refused, so it does
        # not belong in a set whose members fabricate evidence.
        "unauthenticated",
        "unauthorized",
    }
)

#: Which control owns a denial on each surface, when one is attributable.
_DENIAL_BY_SURFACE: dict[Surface, AuthorisationOutcome] = {
    Surface.BIGQUERY: AuthorisationOutcome.DENIED_IAM,
    Surface.CLOUD_STORAGE: AuthorisationOutcome.DENIED_IAM,
    Surface.SECRET_MANAGER: AuthorisationOutcome.DENIED_IAM,
    # Firestore is not in this table, because it is the one surface whose
    # owning control is not a property of the surface. See `_denial_for`.
}


def _denial_for(surface: Surface, exc: BaseException) -> AuthorisationOutcome | None:
    """Which control refused, for a failure already known to be attributable.

    Firestore needs the exception and not just the surface, and it is the only
    surface that does — because it is the only one whose *client path* varies
    (spike 0.3 finding 2). At L3 the caller's own token goes to the REST API
    and `firestore.rules` adjudicates a document: `denied_document`. At L1 and
    L2 the server SDK is not subject to Security Rules at all, so there is no
    document-level control there to do any refusing, and a `PERMISSION_DENIED`
    can only mean the shared estate account is missing `roles/datastore.user`
    — a broken deployment: `denied_iam`.

    Keying on the exception type rather than on the level is deliberate. The
    two client paths raise different exceptions, so this reports the mechanism
    that actually refused rather than the mechanism the level was supposed to
    be using. That keeps the diagnostic in `onboarding.py` alive: a Firestore
    denial arriving as `denied_iam` in an L3 run means the tool was handed a
    server client and the document control is gone. Keying on the level would
    have made every L3 Firestore denial read as `denied_document` whichever
    client produced it, which is the reading that failure mode hides behind.
    """
    if surface is Surface.FIRESTORE:
        if isinstance(exc, FirestoreRestError):
            return AuthorisationOutcome.DENIED_DOCUMENT
        return AuthorisationOutcome.DENIED_IAM
    return _DENIAL_BY_SURFACE.get(surface)


def classify(exc: BaseException, *, surface: Surface | None) -> Classification:
    """Provider exception in, outcome out. Never raises."""
    error_class = type(exc).__name__

    if isinstance(exc, ToolDenied):
        return Classification(exc.outcome, error_class, exc.reason)

    reason = _reason_of(exc)
    normalised = reason.lower() if reason else None

    if normalised in NON_AUTHORISATION_REASONS:
        # A 403 that says nothing about authorisation. Named in the result so
        # the operator can tell a misconfigured deployment from a held cell.
        return Classification(None, error_class, reason)

    if surface is not None and normalised in _AUTHORISATION_REASONS.get(surface, frozenset()):
        outcome = _denial_for(surface, exc)
        if outcome is not None:
            return Classification(outcome, error_class, reason)

    # Includes the case that matters most: a 403 whose reason could not be
    # read. Unattributable, therefore not a denial.
    return Classification(None, error_class, reason)


def _reason_of(exc: BaseException) -> str | None:
    """Dig the machine-readable reason out, without depending on which client
    library raised.

    Deliberately structural rather than a message match. Provider messages are
    prose, they are localised, and they change; a classifier keyed on them
    decays into false `error` classifications that show up months later as
    thinning evidence in `cases_run` with nothing pointing at the cause.

    Every read is guarded. These attributes are properties on some client
    libraries and properties can raise — and this function runs on the
    boundary's failure path, where an exception would lose the audit record for
    the call that failed. An unreadable reason is a `None`, which classifies as
    an error, which is the safe direction.
    """
    # Order matters. `GoogleAPICallError.reason` is the ErrorInfo reason — the
    # specific, structured one — and the legacy `errors[]` list is the generic
    # one that would otherwise shadow it. A project-level denial carries
    # ErrorInfo `USER_PROJECT_DENIED` and a legacy `forbidden`, and reading the
    # legacy entry first is how the specific answer gets thrown away.
    #
    # `status` is the Firestore REST path, which raises with the API's own
    # status string.
    # `reason` is read only off Google's own exception hierarchy. It is a
    # common attribute name — `urllib.error.HTTPError.reason` is the HTTP
    # phrase, so "Forbidden" would arrive here looking like a machine-readable
    # reason — and this module's whole design is that the set of strings which
    # can produce a denial should be hard to get into.
    if isinstance(exc, GoogleAPICallError):
        value: Any = _read(exc, "reason")
        if isinstance(value, str) and value:
            return value

    # `status` is this project's own `FirestoreRestError`, carrying the API's
    # status string from the L3 REST path.
    status = _read(exc, "status")
    if isinstance(status, str) and status:
        return status

    # google-api-core, JSON APIs: `errors` is a list of {"reason": ...}.
    errors = _read(exc, "errors")
    if isinstance(errors, list):
        for entry in errors:
            if isinstance(entry, dict) and entry.get("reason"):
                return str(entry["reason"])

    # google-api-core's gRPC surfaces — the Firestore server SDK and Secret
    # Manager. `PermissionDenied` carries `grpc_status_code`, while its `code`
    # is the HTTP integer 403, so the `code()` branch below never fires for it.
    # Without this read, `denied_iam` on Secret Manager is unreachable in a
    # deployment while looking covered in the tests.
    grpc_name = _read(_read(exc, "grpc_status_code"), "name")
    if isinstance(grpc_name, str) and grpc_name:
        return grpc_name

    # grpc.Call proper — `code()` returns a StatusCode enum.
    code = _read(exc, "code")
    if callable(code):
        name = _read(code, "name", call_first=True)
        if isinstance(name, str) and name:
            return name

    return None


def _read(obj: Any, attribute: str, *, call_first: bool = False) -> Any:
    """`getattr`, but a raising property answers `None` like a missing one."""
    try:
        if call_first:
            return getattr(obj(), attribute, None)
        return getattr(obj, attribute, None)
    except Exception:  # a client that raises here has told us nothing
        return None


__all__ = [
    "AUTHORISATION_REASONS",
    "FirestoreRestError",
    "NON_AUTHORISATION_REASONS",
    "Classification",
    "ToolDenied",
    "classify",
]
