"""Prompt composition — **pre-registered artefact** (`build-plan.md` §4).

Named `composition` rather than `compose` because the package re-exports a
function of that name: `bta.levels.compose` would then resolve to the function,
and `import bta.levels.compose as module` would silently hand a caller the
function instead of failing. That shadowing cost a test an afternoon once
already.

Registered 2026-08-24. The composed prompts decide FR-13's only-variable-is-
authorisation claim, so amending a fragment requires a PR with a dated
justification (`process.md`).

## The shape

    composed(agent, level) == base.<agent>.md + SEPARATOR + level.<level>.<agent>.md

A shared base per agent, plus one per-level fragment. `prompts/composed/`
carries the result of that composition for all six pairs, committed so a
reviewer diffs prompts rather than reconstructing them, and
`tests/test_levels.py` asserts the committed copies match what `compose`
produces.

## Composition returns a template, never a rendered prompt

`level.L1.onboarding_assistant.md` contains `{integration_key}` — FR-14 puts
the integration API key in the L1 system prompt with a withholding instruction,
and that key is **F4's value**. So:

- The committed artefacts under `prompts/composed/` hold the placeholder. A
  rendered prompt in the repository would be a flag value in git.
- The FR-13 byte-identity test runs on **unrendered** fragments. pytest prints
  both operands on failure, and a CI log is a transcript.
- `render` is a separate call, made at session construction from values that
  came from the environment, and its output is never written to disk.

`{caller_team}` is the same mechanism for a non-secret value: FR-14 gives L1
and L2 Deploy Bot a prompt that asserts the caller's team, and the team comes
from the verified identity claim (POR-1), never from anything the caller typed.
"""

from __future__ import annotations

import re
from pathlib import Path

from bta.models import Agent, Level, require_agent, require_level

PROMPTS_DIR = Path(__file__).parent / "prompts"
COMPOSED_DIR = PROMPTS_DIR / "composed"

#: One blank line. Fixed here rather than inline so that "the only difference
#: between two composed prompts is the level fragment" is a property of one
#: constant instead of two string literals that could drift apart.
SEPARATOR = "\n\n"

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


class PromptError(RuntimeError):
    """A fragment is missing, or a placeholder was left unfilled."""


def base_text(agent: Agent) -> str:
    require_agent(agent)
    return _read(PROMPTS_DIR / f"base.{agent.value}.md")


def level_text(level: Level, agent: Agent) -> str:
    require_level(level)
    require_agent(agent)
    return _read(PROMPTS_DIR / f"level.{level.value}.{agent.value}.md")


def compose(agent: Agent, level: Level) -> str:
    """The system prompt as a template, with placeholders intact."""
    return base_text(agent).rstrip("\n") + SEPARATOR + level_text(level, agent).rstrip("\n")


def composed_path(agent: Agent, level: Level) -> Path:
    require_agent(agent)
    require_level(level)
    return COMPOSED_DIR / f"{agent.value}-{level.value}.txt"


def render(template: str, *, caller_team: str, integration_key: str | None = None) -> str:
    """Fill the placeholders. Called once per session, never persisted.

    A template still holding a placeholder after rendering raises rather than
    shipping `{integration_key}` to a model: at L1 that would silently remove
    F4 from the prompt, and the Onboarding L1 column would report a control
    that was never tested.
    """
    filled = template.replace("{caller_team}", caller_team)
    if "{integration_key}" in filled:
        if integration_key is None:
            raise PromptError(
                "the composed prompt expects an integration key and none was "
                "supplied. At L1 and L2 the key is in the system prompt by "
                "design (FR-14) and it is F4; rendering without it would remove "
                "the flag and report a control that never ran."
            )
        filled = filled.replace("{integration_key}", integration_key)

    remaining = sorted(set(_PLACEHOLDER.findall(filled)))
    if remaining:
        raise PromptError(f"unfilled placeholders in composed prompt: {remaining}")
    return filled


def _read(path: Path) -> str:
    if not path.exists():
        raise PromptError(f"prompt fragment missing: {path.name}")
    return path.read_text(encoding="utf-8")


__all__ = [
    "COMPOSED_DIR",
    "PROMPTS_DIR",
    "SEPARATOR",
    "PromptError",
    "base_text",
    "compose",
    "composed_path",
    "level_text",
    "render",
]
