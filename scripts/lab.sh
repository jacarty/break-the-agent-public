#!/usr/bin/env bash
#
# The lab, from a laptop, in one command.
#
# ── Why this exists ──────────────────────────────────────────────────────────
#
# Deploy and teardown used to resolve their inputs by different paths. `ci.yml`
# read estate outputs and real secrets; `teardown.yml` passed literal
# placeholders, because `terraform destroy` evaluates every variable and
# validation even though it needs none of them — a destroy works from state.
#
# That created a second, invisible contract: every variable and every
# validation must have a placeholder that satisfies it. Nothing enforced it,
# and it could only fail during a teardown — the least rehearsed path, at the
# worst moment. Six hosting variables were placeheld; two were shaped to pass
# validation, two were not, and one failed a provider-side format check that a
# placeholder cannot satisfy at all. Teardown broke on all three at once.
#
# **So there are no placeholders here.** `resolve` is one function, and `up`,
# `down` and `plan` all call it. Teardown cannot drift from deploy because they
# read the same values from the same places. A new variable is resolved once,
# for every command, or it is missing from all of them and `plan` says so.
#
# ── Rehearsal is the point ───────────────────────────────────────────────────
#
#     ./scripts/lab.sh plan
#
# resolves every input and plans every module, changing nothing. It is the
# command that would have caught the teardown failure before it happened, and
# it is meant to be run before `up` and before `down`.
#
# ── Commands ─────────────────────────────────────────────────────────────────
#
#   env      Resolve inputs and report which are set. Values are never printed.
#   plan     Resolve, then `terraform plan` every module. Changes nothing.
#   up       Apply estate, seed it, build and push the image, apply hosting.
#   down     Destroy hosting, then estate, then billing-guard, then verify.
#   verify   Run the NFR-6 check against the committed baseline.
#   baseline Recapture that baseline. Only valid after `down`, before `up`.
#
# ── Configuration ────────────────────────────────────────────────────────────
#
# One gitignored file, `.env.lab`, at the repository root. `./scripts/lab.sh
# env` names anything missing. Nothing in it is committed and nothing here is
# hardcoded (POR-6).
#
# ── What this never prints ───────────────────────────────────────────────────
#
# Flag values, the fixture manifest, the access code and the bypass token all
# pass through this script. None is ever echoed: `set -x` is never used, the
# manifest is read into a variable and not a log, and `env` reports names and
# set/unset only. `schemas.md` §3 keeps the set of things that see flag values
# small, and this script is deliberately in it rather than working around it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="${LAB_ENV_FILE:-$REPO_ROOT/.env.lab}"
FIXTURES_DIR="$REPO_ROOT/.fixtures"
MANIFEST="$FIXTURES_DIR/fixture-manifest.json"

die() { printf '\nlab: %s\n\n' "$*" >&2; exit 1; }
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Deployment inputs an operator supplies. Everything else is derived.
REQUIRED_ENV=(
    BTA_PROJECT_ID
    BTA_REGION
    BTA_STATE_BUCKET
    BTA_BIGQUERY_LOCATION
    BTA_ARTEFACT_BUCKET_NAME
    BTA_ARTEFACT_BUCKET_LOCATION
    BTA_FIRESTORE_LOCATION
    BTA_ESTATE_SEED
    BTA_ACCESS_CODE
    BTA_SUITE_BYPASS_TOKEN
    BTA_MODEL_ANTHROPIC
    BTA_MODEL_GOOGLE
    BTA_MODEL_XAI
    BTA_VERTEX_LOCATION
    BTA_ANTHROPIC_VERTEX_LOCATION
    BTA_XAI_VERTEX_LOCATION
    BTA_SITE_PROVIDER
)

#: Secret-shaped. `env` reports these as set/unset and never by value.
SECRET_ENV=(BTA_ACCESS_CODE BTA_SUITE_BYPASS_TOKEN BTA_ESTATE_SEED)

