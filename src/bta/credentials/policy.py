"""Which credential a level uses for a surface. Pure, and the whole of it.

This table is the independent variable of the experiment. Everything else
about the lab is held constant across levels (FR-13); this is what changes.
Keeping it as data in one pure function rather than as branches inside tools
means the L1-to-L3 difference can be read in one screen and asserted in one
test.

**A tool names a surface and never a mode.** `ctx.client(Surface.BIGQUERY)`
resolves through here using the level on the context, so there is no argument a
tool could pass — and therefore no argument a model could influence — that
selects a credential. That is the claim `infra/estate/identity.tf` defers to
phase 2 to assert.
"""

from __future__ import annotations

from bta.models import CredentialMode, Level, Surface, require_level


class CredentialPolicyViolation(RuntimeError):
    """Raised when a resolution would break an invariant below.

    Loud, and not catchable by anything in the tool path on purpose. A silent
    fallback to the shared credential at L3 would produce a full column of
    plausible `held` verdicts over a control that was not running.
    """


#: Surfaces that carry no cloud credential at any level.
#:
#: F6 is a write-path outcome and C1 is text in a tool description, so neither
#: has anything to authenticate to. They are in `Surface` so that the set stays
#: the one `schemas.md` §2 closes, and they are named here so that "no mode"
#: is a stated property rather than a missing dictionary key.
UNCREDENTIALED: frozenset[Surface] = frozenset({Surface.WRITE_PATH, Surface.TOOL_SCHEMA})


# L1 and L2 are deliberately identical. FR-14 gives L2 the mitigation stack of
# FR-15 and *the same credentials as L1* — the mitigations are in the agent, not
# in IAM. If these two rows ever diverge, the L2 column stops measuring the
# mitigation stack and starts measuring a credential change nobody registered.
_SHARED_EVERYWHERE: dict[Surface, CredentialMode] = {
    Surface.BIGQUERY: CredentialMode.SHARED,
    Surface.CLOUD_STORAGE: CredentialMode.SHARED,
    Surface.FIRESTORE: CredentialMode.SHARED,
    Surface.SECRET_MANAGER: CredentialMode.SHARED,
}

POLICY: dict[Level, dict[Surface, CredentialMode]] = {
    Level.L1: dict(_SHARED_EVERYWHERE),
    Level.L2: dict(_SHARED_EVERYWHERE),
    Level.L3: {
        # STS exchange, bound to `attribute.team` (spike 0.1). BigQuery's row
        # access policies and the bucket's conditional bindings do the refusing.
        Surface.BIGQUERY: CredentialMode.FEDERATED,
        Surface.CLOUD_STORAGE: CredentialMode.FEDERATED,
        # Also per-caller, but on a different client path from every other tool:
        # the caller's own ID token against the REST API, because Firestore's
        # server SDKs are not subject to Security Rules at all (spike 0.3
        # finding 2). Still `federated` in `schemas.md` §1.4 terms — the
        # credential is the caller's — and the mode name is what the audit
        # record carries, not the transport.
        Surface.FIRESTORE: CredentialMode.FEDERATED,
        # The agent never holds the integration key at L3. A separate service
        # owns it under its own identity and returns outcomes (FR-14). No
        # credential stops an agent disclosing a secret it is already holding,
        # so at L3 it holds none.
        Surface.SECRET_MANAGER: CredentialMode.SERVICE,
    },
}


def mode_for(level: Level, surface: Surface) -> CredentialMode:
    """The one resolution point. Raises rather than defaulting."""
    # `POLICY` is a dict and `Level` is a `StrEnum`, so a plain string resolves
    # by hash and reads correctly — while `_assert_l3_is_not_shared` below
    # compares with `is` and would not fire. Both invariant copies are
    # `is`-gated, so a string level takes them both out together, which is the
    # one failure mode the pair was written not to have. Checked here because
    # this function is the only way into either of them.
    require_level(level)

    if surface in UNCREDENTIALED:
        raise CredentialPolicyViolation(
            f"{surface.value} carries no credential at any level; a caller asking "
            f"for one has confused an outcome or a description with a data surface"
        )

    try:
        mode = POLICY[level][surface]
    except KeyError as exc:  # pragma: no cover - both enums are closed
        raise CredentialPolicyViolation(f"no credential policy for {level}/{surface}") from exc

    _assert_l3_is_not_shared(level, surface, mode)
    return mode


def _assert_l3_is_not_shared(level: Level, surface: Surface, mode: CredentialMode) -> None:
    """The invariant the entire L3 column rests on, checked where it is decided.

    `infra/estate/identity.tf` states the residual plainly: the process serving
    an L3 session *can* mint the shared estate credential, because the grant is
    static and FR-1 puts all three levels behind one Cloud Run service. The
    alternative — a service per level — was considered and declined on
    2026-08-14, and what was taken instead was this: the claim is about code,
    so the code asserts it.

    Checked here as well as in the factory because they fail differently. This
    catches a bad edit to the table above; the factory's copy catches a caller
    that bypassed the table.
    """
    if level is Level.L3 and mode is CredentialMode.SHARED:
        raise CredentialPolicyViolation(
            f"L3/{surface.value} resolved to the shared estate credential. "
            f"That credential has estate-wide read and is bound to no caller, "
            f"so this would report `held` for a control that never ran."
        )
