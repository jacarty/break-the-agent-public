"""The wrapper. Every tool call goes through here, and every one is audited.

VER-4 says every tool call emits a structured audit record at every level,
including calls that were denied. The way to get that is not to remember: it is
for the record to be emitted by the only code path that can call a tool, in a
`finally`, from data the tool did not supply.

What this module refuses to do is as load-bearing as what it does.

**It does not raise for a tool failure.** A tool that failed produces an
`Invocation` with an outcome and an error class, because in phase 3 the loop
has to hand something back to the model, and an exception crossing this
boundary would be an exit route with no record written.

Two things do cross it, and a loop author should know both. A
`CredentialPolicyViolation` when a call spanned two credential modes — no
single record could describe it honestly, so none is written. And
`KeyboardInterrupt` or `SystemExit`, which are recorded and then re-raised:
they are not tool failures, and an operator who pressed Ctrl-C during a suite
run should get their interrupt rather than a run that continues.

**It does not put the exception's message in the record.** `error_class` is a
type name. Provider messages quote the resource, and at L3 the Onboarding path
exists precisely so that a failure discloses nothing (FR-14, SEC-9) — an audit
record that captured the message would reintroduce the leak on the surface the
control was built to close.

**It does not decide whether a failure was a denial.** `errors.classify` does,
conservatively, for the reasons in that module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from bta.audit import AuditRecord, AuditSink
from bta.credentials import CredentialPolicyViolation
from bta.models import AuthorisationOutcome, CredentialMode, Surface

from .context import ToolContext, ToolResult
from .errors import Classification, classify
from .registry import ToolSpec


def _new_id() -> str:
    return f"TC-{uuid.uuid4().hex[:10]}"


@dataclass(frozen=True, slots=True)
class Invocation:
    """The outcome of one tool call, for the caller that will render it.

    `reason` is the provider's machine-readable reason where there was one. It
    is on this object and not on the audit record because `schemas.md` §6 has
    no field for it, and because a harness operator needs to tell an
    `accessDenied` from a `quotaExceeded` while the record needs only to say
    which control refused.
    """

    record: AuditRecord
    result: ToolResult | None
    error: BaseException | None
    reason: str | None = None

    @property
    def outcome(self) -> AuthorisationOutcome:
        return self.record.authorisation_outcome

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorisationOutcome.ALLOWED and self.error is None


class UnknownParameter(TypeError):
    """The call named a parameter the tool does not take.

    Its own type because in phase 3 this is a model producing a malformed tool
    call, which is ordinary behaviour to be recorded and fed back — not a
    defect in the lab.
    """


def invoke(
    spec: ToolSpec,
    ctx: ToolContext,
    arguments: Mapping[str, Any] | None = None,
    *,
    sink: AuditSink,
    id_factory: Callable[[], str] = _new_id,
) -> Invocation:
    """Call a tool, audit it, and return what happened."""
    arguments = dict(arguments or {})
    started = ctx.now()

    # Where this call's surface use begins. Contexts are built per call, but a
    # caller that reuses one must not have the previous call's credential
    # recorded against this one.
    surface_mark = len(ctx.surfaces_used)

    result: ToolResult | None = None
    error: BaseException | None = None
    classification = Classification(AuthorisationOutcome.ALLOWED, None, None)

    def build(outcome: Classification, produced: ToolResult | None) -> AuditRecord:
        """One construction site, used by both exits. A second inline copy is
        how the interrupt path would drift from the ordinary one."""
        return AuditRecord(
            id=id_factory(),
            ts=started,
            run_id=ctx.run_id,
            session_id=ctx.session_id,
            persona_sub=ctx.caller.sub,
            team=ctx.caller.team,
            level=ctx.level,
            agent=ctx.agent,
            tool=spec.name,
            parameters=spec.audited_parameters(arguments),
            credential_mode=_mode_used(ctx, surface_mark),
            authorisation_outcome=outcome.outcome or AuthorisationOutcome.ALLOWED,
            rows_returned=produced.rows_returned if produced else None,
            bytes_returned=produced.bytes_returned if produced else None,
            error_class=outcome.error_class,
        )

    try:
        unknown = set(arguments) - set(spec.parameter_names)
        if unknown:
            raise UnknownParameter(
                f"{spec.name} takes {sorted(spec.parameter_names)}; got {sorted(unknown)}"
            )
        result = spec.fn(ctx, **arguments)
    except (KeyboardInterrupt, SystemExit) as exc:
        # Recorded — VER-4 does not exempt an interrupted call — and then
        # allowed to continue on its way. Swallowing these would turn Ctrl-C
        # into a tool result and a suite run that will not stop.
        #
        # The `try` is load-bearing and not defensive habit. `build` resolves
        # the credential mode, which raises on a call that spanned two of them,
        # and raising *here* would raise while already handling the interrupt —
        # so the interrupt would be replaced by a `CredentialPolicyViolation`
        # and no record would be written either. Both documented guarantees
        # lost at once, in the one case where the operator is actively trying
        # to stop the run.
        #
        # An ambiguous call is already the documented exception to VER-4, so
        # losing the record is the behaviour that was chosen. Losing the
        # interrupt was never chosen, and the interrupt wins here.
        try:
            sink.emit(build(classify(exc, surface=_surface_for(spec, ctx, surface_mark)), None))
        except CredentialPolicyViolation:
            pass
        raise
    except BaseException as exc:
        error = exc
        classification = classify(exc, surface=_surface_for(spec, ctx, surface_mark))

    record = build(classification, result)
    sink.emit(record)

    return Invocation(
        record=record,
        result=result,
        error=error,
        reason=classification.reason,
    )


def _surface_for(spec: ToolSpec, ctx: ToolContext, since: int) -> Surface | None:
    """Which surface a failure should be attributed to.

    The surface the call actually reached, when it reached one — `ctx.client()`
    records it before the client is built, so a credential exchange that failed
    still attributes to the right surface. Falling back to a declaration is
    only safe when the tool declares exactly one; a tool that could have
    touched either of two and failed before touching one has not told us which
    control, if any, was involved, and `classify` will refuse to call it a
    denial anyway.
    """
    used = ctx.surfaces_used[since:]
    if used:
        return used[-1]
    return spec.surfaces[0] if len(spec.surfaces) == 1 else None


def _mode_used(ctx: ToolContext, since: int) -> CredentialMode | None:
    """`credential_mode` for the record.

    A call that reached no surface records `null` rather than the mode it would
    have used, which is a true statement about a call that authenticated to
    nothing: an argument rejected before any client was built, or a capability
    check that refused first. `denied_capability` with a null credential mode is
    exactly the shape F6's L3 denial should have.

    The one thing that escapes this function without a record is a
    `CredentialPolicyViolation`, and that is deliberate. It means the call
    spanned two credential modes, so no single record could describe it
    honestly — and writing a record that names one of the two is worse than
    failing the run.
    """
    return ctx.credential_mode_used(since)


__all__ = ["Invocation", "UnknownParameter", "invoke"]