load_env() {
    [ -f "$ENV_FILE" ] || die "no $ENV_FILE. See scripts/README.md for the template."

    # **Check the shape before sourcing it.** Sourcing an env file executes it,
    # so a stray line of prose — a template pasted with its surrounding
    # sentence, which is exactly how this first went wrong — runs as a command
    # and reports `command not found` on somebody's documentation. That is a
    # confusing failure a long way from its cause, and this file is edited by
    # hand every time.
    local line_no=0 bad=()
    while IFS= read -r line || [ -n "$line" ]; do
        line_no=$((line_no + 1))
        case "$line" in
            ''|'#'*) continue ;;
            [A-Za-z_]*=*) continue ;;
            *) bad+=("$line_no: $line") ;;
        esac
    done < "$ENV_FILE"
    if [ ${#bad[@]} -gt 0 ]; then
        printf '\nlab: %s is not a shell env file. These lines are neither\n' "$ENV_FILE" >&2
        printf '     KEY=value, a # comment, nor blank:\n\n' >&2
        printf '       %s\n' "${bad[@]}" >&2
        printf '\n     Every line must be KEY=value. See scripts/README.md.\n\n' >&2
        exit 1
    fi

    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a

    local missing=()
    for name in "${REQUIRED_ENV[@]}"; do
        [ -n "${!name:-}" ] || missing+=("$name")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        die "missing from $ENV_FILE: ${missing[*]}"
    fi
}

# ---------------------------------------------------------------------------
# Terraform helpers
# ---------------------------------------------------------------------------

tf() {
    local module="$1"; shift
    terraform -chdir="infra/$module" "$@"
}

#: Bootstrap's backend is gitignored so a fresh clone can migrate state into
#: the bucket it creates. Every later run needs the copy first, and its prefix
#: is supplied at init time because `backend.tf.migrate` commits none — unlike
#: infra/estate, which commits `prefix = "estate"`. Getting that wrong inits
#: against the bucket root, finds empty state, and reports outputs as missing
#: rather than erroring.
init_bootstrap() {
    [ -f infra/bootstrap/backend.tf ] || cp infra/bootstrap/backend.tf.migrate infra/bootstrap/backend.tf
    tf bootstrap init -input=false -reconfigure \
        -backend-config="bucket=$BTA_STATE_BUCKET" \
        -backend-config="prefix=bootstrap" >/dev/null
}

init_module() {
    tf "$1" init -input=false -reconfigure \
        -backend-config="bucket=$BTA_STATE_BUCKET" >/dev/null
}

#: A `terraform output` that does not silently return empty. An unset output
#: reaching an apply as "" is how a module gets configured with nothing and
#: still applies.
#:
#: **Assigns by name rather than printing**, and that is the whole point of the
#: signature. The printing version of this function could not do what its own
#: comment claimed: every caller wrote `export VAR="$(output_or_die ...)"`, and
#: a command substitution runs in a subshell, so `die`'s `exit` killed the
#: subshell and left the parent running with an empty value. `set -e` does not
#: catch it either — the exit status of `export VAR=$(cmd)` is `export`'s, not
#: the substitution's, so the failure is masked at every one of these call
#: sites. Verified against a destroyed estate on 2026-08-29: `lab.sh env`
#: printed the refusal and then listed seven empty inputs.
#:
#: The consequence was not cosmetic. `up` passes four of these straight to the
#: generator, so a missing estate output meant seeding with an empty dataset id
#: rather than stopping.
#: `env` sets this so it can report every missing output instead of stopping at
#: the first — its job is to name anything missing, and an operator checking
#: their configuration before the first `up` has no estate outputs at all. It is
#: a reporting mode on the one resolver, not a second resolver: what gets
#: resolved, and from where, is identical.
LAB_SOFT_RESOLVE=0
MISSING_OUTPUTS=""

require_output() {
    local variable="$1" module="$2" name="$3" value
    value="$(tf "$module" output -raw "$name" 2>/dev/null || true)"
    if [ -z "$value" ]; then
        [ "$LAB_SOFT_RESOLVE" = "1" ] \
            || die "infra/$module has no '$name' output. Has it been applied?"
        MISSING_OUTPUTS="$MISSING_OUTPUTS $module/$name"
        export "$variable="
        return 0
    fi
    export "$variable=$value"
}

# ---------------------------------------------------------------------------
# The one resolver
# ---------------------------------------------------------------------------
#
# Every command calls this. There is no second path, which is the whole point:
# the class of bug this replaces was deploy and teardown disagreeing about what
# an input is.

resolve_base() {
    load_env
    export TF_VAR_project_id="$BTA_PROJECT_ID"
    export TF_VAR_region="$BTA_REGION"
    PROJECT_NUMBER="$(gcloud projects describe "$BTA_PROJECT_ID" --format='value(projectNumber)')"
    [ -n "$PROJECT_NUMBER" ] || die "cannot read the project number for $BTA_PROJECT_ID"
    export TF_VAR_project_number="$PROJECT_NUMBER"
}

resolve_estate_vars() {
    export TF_VAR_bigquery_location="$BTA_BIGQUERY_LOCATION"
    export TF_VAR_artefact_bucket_name="$BTA_ARTEFACT_BUCKET_NAME"
    export TF_VAR_artefact_bucket_location="$BTA_ARTEFACT_BUCKET_LOCATION"
    export TF_VAR_firestore_location="$BTA_FIRESTORE_LOCATION"
    init_bootstrap
    require_output TF_VAR_arena_pool_id bootstrap arena_pool_id
}

#: **The function the placeholder strategy used to replace.**
#:
#: `down` calls this exactly as `up` does. Three of hosting's variables carry
#: validations and three more feed provider-side format checks; none of them is
#: satisfiable by a literal, and a destroy evaluates all of them.
resolve_hosting_vars() {
    resolve_estate_vars
    init_module estate

    require_output TF_VAR_estate_dataset_id estate dataset_id
    require_output TF_VAR_estate_ops_dataset_id estate ops_dataset_id
    require_output TF_VAR_artefact_bucket_name estate artefact_bucket_name
    require_output TF_VAR_firestore_database_id estate firestore_database_id
    require_output TF_VAR_integration_secret_id estate integration_secret_id
    require_output TF_VAR_runtime_service_account_email estate runtime_service_account_email
    require_output TF_VAR_shared_service_account_email estate shared_service_account_email
    require_output TF_VAR_provisioning_service_account_email estate provisioning_service_account_email

    require_output TF_VAR_arena_provider_id bootstrap arena_provider_id
    require_output TF_VAR_identity_platform_api_key bootstrap identity_platform_api_key
    # The bare repository id, not the full push target. Passing the whole thing
    # builds an Artifact Registry resource id containing a hostname, and the
    # apply fails on a resource that reads as correctly configured.
    local repo; require_output repo bootstrap registry_repository
    export TF_VAR_registry_id="${repo##*/}"

    # TD-34. Defaults derived from the project so a deployer needs no extra
    # entry in .env.lab, but overridable like every other input (POR-6).
    export TF_VAR_evidence_bucket_name="${BTA_EVIDENCE_BUCKET_NAME:-${BTA_PROJECT_ID}-evidence}"
    export TF_VAR_evidence_bucket_location="${BTA_EVIDENCE_BUCKET_LOCATION:-$BTA_ARTEFACT_BUCKET_LOCATION}"

    export TF_VAR_access_code="$BTA_ACCESS_CODE"
    export TF_VAR_suite_bypass_token="$BTA_SUITE_BYPASS_TOKEN"
    export TF_VAR_site_provider="$BTA_SITE_PROVIDER"
    export TF_VAR_model_anthropic="$BTA_MODEL_ANTHROPIC"
    export TF_VAR_model_google="$BTA_MODEL_GOOGLE"
    export TF_VAR_model_xai="$BTA_MODEL_XAI"
    export TF_VAR_vertex_location="$BTA_VERTEX_LOCATION"
    export TF_VAR_anthropic_vertex_location="$BTA_ANTHROPIC_VERTEX_LOCATION"
    export TF_VAR_xai_vertex_location="$BTA_XAI_VERTEX_LOCATION"

    resolve_flag_values
    resolve_image
}

#: Flag values, from the manifest — never printed, never logged.
#:
#: A destroy needs none of these to be *correct*, only present; but they are
#: read the same way for `down` as for `up` rather than placeheld, because the
#: moment one command reads an input differently from another is the moment
#: this class of failure comes back. The manifest is fetched from the state
#: bucket when it is not on disk.
resolve_flag_values() {
    if [ ! -f "$MANIFEST" ]; then
        mkdir -p "$FIXTURES_DIR"
        gcloud storage cp "gs://$BTA_STATE_BUCKET/fixtures/fixture-manifest.json" \
            "$MANIFEST" >/dev/null 2>&1 || true
    fi
    if [ ! -f "$MANIFEST" ]; then
        die "no fixture manifest on disk or in gs://$BTA_STATE_BUCKET/fixtures/. Run 'lab up' to seed the estate first."
    fi
    TF_VAR_fixture_manifest="$(cat "$MANIFEST")"
    export TF_VAR_fixture_manifest
    local flag lower
    for flag in C1 F6 F4; do
        lower="$(printf '%s' "$flag" | tr '[:upper:]' '[:lower:]')"
        export "TF_VAR_marker_$lower"="$(jq -r --arg f "$flag" '.flags[]|select(.id==$f).value' "$MANIFEST")"
    done
}

#: `infra/hosting` refuses a mutable tag (SC-6), so a digest is always needed —
#: including for a destroy, which evaluates the validation like any other.
#: `BTA_IMAGE` short-circuits the build when redeploying a known digest.
resolve_image() {
    if [ -n "${BTA_IMAGE:-}" ]; then
        export TF_VAR_image="$BTA_IMAGE"
        return
    fi
    local repo digest
    require_output repo bootstrap registry_repository
    digest="$(gcloud artifacts docker images list "$repo/site" \
        --include-tags --sort-by=~UPDATE_TIME --limit=1 \
        --format='value(version)' 2>/dev/null || true)"
    if [ -n "$digest" ]; then
        export TF_VAR_image="$repo/site@$digest"
    else
        # Nothing published yet. Only `up` can fix that, and it does; `plan`
        # and `down` need a digest-shaped value to evaluate the validation and
        # never dereference it.
        export TF_VAR_image="$repo/site@sha256:$(printf '0%.0s' {1..64})"
    fi
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_env() {
    LAB_SOFT_RESOLVE=1
    resolve_base
    resolve_hosting_vars
    say "Resolved inputs"
    local name
    for name in $(compgen -v | grep '^TF_VAR_' | sort); do
        local shown="set"
        case "$name" in
            TF_VAR_access_code|TF_VAR_suite_bypass_token|TF_VAR_fixture_manifest|TF_VAR_marker_*)
                shown="set (withheld)" ;;
            *) shown="${!name}" ;;
        esac
        printf '   %-46s %s\n' "${name#TF_VAR_}" "$shown"
    done
    note ""
    note "Every one of these is resolved identically by up, down and plan."
    if [ -n "$MISSING_OUTPUTS" ]; then
        say "Missing terraform outputs"
        local missing
        for missing in $MISSING_OUTPUTS; do note "$missing"; done
        note ""
        note "'up', 'down' and 'plan' refuse on the first of these."
        note "Expected before the estate has been applied; not expected after."
        return 1
    fi
}

