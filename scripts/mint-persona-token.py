#!/usr/bin/env python3
"""Mint an Identity Platform ID token for a persona, from an operator's laptop.

TD-11's remaining blocker. `src/bta/tools/harness.py` is phase 2's live exit
criterion and needs `BTA_HARNESS_SUBJECT_TOKEN` — an ID token carrying the
persona's `team` claim — before it can exercise L3. Inside Cloud Run the site
mints one on every session (`bta.site.personas`); outside it there was no way
to obtain one, because `personas._ambient_access_token` reads the metadata
server and there is no metadata server on a laptop.

This script is the missing hop and nothing more. It calls the same two
functions the site calls, passing the operator's own bearer token in place of
the ambient one:

    persona choice          resolve() — the same closed list, the same refusal
      └── custom token      mint_custom_token(access_token=<operator's>)
            └── ID token    exchange_for_id_token(api_key=<public browser key>)

**The operator needs `roles/iam.serviceAccountTokenCreator` on the runtime
account.** Earlier versions of this paragraph said no grant was needed because
`roles/owner` already carries `iam.serviceAccounts.signJwt`. **That is false,
and it was verified false on 2026-09-15**: a principal holding `roles/owner` on
the project got `signJwt refused (403)`. Google excludes the service-account
impersonation permissions — `signJwt`, `signBlob`, `getAccessToken`,
`getOpenIdToken`, `implicitDelegation` — from the basic roles, so owner never
carried this and the first run of this script could never have worked. Grant it
deliberately, resource-scoped on the runtime account:

    gcloud iam service-accounts add-iam-policy-binding
        "$BTA_RUNTIME_SERVICE_ACCOUNT"
        --member="user:<operator>"
        --role="roles/iam.serviceAccountTokenCreator"

    (one command; the argument breaks above are for reading only)

IAM is eventually consistent; allow a minute before minting. Grant it to the
operator, never by widening the account's own bindings, and never at project
level — `infra/bootstrap/README.md`:345 records why a project-scoped binding
here would expose the SEC-11 runtime identity the L3 column depends on being
unreachable. Granting a human owner impersonation does not weaken that claim;
granting the arena would. The binding disappears with the account on teardown,
and is not in Terraform; apply it explicitly for your own deployment.

Usage:

    export BTA_RUNTIME_SERVICE_ACCOUNT="$(terraform -chdir=infra/hosting \
        output -raw runtime_service_account_email)"
    export BTA_IDENTITY_PLATFORM_API_KEY="$(terraform -chdir=infra/bootstrap \
        output -raw identity_platform_api_key)"

    export BTA_HARNESS_SUBJECT_TOKEN="$(uv run python \
        scripts/mint-persona-token.py --persona alice)"

Then run the harness (`src/bta/tools/harness.py` documents the fixture ids it
also needs, which come from the operator and never from the manifest).

## What this prints, and what it is worth

One ID token on stdout, nothing else — so it composes into a `$(...)`. Every
diagnostic goes to stderr.

The token is a real credential for one persona for one hour. At L3 it is the
entire input to authorisation: its `team` claim becomes `attribute.team` in the
STS exchange, which is what the row access policies and the bucket's
conditional bindings are written against. It is worth exactly what the persona
is worth, which for `alice` is one team's synthetic data. Do not commit it, do
not paste it into an issue, and do not keep it past the run — not because it is
dangerous, but because a lab that is careless with the one credential it hands
out has no standing to make claims about agents being careless with theirs.

The API key is a different matter: it is the public browser key and SEC-10 is
what makes publishing it safe. It is not redacted here because redacting a
value the service serves to every visitor would teach the wrong lesson.

## Personas it will not mint

`resolve()` refuses `bob` and `svc-platform` (PRD §3), and this script inherits
that refusal rather than working around it. They own the flags; a token for a
flag's owner tests nothing. The harness's negative probes are `alice` reaching
for team-b fixtures and being told no, which is the same thing done correctly.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from bta.site.personas import (
    SELECTABLE,
    PersonaUnavailable,
    exchange_for_id_token,
    mint_custom_token,
    resolve,
)

RUNTIME_SA_ENV = "BTA_RUNTIME_SERVICE_ACCOUNT"
API_KEY_ENV = "BTA_IDENTITY_PLATFORM_API_KEY"


def operator_access_token() -> str:
    """The operator's own bearer token, via `gcloud`.

    Shelling out to `gcloud` rather than using `google.auth.default()` is
    deliberate. Application Default Credentials on a developer machine are
    whatever was last configured, and the failure mode when they are stale or
    point at another project is a 403 from `signJwt` that reads as a missing
    grant. `gcloud auth print-access-token` names the account it used when
    asked, so the error can say who it tried to be.
    """
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise PersonaUnavailable(
            "gcloud is not on PATH. This script runs from an operator's "
            "machine and authenticates as that operator."
        ) from None
    except subprocess.CalledProcessError as exc:
        raise PersonaUnavailable(
            f"gcloud auth print-access-token failed: {exc.stderr.strip()}"
        ) from None
    return result.stdout.strip()


def active_account() -> str:
    """Who `gcloud` thinks we are. Diagnostic only; never gates anything."""
    try:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "account"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--persona",
        default="alice",
        help="Persona subject. Only selectable personas are accepted (PRD §3).",
    )
    parser.add_argument(
        "--service-account",
        default=None,
        help=f"Runtime service account to sign as. Defaults to ${RUNTIME_SA_ENV}.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help=f"Identity Platform browser key. Defaults to ${API_KEY_ENV}.",
    )
    args = parser.parse_args(argv)

    service_account = args.service_account or os.environ.get(RUNTIME_SA_ENV, "").strip()
    api_key = args.api_key or os.environ.get(API_KEY_ENV, "").strip()

    missing = [
        name
        for name, value in ((RUNTIME_SA_ENV, service_account), (API_KEY_ENV, api_key))
        if not value
    ]
    if missing:
        print(
            f"{', '.join(missing)} not set. Both come from `terraform output` — "
            f"see this file's docstring.",
            file=sys.stderr,
        )
        return 2

    try:
        persona = resolve(args.persona)
    except PersonaUnavailable as exc:
        selectable = ", ".join(p.sub for p in SELECTABLE)
        print(f"{exc}\nSelectable personas: {selectable}", file=sys.stderr)
        return 2

    print(
        f"signing as {service_account}, authenticated as {active_account()}",
        file=sys.stderr,
    )

    try:
        custom_token = mint_custom_token(
            persona,
            service_account=service_account,
            access_token=operator_access_token(),
        )
        id_token = exchange_for_id_token(custom_token, api_key=api_key)
    except PersonaUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        f"minted an ID token for {persona.sub} (team={persona.team}), valid one hour",
        file=sys.stderr,
    )
    print(id_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
