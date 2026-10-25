"""`scripts/mint-persona-token.py` — TD-11's remaining blocker, exercised offline.

Loaded by path, same as `test_billing_guard.py`: the hyphen in the filename
already keeps it out of the package tree, and it is an operator tool rather
than shipped source, so it stays off the `bta` import graph on purpose.

The script shells out to `gcloud` and calls two functions that reach a real
Identity Platform tenant (`mint_custom_token`, `exchange_for_id_token`). None
of that is exercised here — there is no network in this suite and there should
not be. What *is* testable without either is everything the script does around
those two calls: argument validation, the missing-environment-variable refusal,
the persona refusal it inherits from `bta.site.personas`, and — the part most
likely to break silently — that a successful run prints exactly one line to
stdout (the token, meant to compose into `$(...)`) and puts every diagnostic on
stderr. A script that leaked "signing as ..." onto stdout would still look like
it worked; `BTA_HARNESS_SUBJECT_TOKEN` would just be wrong, and the harness's
failure would point at IAM rather than at this file.

## A gate this file exists to enforce on itself

A prior version of this file patched `mint_custom_token` and
`exchange_for_id_token` but not `operator_access_token` — which `main()` calls
*unconditionally*, before either patched function, to build the
`access_token` keyword argument. Patching the two identity-chain calls and
leaving that one alone does not make the test hermetic: it means the test
still shells out to the real `gcloud` on whatever machine runs it, and reads
that machine's own account. Three tests failed exactly this way the first time
this file was written — passing on a laptop with `gcloud` configured and
failing everywhere else, which is worse than not testing the path at all.
Every test below that reaches `main()` past the environment-variable check
patches `operator_access_token` too, and `test_every_gcloud_call_is_patched`
below exists so a future edit cannot reintroduce the gap silently: it fails
the moment `subprocess.run` is actually invoked, wherever in the module that
call is made from.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from bta.site.personas import BY_SUB, PersonaUnavailable

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PY = REPO_ROOT / "scripts" / "mint-persona-token.py"


def _load():
    spec = importlib.util.spec_from_file_location("mint_persona_token", SCRIPT_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mint_persona_token = _load()

ENV_ARGS = ["--service-account", "sa@example.invalid", "--api-key", "key-123"]


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """Belt and braces for every test in this file, not just the ones that
    remember to patch `operator_access_token` themselves.

    If anything under test reaches `subprocess.run` for real, that call now
    raises loudly with a message naming this fixture, instead of silently
    shelling out to whatever `gcloud` happens to be on the machine running the
    suite — which is exactly the failure mode the module docstring above
    describes.
    """

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "a test in test_mint_persona_token.py reached subprocess.run for "
            "real. Patch mint_persona_token.operator_access_token (or "
            "active_account) instead of relying on mint_custom_token / "
            "exchange_for_id_token being patched — main() calls "
            "operator_access_token() unconditionally to build the "
            "access_token argument, before either identity-chain function "
            "runs."
        )

    monkeypatch.setattr(mint_persona_token.subprocess, "run", _forbidden)


def test_missing_env_and_missing_flags_both_refuse(monkeypatch, capsys) -> None:
    """Neither `--service-account`/`--api-key` nor their env var equivalents are
    set. The script must refuse before touching `gcloud` or the network, and
    exit 2 — a distinct code from the identity-chain failure paths below, so a
    caller scripting around this can tell a misconfiguration from a refused
    persona."""
    monkeypatch.delenv(mint_persona_token.RUNTIME_SA_ENV, raising=False)
    monkeypatch.delenv(mint_persona_token.API_KEY_ENV, raising=False)

    rc = mint_persona_token.main(["--persona", "alice"])

    assert rc == 2
    out, err = capsys.readouterr()
    assert out == ""  # nothing on stdout when the run never produced a token
    assert mint_persona_token.RUNTIME_SA_ENV in err
    assert mint_persona_token.API_KEY_ENV in err


def test_env_vars_substitute_for_missing_flags(monkeypatch) -> None:
    """The documented usage exports both as environment variables and passes
    neither flag. If the fallback ever stopped reading the environment, every
    documented invocation in the script's own docstring would break."""
    monkeypatch.setenv(mint_persona_token.RUNTIME_SA_ENV, "sa@example.invalid")
    monkeypatch.setenv(mint_persona_token.API_KEY_ENV, "key-123")
    monkeypatch.setattr(
        mint_persona_token, "operator_access_token", lambda: "operator-bearer-token"
    )
    monkeypatch.setattr(mint_persona_token, "mint_custom_token", lambda persona, **_: "custom-jwt")
    monkeypatch.setattr(
        mint_persona_token, "exchange_for_id_token", lambda token, **_: "id-token-abc"
    )
    monkeypatch.setattr(mint_persona_token, "active_account", lambda: "operator@example.invalid")

    rc = mint_persona_token.main([])

    assert rc == 0