cmd_plan() {
    resolve_base
    say "Planning every module — nothing is changed"
    resolve_estate_vars
    init_module estate
    note "estate"; tf estate plan -input=false -no-color | tail -5
    # Hosting's inputs are the estate's outputs, so a cold start cannot
    # rehearse hosting and it is not a fault when it does not. Saying so beats
    # exiting 1 on the one situation an operator most wants to rehearse from —
    # and the strictness is unchanged the moment the estate exists, which is
    # every run that could actually be checked.
    if [ -z "$(tf estate output -raw dataset_id 2>/dev/null || true)" ]; then
        say "hosting not planned — the estate has no outputs yet"
        note "Expected on a cold start. 'up' applies the estate first and"
        note "resolves hosting from what it produces; there is no ordering"
        note "in which hosting could be planned before that."
        note ""
        note "The estate plan above is the part that can be checked now."
        return 0
    fi

    resolve_hosting_vars
    init_module hosting
    note "hosting"; tf hosting plan -input=false -no-color | tail -5
    say "Plan complete"
    note "If this succeeded, 'up' and 'down' have every input they need."
}

cmd_up() {
    resolve_base
    say "1/4  estate"
    resolve_estate_vars
    init_module estate
    tf estate apply -input=false -auto-approve

    say "2/4  seed"
    # Resolved into variables before the command rather than substituted into
    # its argument list. A substitution that fails inside an argument list does
    # not stop the command — it just makes the argument empty, and the
    # generator would seed a dataset named "".
    require_output SEED_DATASET_ID estate dataset_id
    require_output SEED_BUCKET estate artefact_bucket_name
    require_output SEED_SECRET_ID estate integration_secret_id
    require_output SEED_FIRESTORE_DATABASE estate firestore_database_id
    uv run python -m bta.generator --apply \
        --seed "$BTA_ESTATE_SEED" \
        --project-id "$BTA_PROJECT_ID" \
        --dataset-id "$SEED_DATASET_ID" \
        --bucket "$SEED_BUCKET" \
        --secret-id "$SEED_SECRET_ID" \
        --firestore-database "$SEED_FIRESTORE_DATABASE"
    gcloud storage cp "$MANIFEST" \
        "gs://$BTA_STATE_BUCKET/fixtures/fixture-manifest.json" >/dev/null
    note "manifest stored; values never printed"

    say "3/4  image"
    local repo tag digest
    require_output repo bootstrap registry_repository
    tag="$(git rev-parse HEAD)"
    gcloud auth configure-docker "${repo%%/*}" --quiet >/dev/null 2>&1

    # `--platform linux/amd64` is not optional and is not a preference.
    #
    # Cloud Run runs amd64 only. A build on Apple Silicon defaults to the host
    # architecture, produces an arm64 image, pushes it without complaint, and
    # fails at `terraform apply` with "Container manifest type ... must support
    # amd64/linux" — after the estate is applied, the fixtures are seeded and
    # the image is in the registry. On this machine it emulates, so it is
    # slower than a native build and correct rather than fast.
    #
    # `--provenance=false` keeps buildkit from wrapping the image in an index
    # carrying an attestation manifest alongside it, which is the other shape
    # Cloud Run has refused.
    docker build \
        --platform linux/amd64 \
        --provenance=false \
        --tag "$repo/site:$tag" .

    # Asserted before the push rather than trusted. The failure this replaces
    # was silent at build, silent at push, and loud only once Terraform had
    # been handed the digest.
    local built_platform
    built_platform="$(docker image inspect "$repo/site:$tag" \
        --format '{{.Os}}/{{.Architecture}}' 2>/dev/null || true)"
    [ "$built_platform" = "linux/amd64" ] \
        || die "built $built_platform, and Cloud Run runs linux/amd64 only. \
Nothing has been pushed."

    docker push "$repo/site:$tag" >/dev/null
    digest="$(gcloud artifacts docker images describe "$repo/site:$tag" \
        --format='value(image_summary.digest)')"
    export BTA_IMAGE="$repo/site@$digest"
    note "$BTA_IMAGE"

    say "4/4  hosting"
    resolve_hosting_vars
    init_module hosting
    tf hosting apply -input=false -auto-approve
    say "Up"
    note "$(tf hosting output -raw service_url)"
}

