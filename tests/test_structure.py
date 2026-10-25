"""build-plan.md §5 — structural contracts that are not due yet.

Four of §5's seven rows are import contracts over packages that arrive in
phases 2–4. A contract written after its package exists is shaped to fit the
imports it already has, which makes it a description rather than a constraint.

This does not check those contracts. It checks that each tree cannot appear on
disk without one, so the decision happens in the commit that creates it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Paths rather than importable names: the question is what is on disk, before
# anything imports it.
DEFERRED_CONTRACTS = [
    pytest.param(
        "bta.tools",
        "src/bta/tools",
        ("subject",),
        "bta.tools imports nothing from bta.agent",
        "POR-3, FR-11",
        id="tools",
    ),
    pytest.param(
        "bta.agent",
        "src/bta/agent",
        ("object",),
        "bta.tools imports nothing from bta.agent",
        "POR-3, FR-11",
        id="agent",
    ),
    pytest.param(
        "bta.identity",
        "src/bta/identity",
        ("object",),
        "request auth is read only through bta.identity",
        "POR-1",
        id="identity",
    ),
    pytest.param(
        "bta.credentials",
        "src/bta/credentials",
        ("subject",),
        "credential minting does not depend on the tool boundary",
        "FR-14, schemas.md §1.4",
        id="credentials",
        # Added in phase 2, alongside the package. It was absent from this list
        # while `src/bta/credentials/` did not exist, which meant the tree the
        # entire L3 column rests on was the one tree here that could have
        # arrived unconstrained. §5 of the build plan does not name a contract
        # for it, so this one is the phase's own: authority flows one way, and
        # `policy.py` stays readable on its own because nothing in the boundary
        # can be pulled back into it.
    ),
    pytest.param(
        "bta.screener",
        "src/bta/screener",
        ("subject", "object"),
        "no import path between bta.screener and verifier, in either direction",
        "VER-3",
        id="screener",
    ),
    pytest.param(
        "bta.site",
        "src/bta/site",
        ("subject",),
        "the site reaches flag values only through the verifier",
        "schemas.md §3, FR-6, FR-16",
        id="site",
        # Phase 4's own, not one of build-plan.md §5's seven. FR-6 requires
        # automatic capture, so the site process necessarily contains the
        # verifier and therefore the flag values — the constraint that remains
        # available is that it reach them through the verifier and never
        # independently.
    ),
    pytest.param(
        "verifier",
        "verifier",
        ("subject", "object"),
        "no import path between bta.screener and verifier, in either direction",
        "VER-3",
        id="verifier",
    ),
    pytest.param(
        "suite",
        "suite",
        ("subject",),
        "the suite is told about captures and never holds a flag value",
        "schemas.md §3, VER-6, SC-6",
        id="suite",
        # Phase 5's own. The suite is the third tree that could plausibly reach
        # a live flag value and the only one with no reason to: capture happens
        # in the site process, where the verifier already is, and the suite
        # records the id and channel it is handed. The convenience this forbids
        # — re-scanning a transcript in the runner — would put values in an
        # artefact SC-6 commits to the repository.
    ),
]


def _contracts() -> list[dict]:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config.get("tool", {}).get("importlinter", {}).get("contracts", [])


#: Where in a contract a module may be named. The distinction is the point:
#: appearing as a `source_module` means the contract constrains what this tree
#: may import, while appearing as a `forbidden_module` means it constrains what
#: may import *this* tree. VER-3 needs both, and an earlier version of this
#: file could not tell them apart.
SUBJECT_KEYS = ("source_modules", "modules", "layers", "containers")
OBJECT_KEYS = ("forbidden_modules",)


def _named_under(contract: dict, keys: tuple[str, ...]) -> set[str]:
    named: set[str] = set()
    for key in keys:
        value = contract.get(key, [])
        named.update(value if isinstance(value, list) else [value])
    return named


def _modules_named(contract: dict) -> set[str]:
    return _named_under(contract, SUBJECT_KEYS + OBJECT_KEYS)


def _covers(module: str, names: set[str]) -> bool:
    """A contract naming `bta.identity.tokens` covers `bta.identity`: the
    submodule is the thing actually held, and the package is what §5 names."""
    return any(
        module == named or named.startswith(f"{module}.") or module.startswith(f"{named}.")
        for named in names
    )


def test_the_generator_contract_is_configured() -> None:
    """`lint-imports` exits 0 on an empty contract list, so a config that
    stopped being read looks identical to a clean run."""
    names = [contract.get("name", "") for contract in _contracts()]
    assert any("cloud" in name for name in names), (
        "The generator's purity contract has gone from pyproject.toml. "
        "`lint-imports` passes vacuously when no contract is configured, so "
        "removing it does not fail anything else."
    )


@pytest.mark.parametrize(("module", "path", "positions", "rule", "requirement"), DEFERRED_CONTRACTS)
def test_a_new_tree_arrives_with_its_contract(
    module: str, path: str, positions: tuple[str, ...], rule: str, requirement: str
) -> None:
    """A tree may not land without the contract §5 owes it — *in the direction*
    §5 owes it.

    Asking only whether a module is mentioned anywhere cannot tell "covered"
    from "mentioned". `verifier` was named in the screener contract's
    `forbidden_modules` before the tree existed, so this test would have gone
    green the moment the directory appeared, with the reverse direction of
    VER-3 still unwritten and — because `verifier` was not a root package —
    still inexpressible.
    """
    if not (REPO_ROOT / path).is_dir():
        pytest.skip(f"{path} does not exist yet — its contract is not due")

    contracts = _contracts()
    held = {
        "subject": any(_covers(module, _named_under(c, SUBJECT_KEYS)) for c in contracts),
        "object": any(_covers(module, _named_under(c, OBJECT_KEYS)) for c in contracts),
    }
    missing = [position for position in positions if not held[position]]

    assert not missing, (
        f"\n{path}/ exists, and pyproject.toml's importlinter contracts do not "
        f"constrain `{module}` as {' and '.join(missing)}.\n\n"
        f"build-plan.md §5 owes it:\n"
        f"    {rule}\n"
        f"    ({requirement})\n\n"
        f"`subject` means a contract naming it in source_modules — what this "
        f"tree may import.\n`object` means a contract naming it in "
        f"forbidden_modules — what may import this tree.\nA rule in one "
        f"direction does not imply the other, and VER-3 needs both.\n"
    )


def test_the_verifier_is_a_root_package() -> None:
    """VER-3's reverse direction is not expressible otherwise.

    With `verifier` absent from `root_packages`, import-linter treats it as an
    external package: `bta.screener -> verifier` is still caught, but no
    contract can constrain what `verifier` itself imports, so a contract naming
    it as a source module would silently analyse nothing.
    """
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    roots = config["tool"]["importlinter"]["root_packages"]
    assert "verifier" in roots, (
        "`verifier` is not in root_packages, so any contract naming it as a "
        "source module analyses an empty graph and passes vacuously."
    )


# ---------------------------------------------------------------------------
# Every required Terraform variable in a CI-applied module is wired in ci.yml
# ---------------------------------------------------------------------------
#
# NFR-5 — `terraform apply` from clean to fully seeded with no manual
# intervention. A required variable with no `TF_VAR_` behind it breaks that,
# and breaks it late: Terraform evaluates variables at apply time, so the job
# fails *after* the modules before it have already applied and seeded.
#
# Not hypothetical. `evidence_bucket_name` and `evidence_bucket_location` were
# added to `infra/hosting/variables.tf` on 2026-08-29 without being wired here,
# and nothing noticed until 2026-09-15 — the first dispatch ever to reach the
# hosting job — which failed on "No value for required variable" with the
# estate already applied, already seeded, and an image already pushed.
# `scripts/lab.sh` had been updated, so the local path worked and only CI did
# not, which is the shape that survives longest.
#
# infra/bootstrap is excluded deliberately: no workflow applies it (TD-6) and
# its inputs are supplied at apply time on an operator's laptop.
CI_APPLIED_MODULES = ("billing-guard", "estate", "hosting")


def _required_variables(module: str) -> set[str]:
    """Variables in a module with no default, so they must be supplied."""
    body = (REPO_ROOT / "infra" / module / "variables.tf").read_text(encoding="utf-8")
    blocks = re.findall(r'variable\s+"([A-Za-z0-9_]+)"\s*\{(.*?)\n\}', body, re.S)
    return {name for name, block in blocks if not re.search(r"^\s*default\s*=", block, re.M)}


def _wired_in_ci() -> tuple[set[str], set[str], set[str]]:
    """The three shapes a `TF_VAR_` assignment takes in `ci.yml`.

    **Exact** — `TF_VAR_project_id: ...` in a job's `env:`.

    **Prefixed** — `TF_VAR_marker_${lower}=`, one loop covering a family. The
    empty prefix that `TF_VAR_${name}=` produces is discarded: `startswith("")`
    is true of everything, so keeping it would make this check pass vacuously.

    **Paired** — `"estate_dataset_id:dataset_id"`, from the loop that reads
    estate outputs and names its targets in a pair list rather than a literal.
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    exact = set(re.findall(r"TF_VAR_([a-z0-9_]+)(?!\$)", ci))
    prefixes = {p for p in re.findall(r"TF_VAR_([a-z0-9_]*)\$\{", ci) if p}
    paired = set(re.findall(r'"([a-z0-9_]+):[a-z0-9_]+"', ci))
    return exact, prefixes, paired