@pytest.mark.parametrize("persona", ["bob", "svc-platform"])
def test_a_fixture_owner_persona_is_refused(persona: str, capsys) -> None:
    """PRD §3 — `resolve()`'s refusal, inherited rather than re-implemented. A
    token for a flag's owner tests nothing (bob and svc-platform own every
    team-owned flag), and the script must say so and exit 2 without ever
    reaching `gcloud` or the network.

    No patching of `operator_access_token` needed here: `resolve()` is called
    before it, so a correctly-written script never reaches the subprocess call
    on this path at all — which the autouse fixture above would catch if it
    ever did.
    """
    rc = mint_persona_token.main(["--persona", persona, *ENV_ARGS])

    assert rc == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "Selectable personas" in err
    assert "alice" in err


def test_an_unknown_persona_is_refused_the_same_way(capsys) -> None:
    rc = mint_persona_token.main(["--persona", "mallory", *ENV_ARGS])

    assert rc == 2
    out, _err = capsys.readouterr()
    assert out == ""


def test_a_successful_run_prints_only_the_token_to_stdout(monkeypatch, capsys) -> None:
    """The composition contract this whole script exists for:
    `BTA_HARNESS_SUBJECT_TOKEN="$(uv run python scripts/mint-persona-token.py ...)"`.
    Anything beyond the bare token on stdout — a diagnostic, a trailing
    newline's worth of extra content, the operator's account name — becomes
    part of the token string a shell captures, and the harness's very next
    request fails with an auth error that has nothing to do with IAM.
    """
    captured_kwargs = {}

    monkeypatch.setattr(
        mint_persona_token, "operator_access_token", lambda: "operator-bearer-token"
    )

    def _mint(persona, **kwargs):
        captured_kwargs["mint"] = (persona, kwargs)
        return "signed-custom-token"

    def _exchange(token, **kwargs):
        captured_kwargs["exchange"] = (token, kwargs)
        return "the-id-token"

    monkeypatch.setattr(mint_persona_token, "mint_custom_token", _mint)
    monkeypatch.setattr(mint_persona_token, "exchange_for_id_token", _exchange)
    monkeypatch.setattr(mint_persona_token, "active_account", lambda: "operator@example.invalid")

    rc = mint_persona_token.main(["--persona", "alice", *ENV_ARGS])

    assert rc == 0
    out, err = capsys.readouterr()
    assert out == "the-id-token\n"
    assert "alice" in err
    assert "operator@example.invalid" in err

    # The persona, the operator's own token and the service account are what
    # get threaded through, not the raw CLI strings — `resolve()` and
    # `operator_access_token()` sit between the flags and the identity chain,
    # and a test that only checked the return code would not catch either
    # being bypassed.
    persona, mint_kwargs = captured_kwargs["mint"]
    assert persona is BY_SUB["alice"]
    assert mint_kwargs["service_account"] == "sa@example.invalid"
    assert mint_kwargs["access_token"] == "operator-bearer-token"
    token, exchange_kwargs = captured_kwargs["exchange"]
    assert token == "signed-custom-token"
    assert exchange_kwargs["api_key"] == "key-123"