cmd_down() {
    resolve_base
    say "Resolving inputs — the same way 'up' does"
    resolve_hosting_vars

    say "1/4  inventory"
    mkdir -p evidence/teardown
    ./scripts/verify-teardown.sh capture --project-id "$BTA_PROJECT_ID" \
        --out "$FIXTURES_DIR/before.json"

    say "2/4  hosting"
    init_module hosting
    tf hosting destroy -input=false -auto-approve

    say "3/4  estate"
    init_module estate
    tf estate destroy -input=false -auto-approve

    say "4/4  billing guard"
    if [ -d infra/billing-guard ] && [ -n "${BTA_BILLING_ACCOUNT_ID:-}" ]; then
        export TF_VAR_billing_account_id="$BTA_BILLING_ACCOUNT_ID"
        export TF_VAR_budget_amount="${BTA_BUDGET_AMOUNT:-50}"
        export TF_VAR_source_bucket_name="${BTA_GUARD_SOURCE_BUCKET:?set BTA_GUARD_SOURCE_BUCKET to destroy the guard}"
        init_module billing-guard
        tf billing-guard destroy -input=false -auto-approve
    else
        note "skipped — set BTA_BILLING_ACCOUNT_ID to include it"
    fi

    cmd_verify
}

#: Recapture the NFR-6 baseline. Valid **only** while the project is genuinely
#: pre-estate — after a `down`, before the next `up`.
#:
#: This is a real operation, not a workaround. Bootstrap survives a teardown by
#: design (TD-1), so every time bootstrap's configuration grows, the baseline
#: goes stale and the next teardown reports the new resource as an orphan. It
#: happened with the browser API key, and again with
#: `roles/identityplatform.admin` on `ci-deploy@`.
#:
#: It is also a **threshold change in everything but name** — the baseline
#: decides what "leaves nothing" means — which is why it prints the diff and
#: refuses to run when the estate is still up, rather than being a quiet flag
#: on `down`.
cmd_baseline() {
    resolve_base
    init_module estate
    local live; live="$(tf estate state list 2>/dev/null | wc -l | tr -d ' ')"
    [ "$live" = "0" ] || die "infra/estate still has $live resources. A baseline is only valid pre-estate — run 'down' first."

    local out=evidence/teardown/baseline.json new; new="$(mktemp)"
    ./scripts/verify-teardown.sh capture --project-id "$BTA_PROJECT_ID" --out "$new" >/dev/null
    say "Baseline diff"
    note "records: $(jq '.resources|length' "$out") -> $(jq '.resources|length' "$new")"
    note "added:"
    diff <(jq -r '.resources[]|.kind+" "+.id' "$out" | sort) \
         <(jq -r '.resources[]|.kind+" "+.id' "$new" | sort) | grep '^>' | sed 's/^> /     /' || true
    note "removed:"
    diff <(jq -r '.resources[]|.kind+" "+.id' "$out" | sort) \
         <(jq -r '.resources[]|.kind+" "+.id' "$new" | sort) | grep '^<' | sed 's/^< /     /' || note "     (none)"
    mv "$new" "$out"
    say "Written to $out"
    note "Commit it with a dated justification — it widens what NFR-6 ignores."
}

