"""Command line entry point.

    uv run python -m bta.generator \
        --seed orbit-7f3a91 \
        --project-id "$(terraform -chdir=infra/estate output -raw project_id)" \
        --dataset-id "$(terraform -chdir=infra/estate output -raw dataset_id)" \
        --bucket "$(terraform -chdir=infra/estate output -raw artefact_bucket_name)" \
        --secret-id "$(terraform -chdir=infra/estate output -raw integration_secret_id)"

Writes `.fixtures/plan.json` and `.fixtures/fixture-manifest.json`, and prints
the two fingerprints. Nothing this command prints contains a flag value: the
fingerprints are hashes, and the counts are counts.

Planning is the default and it touches no cloud. Add `--apply` to push the plan
to GCP, which needs Application Default Credentials for an identity that can
write to the estate. It is a separate flag rather than the default because the
apply is a **replace**: it prunes each surface to the plan, which is how a
reseed stops serving the retired seed's values (`apply.py`). Running it against
a live estate with the wrong seed reseeds that estate.

`.fixtures/` is gitignored and denied to Claude Code. Writing the manifest
anywhere else inside the working tree needs `--force-path`, because a flag
value that reaches git history means reseeding the estate and there is no
undo — `git rm` does not remove it from history.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import build
from .config import DEFAULT_TEAMS, GeneratorConfig

FIXTURES_DIR = ".fixtures"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bta.generator",
        description="Plant the flags and emit the fixture manifest (schemas.md §3).",
    )
    parser.add_argument("--seed", required=True, help="Estate seed. Same seed, same estate.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--bucket", required=True, help="Deploy artefact bucket name.")
    parser.add_argument("--secret-id", required=True, help="Integration key secret id.")
    parser.add_argument("--firestore-database", default="(default)")
    parser.add_argument(
        "--teams",
        default=",".join(DEFAULT_TEAMS),
        help="Comma-separated. Must match infra/estate's `teams` variable.",
    )
    parser.add_argument("--flags-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=Path(FIXTURES_DIR))
    parser.add_argument(
        "--force-path",
        action="store_true",
        help="Permit an output directory outside .fixtures/. Rarely the right answer.",
    )
    parser.add_argument(
        "--fingerprint-only",
        action="store_true",
        help="Print fingerprints and write nothing. Useful for comparing two seeds.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Push the plan to GCP. Replaces each surface, pruning anything a "
            "previous seed left behind. Needs Application Default Credentials."
        ),
    )
    return parser


def _check_output_dir(out_dir: Path, forced: bool) -> None:
    resolved = out_dir.resolve()
    if FIXTURES_DIR in resolved.parts or forced:
        return
    raise SystemExit(
        f"refusing to write flag values to {resolved}.\n"
        f"Use a directory under {FIXTURES_DIR}/, which is gitignored, or pass --force-path "
        f"if you are certain. A value committed once means reseeding the estate."
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    config = GeneratorConfig(
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        artefact_bucket=args.bucket,
        integration_secret_id=args.secret_id,
        firestore_database_id=args.firestore_database,
        teams=tuple(team.strip() for team in args.teams.split(",") if team.strip()),
    )

    result = build(args.seed, config, flags_dir=args.flags_dir)

    print(f"seed                 {args.seed}")
    print(f"manifest fingerprint {result.manifest.fingerprint()}")
    print(f"plan fingerprint     {result.plan.fingerprint()}")
    print(f"flags planted        {len(result.manifest.flags)}")
    for table, rows in sorted(result.plan.tables.items()):
        print(f"  {table:<10} {len(rows):>5} rows")
    print(f"  objects    {len(result.plan.objects):>5}")
    print(f"  documents  {len(result.plan.documents):>5}")
    print(f"  secrets    {len(result.plan.secrets):>5}")

    if args.fingerprint_only:
        if args.apply:
            raise SystemExit(
                "--fingerprint-only and --apply contradict each other. An estate seeded "
                "without its manifest written out is an estate the verifier cannot score."
            )
        return 0

    _check_output_dir(args.out_dir, args.force_path)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = args.out_dir / "fixture-manifest.json"
    plan_path = args.out_dir / "plan.json"
    manifest_path.write_text(result.manifest.to_json(), encoding="utf-8")
    plan_path.write_text(result.plan.to_json(), encoding="utf-8")

    # Both files hold live values. Owner-only, so a stray `cat *` in a shared
    # checkout is at least the owner's own doing.
    manifest_path.chmod(0o600)
    plan_path.chmod(0o600)

    print(f"\nwrote {manifest_path} and {plan_path} (mode 600, never commit either)")

    if not args.apply:
        print("planned only — pass --apply to write it to the project")
        return 0

    # Imported here rather than at the top of the file. The client libraries are
    # the applier's alone, and a planning run — including every determinism test
    # — should not need them importable. It also keeps the failure honest: a
    # missing GCP dependency stops the apply, not the plan.
    from .apply import apply as apply_plan

    print(f"\napplying to {config.project_id}...")
    report = apply_plan(result.plan, config)
    print("\n".join(report.lines()))
    print(
        f"\nestate seeded. Record the manifest fingerprint {result.manifest.fingerprint()} "
        f"against this run."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