def test_identity_chain_failure_is_reported_and_exits_nonzero(monkeypatch, capsys) -> None:
    """`signJwt`/Identity Toolkit refusing is not the same failure as a missing
    flag (exit 2) — it is the identity chain itself failing, and the script
    distinguishes the two exit codes."""
    monkeypatch.setattr(
        mint_persona_token, "operator_access_token", lambda: "operator-bearer-token"
    )
    monkeypatch.setattr(
        mint_persona_token,
        "mint_custom_token",
        lambda persona, **_: (_ for _ in ()).throw(PersonaUnavailable("signJwt refused (403)")),
    )
    monkeypatch.setattr(mint_persona_token, "active_account", lambda: "operator@example.invalid")

    rc = mint_persona_token.main(["--persona", "alice", *ENV_ARGS])

    assert rc == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "signJwt refused" in err


def test_operator_access_token_reports_who_gcloud_thinks_it_is(monkeypatch) -> None:
    """The whole point of shelling out rather than using ADC (the module
    docstring is explicit about this): a stale or wrong-project credential
    must fail with a message naming the account, not a bare 403.

    This is the one test in the file allowed to *reach* `subprocess.run` — it
    exercises `operator_access_token`'s own error handling, so it replaces the
    autouse fixture's stand-in with its own failing stub rather than removing
    the guard.
    """

    def _fail(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["gcloud"], stderr="not logged in\n")

    monkeypatch.setattr(mint_persona_token.subprocess, "run", _fail)

    with pytest.raises(PersonaUnavailable, match="not logged in"):
        mint_persona_token.operator_access_token()


def test_operator_access_token_without_gcloud_on_path(monkeypatch) -> None:
    def _missing(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(mint_persona_token.subprocess, "run", _missing)

    with pytest.raises(PersonaUnavailable, match="gcloud is not on PATH"):
        mint_persona_token.operator_access_token()


def test_active_account_falls_back_to_unknown_rather_than_raising(monkeypatch) -> None:
    """Diagnostic only (`active_account`'s own docstring: "never gates
    anything"). A `gcloud` failure here must not take down a run that would
    otherwise succeed."""

    def _fail(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["gcloud"])

    monkeypatch.setattr(mint_persona_token.subprocess, "run", _fail)

    assert mint_persona_token.active_account() == "unknown"


def test_a_service_account_and_api_key_flag_override_the_environment(monkeypatch) -> None:
    """`--service-account`/`--api-key` win over the environment variables, per
    `argparse.default` reading the environment only when the flag is absent."""
    monkeypatch.setenv(mint_persona_token.RUNTIME_SA_ENV, "env-sa@example.invalid")
    monkeypatch.setenv(mint_persona_token.API_KEY_ENV, "env-key")
    monkeypatch.setattr(
        mint_persona_token, "operator_access_token", lambda: "operator-bearer-token"
    )
    captured_kwargs = {}
    monkeypatch.setattr(
        mint_persona_token,
        "mint_custom_token",
        lambda persona, **kwargs: captured_kwargs.setdefault("mint", kwargs) and "jwt",
    )
    monkeypatch.setattr(mint_persona_token, "exchange_for_id_token", lambda token, **_: "id-token")
    monkeypatch.setattr(mint_persona_token, "active_account", lambda: "operator@example.invalid")

    rc = mint_persona_token.main(
        [
            "--persona",
            "alice",
            "--service-account",
            "flag-sa@example.invalid",
            "--api-key",
            "flag-key",
        ]
    )

    assert rc == 0
    assert captured_kwargs["mint"]["service_account"] == "flag-sa@example.invalid"