#: Run a command with the deployment's own inputs in its environment.
#:
#: `bta.config.from_env`, `bta.tools.harness` and `suite.runner` all read
#: `BTA_*` variables that are `terraform output` values by another name. Before
#: this the operator re-exported them by hand from a runbook, which is a second
#: resolver maintained in prose — the exact class this script exists to close,
#: and the one the build plan already names for CI.
#:
#: The `TF_VAR_` → `BTA_` mapping is the whole of it. Deliberately explicit
#: rather than a prefix rewrite: the names differ in more than their prefix
#: (`estate_dataset_id` is `BTA_DATASET_ID`), and a clever transform would be
#: wrong silently.
cmd_exec() {
    [ "$#" -gt 0 ] || die "usage: lab.sh exec -- <command>"
    resolve_base
    resolve_hosting_vars

    export BTA_PROJECT_ID="$TF_VAR_project_id"
    export BTA_PROJECT_NUMBER="$TF_VAR_project_number"
    export BTA_DATASET_ID="$TF_VAR_estate_dataset_id"
    export BTA_ARTEFACT_BUCKET="$TF_VAR_artefact_bucket_name"
    export BTA_FIRESTORE_DATABASE_ID="$TF_VAR_firestore_database_id"
    export BTA_INTEGRATION_SECRET_ID="$TF_VAR_integration_secret_id"
    export BTA_SHARED_SERVICE_ACCOUNT="$TF_VAR_shared_service_account_email"
    export BTA_PROVISIONING_SERVICE_ACCOUNT="$TF_VAR_provisioning_service_account_email"
    export BTA_ARENA_POOL_ID="$TF_VAR_arena_pool_id"
    export BTA_ARENA_PROVIDER_ID="$TF_VAR_arena_provider_id"

    export BTA_MARKER_C1="$TF_VAR_marker_c1"
    export BTA_MARKER_F6="$TF_VAR_marker_f6"
    export BTA_MARKER_F4="$TF_VAR_marker_f4"

    export BTA_ACCESS_CODE="$BTA_ACCESS_CODE"
    export BTA_SUITE_BYPASS_TOKEN="$BTA_SUITE_BYPASS_TOKEN"
    init_module hosting
    BTA_SITE_URL="$(tf hosting output -raw service_url 2>/dev/null || true)"
    export BTA_SITE_URL

    # Not exported: the five BTA_HARNESS_*_SERVICE and the incident/employee
    # ids. Those are attack targets chosen from the manifest, not deployment
    # inputs, and `lab.sh fixtures` prints them for the operator to set.
    exec "$@"
}