def _wired(name: str, exact: set[str], prefixes: set[str], paired: set[str]) -> bool:
    return name in exact or name in paired or any(name.startswith(prefix) for prefix in prefixes)


@pytest.mark.parametrize("module", CI_APPLIED_MODULES)
def test_every_required_terraform_variable_is_supplied_by_ci(module: str) -> None:
    required = _required_variables(module)
    assert required, f"infra/{module}/variables.tf parsed to no required variables"

    exact, prefixes, paired = _wired_in_ci()
    missing = sorted(name for name in required if not _wired(name, exact, prefixes, paired))

    assert not missing, (
        f"infra/{module} requires {missing} and .github/workflows/ci.yml supplies "
        f"no TF_VAR_ for them. The apply fails at variable evaluation, after the "
        f"modules before it have already applied — wire them into the job's env, "
        f"or give the variable a default if it genuinely has a sensible one."
    )


def test_the_ci_variable_check_can_actually_fail() -> None:
    """The negative control.

    A parse that matched nothing, or a `prefixes` set that kept the empty
    string, would report every module clean forever — the same silent-pass
    failure the rest of this file exists to prevent, one level up.
    """
    exact, prefixes, paired = _wired_in_ci()

    assert "" not in prefixes, "an empty prefix matches every name and voids the check"
    assert not _wired("a_variable_nobody_declared", exact, prefixes, paired)
    assert _wired("project_id", exact, prefixes, paired), "exact matching is broken"
