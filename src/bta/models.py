"""The closed sets from `schemas.md`, transcribed once.

Only the enumerations phase 2 consumes are here. The record schemas — run
result (§5), memory record (§7), result matrix (§8) — arrive with the phase
that has a consumer for them, for the reason `build-plan.md` §7 gives: a model
with nothing reading it drifts from the document it mirrors and nothing fails.

`schemas.md` is the source. If a value below disagrees with it, this file is
wrong.
"""

from __future__ import annotations

from enum import StrEnum


class Level(StrEnum):
    """`schemas.md` conventions. Levels are alternatives, not layers (FR-14)."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class Agent(StrEnum):
    """`schemas.md` §2. Load-bearing: prompt composition, tool registration and
    case routing all key on these strings."""

    DEPLOY_BOT = "deploy_bot"
    ONBOARDING_ASSISTANT = "onboarding_assistant"
    BOTH = "both"


class Surface(StrEnum):
    """`schemas.md` §2 `service` — the surface a tool reads from or writes to.

    Deliberately the same closed set the flag definitions use rather than a
    parallel vocabulary. A flag names the surface it was planted into and a
    tool names the surface it reaches; when those two are the same word, "which
    tool can reach F5" is answerable by reading two data structures instead of
    by reading tool bodies.

    `WRITE_PATH` and `TOOL_SCHEMA` are surfaces that carry no cloud credential
    — F6 is an outcome and C1 is text in a description. They appear here so the
    set stays closed rather than because anything mints for them.
    """

    BIGQUERY = "bigquery"
    CLOUD_STORAGE = "cloud_storage"
    FIRESTORE = "firestore"
    SECRET_MANAGER = "secret_manager"
    WRITE_PATH = "write_path"
    TOOL_SCHEMA = "tool_schema"


class CredentialMode(StrEnum):
    """`schemas.md` §1.4."""

    SHARED = "shared"
    FEDERATED = "federated"
    SERVICE = "service"


class AuthorisationOutcome(StrEnum):
    """`schemas.md` §6.

    The three denials are distinct because at L3 the question is *which*
    control refused, and an aggregate `denied` answers it with nothing. There
    is deliberately no member for a model declining to act: that is not an
    authorisation outcome and must never be recorded as one.
    """

    ALLOWED = "allowed"
    DENIED_IAM = "denied_iam"
    DENIED_CAPABILITY = "denied_capability"
    DENIED_DOCUMENT = "denied_document"


#: Outcomes that mean a control refused, as opposed to something breaking.
DENIALS: frozenset[AuthorisationOutcome] = frozenset(
    {
        AuthorisationOutcome.DENIED_IAM,
        AuthorisationOutcome.DENIED_CAPABILITY,
        AuthorisationOutcome.DENIED_DOCUMENT,
    }
)


def require_agent(agent: object) -> None:
    """`Agent` is a `StrEnum` too, and it fails more quietly than `Level` does.

    `ToolRegistry.for_agent` selects with `is`, so a plain string matches
    nothing and the caller receives **an empty tool list**. An agent with no
    tools calls nothing, reaches no surface, discloses no flag, and produces a
    transcript that reads like a well-behaved model declining to act. Every
    cell in that agent's row would report `held` (`schemas.md` §8 rule 2), and
    nothing in the run results would say why: no error, no denial, no audit
    record, because no tool was ever called.

    That is the most flattering failure available to this project, which is the
    class of thing `build-plan.md` §1 says to make structurally impossible
    rather than to remember. A string is how an agent identifier arrives from a
    request parameter (FR-4) and from an attack case's `agent` field
    (`schemas.md` §4), so the boundary that turns one into an `Agent` is the
    place to be strict.
    """
    if not isinstance(agent, Agent):
        raise TypeError(
            f"agent must be an Agent, not {type(agent).__name__}. `Agent` is a "
            f"StrEnum, so {agent!r} compares equal to a member while matching "
            f"nothing under `is` — which returns an empty tool list and reports "
            f"`held` for every flag the agent owns."
        )


def require_level(level: object) -> None:
    """`Level` is a `StrEnum`, and that is a trap worth one explicit check.

    `"L3" == Level.L3` is true and `"L3" is Level.L3` is false. Dispatch in
    `bta.tools.clients` selects the L3 client with `is`, while `POLICY` is a
    dict and looks the level up by hash — so a level arriving as a plain string
    resolves to `federated` in the credential policy and to the **server SDK**
    in the Firestore dispatch. The audit record reads `credential_mode:
    federated`, the tool returns correct-looking data, and the document-level
    control is gone. That is spike 0.3 finding 2 reached by a type slip instead
    of by a wiring mistake.

    A string is exactly how a level arrives from a request parameter or a
    config file, which is what phase 3 and phase 4 will be handing to this
    object. Comparing with `==` everywhere instead would make the enum's
    identity meaningless; making the type true at the boundary keeps both.
    """
    if not isinstance(level, Level):
        raise TypeError(
            f"level must be a Level, not {type(level).__name__}. `Level` is a "
            f"StrEnum, so {level!r} compares equal to a member while failing "
            f"every `is Level.L3` dispatch — which silently returns the L1/L2 "
            f"client at L3 (spike 0.3 finding 2)."
        )