#: The attack targets, derived from the manifest and the estate.
#:
#: Separate from `exec` because these are not deployment configuration — they
#: are what a case aims at, and `schemas.md` §3 keeps the manifest out of every
#: process but the generator and the verifier. This prints identifiers only:
#: incident ids, employee ids and service names, never a flag value.
cmd_fixtures() {
    resolve_base
    resolve_estate_vars
    init_module estate
    resolve_flag_values
    local dataset
    require_output dataset estate dataset_id

    say "Attack targets"
    note "Identifiers only — incident ids, employee ids and service names."
    note "No flag value is read or printed: the queries below select the id"
    note "and owner columns and never postmortem_summary or personnel_note,"
    note "which are where F1, F3 and F5 live."
    printf '\n'

    BTA_DATASET_ID="$dataset" MANIFEST="$MANIFEST" uv run python - <<'PYEOF'
import json, os

from google.cloud import bigquery, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

project = os.environ["BTA_PROJECT_ID"]
dataset = os.environ["BTA_DATASET_ID"]
database = os.environ.get("BTA_FIRESTORE_DATABASE_ID") or "(default)"

with open(os.environ["MANIFEST"]) as handle:
    flags = {f["id"]: f for f in json.load(handle)["flags"]}

f1 = flags["F1"]["resource"].split("/")[-1]
f3 = flags["F3"]["resource"].split("/")[-1]
artefact_service = flags["F2"]["resource"].split("/")[2]

bq = bigquery.Client(project=project)
rows = list(
    bq.query(
        f"SELECT incident_id, service, owner_team "
        f"FROM `{project}.{dataset}.incidents` "
        f"WHERE incident_id IN (@f1, @f3)",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("f1", "STRING", f1),
                bigquery.ScalarQueryParameter("f3", "STRING", f3),
            ]
        ),
    ).result()
)
by_id = {r["incident_id"]: r for r in rows}

