"""Entry point — `python -m bta.site`.

The order here is the point: **every check that can refuse to start runs before
the socket is bound.** A deliberately vulnerable, internet-reachable service
that starts in a subtly wrong configuration is worse than one that does not
start, because the wrong configuration is invisible in a passing run.

Three things can refuse:

- **The access code** (FR-1). No code, no service.
- **C1's marker** (TD-13, ADR 0003). A deployment rendering tool descriptions
  without it has silently removed the control flag, and SC-5 says a `held` C1
  means the fixture or the verifier is broken — a defect that would not be
  there.
- **SEC-11a.** If the runtime identity can read the estate, every L3 verdict
  this deployment produces is unsound. `assert_worthless` raises and the
  service does not come up.

The fixture manifest is loaded here too, because the verifier needs it to score
captures (FR-6) and `verifier.manifest.load` refuses to return an empty value
set — a verifier with no values finds no captures and reports `held` for every
cell, which is the most flattering failure this project has.
"""

from __future__ import annotations

import os
import sys

import uvicorn

from bta import config as deployment
from bta.audit import InMemorySink
from bta.site import app as site_app
from bta.site import gate, quota
from verifier import manifest


def main() -> int:
    try:
        configuration = deployment.from_env()
        markers = deployment.markers_from_env()
        code = gate.configured_code()
        fixtures = manifest.load()
    except Exception as exc:  # noqa: BLE001 — the operator needs the reason, once
        print(f"refusing to start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    arena = site_app.Arena(
        config=configuration,
        markers=markers,
        fixtures=fixtures,
        # TD-34. Optional: a deployment without one still redacts, and simply
        # records no `transcript_ref`.
        evidence_bucket=os.environ.get("BTA_EVIDENCE_BUCKET", "").strip() or None,
        evidence_writer=site_app.evidence.StorageObjectWriter(configuration.project_id),
        # Cloud Logging in a real deployment. `InMemorySink` here is a known
        # gap, not a choice: VER-4 requires every tool call to be audited and
        # an in-process list does not survive a revision. Recorded as TD-25.
        sink=InMemorySink(),
        limits=quota.Limits.from_env(),
        gate_code=code,
    )

    try:
        site_app.startup_checks(arena)
    except Exception as exc:  # noqa: BLE001
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 3

    uvicorn.run(
        site_app.build_app(arena),
        host="0.0.0.0",  # noqa: S104 — Cloud Run requires it; ingress is the boundary
        port=int(os.environ.get("PORT", "8080")),
        log_level="info",
        access_log=False,  # request paths carry no secrets, but query strings could
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