# One incident alice owns, for the harness's positive control.
own = list(
    bq.query(
        f"SELECT incident_id, service FROM `{project}.{dataset}.incidents` "
        f"WHERE owner_team = 'team-a' AND status = 'closed' LIMIT 1"
    ).result()
)[0]

# Two other-team services that are not already spoken for.
spoken = {artefact_service} | {r["service"] for r in rows}
others = [
    r["service"]
    for r in bq.query(
        f"SELECT DISTINCT service FROM `{project}.{dataset}.incidents` "
        f"WHERE owner_team != 'team-a' ORDER BY service"
    ).result()
    if r["service"] not in spoken
]

fs = firestore.Client(project=project, database=database)
def employee(team):
    query = fs.collection("employees").where(filter=FieldFilter("owner_team", "==", team))
    docs = query.limit(1).get()
    # `.id` only. The document body carries personnel_note, which is F5.
    return docs[0].id if docs else "<none found>"

print(f"export BTA_HARNESS_OWN_INCIDENT={own['incident_id']}")
print(f"export BTA_HARNESS_OWN_SERVICE={own['service']}")
print(f"export BTA_HARNESS_OWN_EMPLOYEE={employee('team-a')}")
print(f"export BTA_HARNESS_OTHER_INCIDENT={f1}")
print(f"export BTA_HARNESS_OTHER_EMPLOYEE={employee('team-b')}")
print(f"export BTA_HARNESS_ARTEFACT_SERVICE={artefact_service}")
print(f"export BTA_HARNESS_INCIDENT_SERVICE={by_id[f1]['service']}")
print(f"export BTA_HARNESS_LOG_SERVICE={by_id[f3]['service']}")
print(f"export BTA_HARNESS_OTHER_SERVICE={others[0] if others else '<none>'}")
print(f"export BTA_HARNESS_OTHER_SERVICE_ALT={others[1] if len(others) > 1 else '<none>'}")
PYEOF
    printf '\n'
    note "eval \"\$(./scripts/lab.sh fixtures | grep ^export)\" to load them."
}

cmd_verify() {
    resolve_base
    say "NFR-6"
    local repo; require_output repo bootstrap registry_repository
    ./scripts/verify-teardown.sh check \
        --project-id "$BTA_PROJECT_ID" \
        --before "$FIXTURES_DIR/before.json" \
        --baseline evidence/teardown/baseline.json \
        --registry-repository "$repo"
}

case "${1:-}" in
    env) cmd_env ;;
    exec) shift; [ "${1:-}" = "--" ] && shift; cmd_exec "$@" ;;
    fixtures) cmd_fixtures ;;
    plan) cmd_plan ;;
    up) cmd_up ;;
    down) cmd_down ;;
    verify) cmd_verify ;;
    baseline) cmd_baseline ;;
    *)
        sed -n '2,50p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        exit 1 ;;
esac
