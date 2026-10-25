#!/usr/bin/env bash
#
# NFR-6: `terraform destroy` removes every provisioned resource. Verified, not
# assumed.
#
# ── Why this is not just reading Terraform's output ──────────────────────────
#
# "Destroy complete! 47 destroyed." is a statement about state, not about the
# project. Terraform knows what it tracked. The resources that generate a bill
# after a lab is torn down are precisely the ones it did not: a table a log sink
# created inside a dataset, a ruleset left behind when its release was deleted,
# a bucket someone made by hand during a spike. So this script asks the cloud
# what exists, and never asks Terraform anything.
#
# ── The comparison it makes ──────────────────────────────────────────────────
#
# Enumerating after destroy and asserting "nothing" is the wrong test: the
# project is not meant to be empty, because bootstrap deliberately survives
# (state bucket, WIF pools, enabled APIs — see infra/bootstrap/README.md and
# TD-1). So teardown is verified as a *return to a baseline*:
#
#   capture --out baseline.json     after bootstrap, before estate exists
#   capture --out seeded.json       after estate apply and the generator run
#   ... terraform destroy ...
#   check --baseline baseline.json --before seeded.json
#
# Anything live that appeared between baseline and seeded, and is still here,
# is an orphan. This needs no list of resource names in this file, which is
# also what keeps it free of deployment-specific values (POR-6).
#
# For NFR-6 read literally — nothing at all survives — pass
# `--baseline /dev/null`. That is the check bootstrap currently fails by design.
#
# ── A failed enumerator is a failure, not a gap ──────────────────────────────
#
# An enumerator that errors and is skipped turns into a clean bill of health for
# a surface nobody looked at. Every error is recorded in the evidence and fails
# the check, for the same reason `insufficient` is a legitimate verdict
# elsewhere in this project: not knowing is not the same as nothing being there.
#
# ── The backstop is reconciled, not trusted ──────────────────────────────────
#
# Cloud Asset Inventory finds resource types nobody wrote an enumerator for, and
# is the only enumerator here that does not ask the API owning the resource. It
# names things differently from that API and it reports things that no longer
# exist, and both faults manufacture orphans. See `normalise_assets`.
#
# Nothing here reads or prints resource *contents*, only names. The estate holds
# live flag values; an inventory that quoted a row would put one in an evidence
# file that is meant to be committable.

set -uo pipefail

# gcloud offers to enable a disabled API and waits for an answer. Unattended
# that hangs; answered by accident it would enable an API on the project under
# test, during the run that is meant to observe it.
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROGRAM=$(basename "$0")
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

usage() {
    cat <<EOF
Verify that a teardown left nothing behind (NFR-6).

  $PROGRAM capture --project-id ID [--out FILE]
  $PROGRAM check   --project-id ID --before FILE [--baseline FILE] [--out FILE]

capture   Enumerate every resource the project currently holds, from the cloud
          rather than from Terraform state, and write it as JSON.

check     Enumerate again and compare. Exits non-zero if anything that appeared
          between --baseline and --before is still live, or if any enumerator
          failed to answer.

Options
  --project-id ID   Required. No default: this script is not tied to a
                    deployment (POR-6).
  --before FILE     Inventory captured while the estate existed.
  --baseline FILE   Inventory captured before the estate existed. Resources
                    present here are out of scope. Use /dev/null to require an
                    empty project.
  --out FILE        Where to write the evidence. Default: stdout for capture,
                    and no file for check.
  --registry-repository REF
                    The arena's image repository, whole reference or bare id.
                    Images in it survive a destroy by design — bootstrap owns
                    the repository so that a digest exists before hosting is
                    applied. Without this every teardown after a deploy reports
                    the site's own image as an orphan. A deployment value, so
                    it is an argument rather than a constant (POR-6).

Requires: gcloud (authenticated), bq, jq, curl.
EOF
}

die() {
    printf '%s: %s\n' "$PROGRAM" "$1" >&2
    exit 2
}

# ---------------------------------------------------------------------------
# Enumerators
#
# Each prints TSV: kind <TAB> id <TAB> state. `state` is `live` unless the API
# reports the resource as soft-deleted, which several of them do and which is
# the difference between "gone" and "gone but the name is unusable for 30 days"
# (spike 0.1 finding 6).
#
# Each returns non-zero if it could not answer. None of them tolerates its own
# failure — that is `collect`'s job, and it does not tolerate it either.
# ---------------------------------------------------------------------------

emit() { printf '%s\t%s\t%s\n' "$1" "$2" "${3:-live}"; }

enum_bigquery() {
    local datasets dataset
    datasets=$(bq --project_id="$PROJECT" --format=json ls --datasets --max_results=1000) || return 1
    for dataset in $(jq -r '.[]?.datasetReference.datasetId' <<<"$datasets"); do
        emit bigquery_dataset "$dataset"
        # Tables are enumerated separately rather than assumed to die with the
        # dataset. A dataset without `delete_contents_on_destroy` refuses to be
        # deleted while it holds tables, and the failure mode worth catching is
        # the dataset surviving *because* of a table nobody knew was in it.
        local tables
        tables=$(bq --project_id="$PROJECT" --format=json ls --max_results=1000 "$dataset") || return 1
        jq -r --arg d "$dataset" '.[]?.tableReference.tableId | "\($d).\(.)"' <<<"$tables" |
            while read -r table; do emit bigquery_table "$table"; done
    done
}

enum_storage() {
    local buckets bucket
    buckets=$(gcloud storage buckets list --project="$PROJECT" --format='value(name)') || return 1
    while read -r bucket; do
        [ -n "$bucket" ] || continue
        emit storage_bucket "$bucket" "$(_bucket_state "$bucket")"
    done <<<"$buckets"
}

# `empty` means holding no objects at all, soft-deleted ones included. That
# distinction is the whole point of asking: buckets carry a seven-day
# soft-delete policy by default, `gcloud storage ls` does not show what is
# under it, and retained objects are billed. A bucket `ls` calls empty can be
# holding a week of data. Verified on `gcf-v2-sources-*` after the first
# teardown, where `ls` reported nothing and two retained copies of the
# function source were sitting in it.
#
# Anything this cannot establish reports `live`, including a gcloud that does
# not know `--soft-deleted`. The exemption in `classify` keys on `empty` and
# must not be reachable by failing to look.
#
# Both passes are kept even though the plain one was observed returning
# soft-deleted generations too, because that is not documented behaviour to
# rest an exemption on. The one failure that is not a failure is a bucket with
# no soft-delete policy at all: `--soft-deleted` rejects it outright, and a
# bucket that cannot hold soft-deleted objects has none.
_bucket_state() {
    local bucket=$1 flag listing status
    for flag in "" "--soft-deleted"; do
        # shellcheck disable=SC2086 # $flag is deliberately word-split away when empty
        listing=$(gcloud storage objects list "gs://$bucket/**" $flag \
            --format='value(name)' 2>"$WORK/bucket.err")
        status=$?
        if [ $status -ne 0 ]; then
            case "$(tr -d '\n' <"$WORK/bucket.err")" in
                *"Soft delete policy is required"*) continue ;;
                *) echo live; return ;;
            esac
        fi
        [ -z "$listing" ] || { echo live; return; }
    done
    echo empty
}

enum_secrets() {
    gcloud secrets list --project="$PROJECT" --format='value(name)' |
        while read -r secret; do emit secret "$secret"; done
}

enum_service_accounts() {
    gcloud iam service-accounts list --project="$PROJECT" --format='value(email)' |
        while read -r account; do emit service_account "$account"; done
}

enum_wif() {
    # `--show-deleted`, because a soft-deleted pool is invisible without it while
    # still holding its ID for 30 days. A teardown check that could not see one
    # would report a clean project and then the next apply would fail on a name
    # collision it had just been told did not exist.
    local pools pool state
    pools=$(gcloud iam workload-identity-pools list \
        --project="$PROJECT" --location=global --show-deleted \
        --format='value(name,state)') || return 1
    while IFS=$'\t' read -r pool state; do
        [ -n "$pool" ] || continue
        local id=${pool##*/}
        emit wif_pool "$id" "$(_wif_state "$state")"
        # A soft-deleted pool has no listable providers — the call returns
        # NOT_FOUND, which `collect` would correctly read as "this enumerator
        # could not answer" and fail the run. The pool itself is already
        # recorded, and its providers went with it.
        [ "$state" = "DELETED" ] && continue
        local providers
        providers=$(gcloud iam workload-identity-pools providers list \
            --project="$PROJECT" --location=global --workload-identity-pool="$id" \
            --show-deleted --format='value(name,state)') || return 1
        while IFS=$'\t' read -r provider pstate; do
            [ -n "$provider" ] || continue
            emit wif_provider "$id/${provider##*/}" "$(_wif_state "$pstate")"
        done <<<"$providers"
    done <<<"$pools"
}

_wif_state() {
    case "$1" in
        DELETED) echo deleted ;;
        *) echo live ;;
    esac
}

enum_firestore() {
    gcloud firestore databases list --project="$PROJECT" --format='value(name)' |
        while read -r database; do emit firestore_database "${database##*/}"; done
}

enum_rulesets() {
    # Firebase Rules has no stable gcloud surface, and it is worth reaching for
    # anyway: deleting a release does not delete the ruleset it pointed at, so
    # this is a real orphan path rather than a completionist one.
    local token response
    token=$(gcloud auth print-access-token) || return 1
    # `x-goog-user-project` is not optional here. Under user ADC this API
    # refuses without a quota project, and the refusal it returns is a 403
    # carrying `SERVICE_DISABLED` — which reads exactly like "the API is off"
    # and would be filed as an unobservable surface rather than a bug in this
    # call. No `-f`: the body is how the two are told apart.
    response=$(curl -sS -H "Authorization: Bearer $token" \
        -H "x-goog-user-project: $PROJECT" \
        "https://firebaserules.googleapis.com/v1/projects/$PROJECT/rulesets?pageSize=100") || return 1
    if jq -e 'has("error")' >/dev/null <<<"$response"; then
        jq -r '.error.message' <<<"$response" >&2
        return 1
    fi
    jq -r '.rulesets[]?.name' <<<"$response" |
        while read -r ruleset; do emit firebase_ruleset "${ruleset##*/}"; done
}

# Cloud Run is enumerated because Cloud Functions gen2 *is* Cloud Run: deploying
# the billing guard creates a service and a revision under the covers, and
# deleting the function removes them. Cloud Asset Inventory goes on reporting
# both for a while afterwards, which is how run 32294015789 produced two
# orphans for a service that `gcloud run services list` says does not exist.
#
# Without an enumerator there was nothing to contradict the index: `run` has no
# entry in the probe's host map, and a revision's asset path carries no service
# segment so it cannot be mapped onto the v2 REST shape either. An enumerator
# settles both, and Cloud Run stops being an unwatched surface before phase 4
# puts the site on it.
#
# Revisions are listed per region rather than globally, because `revisions list`
# refuses without one. The regions come from the services, which is sound in the
# direction that matters: a revision cannot outlive the service that owns it, so
# no services means no revisions, and the empty answer is a real answer.
enum_cloudrun() {
    local services name region regions="" revisions revision
    services=$(gcloud run services list --project="$PROJECT" \
        --format="value(metadata.name,metadata.labels['cloud.googleapis.com/location'])") || return 1
    local located=0 unlocated=0
    while IFS=$'\t' read -r name region; do
        [ -n "$name" ] || continue
        emit cloud_run_service "$name"
        if [ -z "$region" ]; then
            unlocated=$((unlocated + 1))
            continue
        fi
        located=$((located + 1))
        case " $regions " in
            *" $region "*) ;;
            *) regions="$regions $region" ;;
        esac
    done <<<"$services"

    # A service whose region cannot be read is a surface this cannot reach, and
    # answering anyway would be worse than failing. `normalise_assets` treats an
    # enumerator that answered as authoritative, so returning success here with
    # no revisions listed would mark every revision the index reports as stale —
    # clearing real orphans on the strength of a label that did not resolve.
    # Fail instead, and let `collect` record it.
    if [ "$unlocated" -gt 0 ]; then
        printf 'cloud run: %d service(s) with no readable location label\n' \
            "$unlocated" >&2
        return 1
    fi

    for region in $regions; do
        revisions=$(gcloud run revisions list --project="$PROJECT" --region="$region" \
            --format="value(metadata.name)") || return 1
        while read -r revision; do
            [ -n "$revision" ] || continue
            emit cloud_run_revision "$revision"
        done <<<"$revisions"
    done
}

enum_sinks() {
    gcloud logging sinks list --project="$PROJECT" --format='value(name)' |
        while read -r sink; do emit logging_sink "$sink"; done
}

enum_services() {
    gcloud services list --enabled --project="$PROJECT" --format='value(config.name)' |
        while read -r service; do emit enabled_service "$service"; done
}

enum_iam() {
    # Role/member pairs, not just members. A binding left behind after its
    # service account is deleted becomes `deleted:serviceAccount:...?uid=`, which
    # is harmless but is also the fingerprint of a destroy that did not finish.
    local policy
    policy=$(gcloud projects get-iam-policy "$PROJECT" --format=json) || return 1
    jq -r '.bindings[]? | .role as $r | .members[]? | "\($r) \(.)"' <<<"$policy" |
        while read -r binding; do emit iam_binding "$binding"; done
}

enum_assets() {
    # The backstop, and the only enumerator here that can find a resource type
    # nobody thought to write a function for. It is also the only one that does
    # not ask the API that owns the resource, and that costs it twice — see
    # `normalise_assets`, which is where both costs are paid.
    local assets
    assets=$(gcloud asset search-all-resources --scope="projects/$PROJECT" \
        --format='value(assetType,name)' --page-size=500) || return 1
    while IFS=$'\t' read -r type name; do
        [ -n "$type" ] || continue
        emit "asset:$type" "$name"
    done <<<"$assets"
}

# ---------------------------------------------------------------------------
# Reconciling the backstop with the enumerators above
#
# Cloud Asset Inventory names a resource differently from the API that owns it,
# and indexes it on its own schedule. Left alone, each fault produces a false
# orphan, and in run 32229645507 the two together produced 187 of 190.
#
# **Naming.** `asset:iam.googleapis.com/ServiceAccount` with an id of
# `//iam.googleapis.com/projects/P/serviceAccounts/ci-deploy@...` and
# `service_account` with an id of `ci-deploy@...` are one survivor under two
# names. The baseline records the second, the backstop reports the first, and
# nothing in a key-by-key diff can join them — so every bootstrap resource that
# both see is reported as an orphan. `asset_native_kind` and `asset_native_id`
# translate the backstop's vocabulary into the owning enumerator's.
#
# **Staleness.** The index lags, and not only by minutes: after the estate was
# destroyed it was still returning `spike_team_a` and three `incidents` tables
# that `bq ls` had stopped listing weeks earlier, along with a secret version
# and two pool providers that were already gone. The comment this replaces
# assumed the lag erred safe — a false orphan costs a re-run, a missed one
# costs a bill — which is true of one stale record and false of a hundred, at
# which point the check reports `failed` every time and stops being read.
#
# So an enumerator that asked the owning API and answered is authoritative for
# its kinds. If it did not report a resource the index claims, the index is
# wrong, and the record is dropped as stale rather than counted. Where the
# owning enumerator could *not* answer, the index fills the gap instead: that
# is the surface it exists to cover.
# ---------------------------------------------------------------------------

asset_native_kind() {
    case "$1" in
        bigquery.googleapis.com/Dataset) echo bigquery_dataset ;;
        bigquery.googleapis.com/Table) echo bigquery_table ;;
        storage.googleapis.com/Bucket) echo storage_bucket ;;
        secretmanager.googleapis.com/Secret) echo secret ;;
        iam.googleapis.com/ServiceAccount) echo service_account ;;
        iam.googleapis.com/WorkloadIdentityPool) echo wif_pool ;;
        iam.googleapis.com/WorkloadIdentityPoolProvider) echo wif_provider ;;
        firestore.googleapis.com/Database) echo firestore_database ;;
        firebaserules.googleapis.com/Ruleset) echo firebase_ruleset ;;
        logging.googleapis.com/LogSink) echo logging_sink ;;
        serviceusage.googleapis.com/Service) echo enabled_service ;;
        run.googleapis.com/Service) echo cloud_run_service ;;
        run.googleapis.com/Revision) echo cloud_run_revision ;;
        *) echo "" ;;
    esac
}

# Which enumerator owns a kind, and so decides whether the index is telling the
# truth about it.
kind_owner() {
    case "$1" in
        bigquery_dataset | bigquery_table) echo bigquery ;;
        storage_bucket) echo storage ;;
        secret) echo secrets ;;
        service_account) echo service_accounts ;;
        wif_pool | wif_provider) echo wif ;;
        firestore_database) echo firestore ;;
        firebase_ruleset) echo rulesets ;;
        logging_sink) echo sinks ;;
        enabled_service) echo services ;;
        cloud_run_service | cloud_run_revision) echo cloudrun ;;
        iam_binding) echo iam ;;
        *) echo "" ;;
    esac
}

# The id the owning enumerator would have written for the same resource.
asset_native_id() {
    local kind=$1 name=$2 tail=${2##*/} parent
    case "$kind" in
        bigquery_table)
            parent=${name#*/datasets/}
            printf '%s.%s\n' "${parent%%/*}" "$tail"
            ;;
        wif_provider)
            parent=${name#*/workloadIdentityPools/}
            printf '%s/%s\n' "${parent%%/*}" "$tail"
            ;;
        *) printf '%s\n' "$tail" ;;
    esac
}

# Some asset types have no enumerator of their own but hang off one that does.
# A row access policy is not independently billable and cannot outlive its
# dataset; a secret version cannot outlive its secret. If the owning enumerator
# answered and did not report the parent, the child is stale for the same
# reason the parent would have been. Prints `kind<TAB>id`, or nothing.
asset_parent_key() {
    local type=$1 name=$2 parent
    case "$type" in
        bigquery.googleapis.com/RowAccessPolicy)
            parent=${name#*/datasets/}
            printf 'bigquery_dataset\t%s\n' "${parent%%/*}"
            ;;
        secretmanager.googleapis.com/SecretVersion)
            parent=${name#*/secrets/}
            printf 'secret\t%s\n' "${parent%%/*}"
            ;;
    esac
}

# Reads the raw asset records and writes the reconciled ones. Anything judged
# stale goes to $WORK/stale.tsv instead, which is reported rather than dropped
# silently — the same rule the residue carve-outs follow.
normalise_assets() {
    local kind id state type native nid owner key
    while IFS=$'\t' read -r kind id state; do
        [ -n "$kind" ] || continue
        type=${kind#asset:}
        native=$(asset_native_kind "$type")

        if [ -n "$native" ]; then
            nid=$(asset_native_id "$native" "$id")
            key=$(printf '%s\t%s' "$native" "$nid")
        else
            key=$(asset_parent_key "$type" "$id")
            [ -n "$key" ] || { printf '%s\t%s\t%s\n' "$kind" "$id" "$state"; continue; }
        fi

        owner=$(kind_owner "${key%%$'\t'*}")
        if [ -n "$owner" ] && grep -qxF "$owner" "$WORK/answered.txt"; then
            if grep -qxF "$key" "$WORK/native.keys"; then
                # The owning enumerator saw it too. Its record already stands,
                # and carries a state this one does not know (`empty`,
                # `deleted`), so the index adds nothing.
                [ -n "$native" ] || printf '%s\t%s\t%s\n' "$kind" "$id" "$state"
            else
                printf '%s\t%s\t%s\n' "$kind" "$id" \
                    "the $owner enumerator answered and does not list it; index record is stale" \
                    >>"$WORK/stale.tsv"
            fi
            continue
        fi

        # No enumerator could vouch either way, so the backstop stands. This is
        # the case it was added for.
        if [ -n "$native" ]; then
            printf '%s\t%s\t%s\n' "$native" "$nid" "$state"
        else
            printf '%s\t%s\t%s\n' "$kind" "$id" "$state"
        fi
    done
}

ENUMERATORS=(
    bigquery storage secrets service_accounts wif firestore
    rulesets sinks services iam cloudrun
)

# Runs one enumerator, and sorts what goes wrong into two piles.
#
# **unobservable** — the surface's API is not enabled, so the question cannot be
# asked. Expected on a baseline capture taken before the estate turned its APIs
# on, and harmless there: an under-reported baseline widens the in-scope set and
# produces false orphans, which costs a re-run rather than a bill. Not harmless
# on a check, because `disable_on_destroy = false` means every estate API is
# still enabled after a destroy — so an API that is off during a check is itself
# the surprise, and "cannot see" is not "nothing there".
#
# **error** — anything else. Always fatal. A surface nobody could look at must
# not read as a surface with nothing on it.
#
# Records the name of every enumerator that answered. `normalise_assets` needs
# it: only an enumerator that actually reached its API can be used to contradict
# the asset index, and one that failed must not be read as having found nothing.
run_enumerator() {
    local name=$1 out=$2
    if "enum_$name" >"$out" 2>"$WORK/one.err"; then
        printf '%s\n' "$name" >>"$WORK/answered.txt"
        return 0
    fi
    : >"$out"
    local message
    message=$(tr '\n' ' ' <"$WORK/one.err" | tr -s ' ' | cut -c1-300)
    case "$message" in
        *"not enabled on project"* | *SERVICE_DISABLED* | *"has not been used in project"*)
            printf '%s: API not enabled\n' "$name" >>"$WORK/unobservable.txt" ;;
        *)
            printf '%s: %s\n' "$name" "$message" >>"$WORK/errors.txt" ;;
    esac
    return 1
}

# The backstop runs last and is reconciled against the rest, so the enumerators
# that ask an owning API have all had their say before the index is read.
collect() {
    : >"$WORK/records.tsv"
    : >"$WORK/errors.txt"
    : >"$WORK/unobservable.txt"
    : >"$WORK/answered.txt"
    : >"$WORK/stale.tsv"
    local name
    for name in "${ENUMERATORS[@]}"; do
        if run_enumerator "$name" "$WORK/one.tsv"; then
            cat "$WORK/one.tsv" >>"$WORK/records.tsv"
        fi
    done
    sort -u -o "$WORK/records.tsv" "$WORK/records.tsv"
    cut -f1,2 "$WORK/records.tsv" | sort -u >"$WORK/native.keys"

    if run_enumerator assets "$WORK/assets.tsv"; then
        normalise_assets <"$WORK/assets.tsv" >>"$WORK/records.tsv"
    fi
    sort -u -o "$WORK/records.tsv" "$WORK/records.tsv"
}

as_json() {
    jq -Rn \
        --arg project "$PROJECT" \
        --rawfile records "$WORK/records.tsv" \
        --rawfile errors "$WORK/errors.txt" \
        --rawfile unobservable "$WORK/unobservable.txt" \
        '{
            project_id: $project,
            resources: ($records | split("\n") | map(select(length > 0)) | map(
                split("\t") | {kind: .[0], id: .[1], state: .[2]}
            )),
            enumerator_errors: ($errors | split("\n") | map(select(length > 0))),
            unobservable: ($unobservable | split("\n") | map(select(length > 0)))
        }'
}

keys_of() { jq -r '.resources[] | "\(.kind)\t\(.id)"' "$1" | sort -u; }

# Refuses an inventory captured against a different project. Fatal rather than a
# warning: the comparison it feeds decides whether NFR-6 passes.
_inventory_project() {
    local file=$1 label=$2 recorded
    recorded=$(jq -r '.project_id // empty' "$file") || die "cannot read $label"
    [ -n "$recorded" ] || die "$label records no project_id"
    [ "$recorded" = "$PROJECT" ] ||
        die "$label was captured against project '$recorded', not '$PROJECT'"
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_capture() {
    collect
    local json
    json=$(as_json)
    if [ -n "$OUT" ]; then
        printf '%s\n' "$json" >"$OUT"
        printf 'captured %s resources to %s\n' \
            "$(jq '.resources | length' <<<"$json")" "$OUT"
    else
        printf '%s\n' "$json"
    fi
    local unobservable failures
    unobservable=$(jq -r '.unobservable[]' <<<"$json")
    if [ -n "$unobservable" ]; then
        printf '\nSurfaces whose API is not enabled, so not observed:\n%s\n' "$unobservable" >&2
        printf 'Expected on a baseline. A check will treat anything found on these\n' >&2
        printf 'surfaces later as in scope, which is the safe direction.\n' >&2
    fi
    local dropped
    dropped=$(wc -l <"$WORK/stale.tsv" | tr -d ' ')
    if [ "$dropped" -gt 0 ]; then
        printf '\nAsset index records dropped as stale, contradicted by the API\n' >&2
        printf 'that owns them:\n' >&2
        sed 's/^/  /' "$WORK/stale.tsv" >&2
    fi
    failures=$(jq -r '.enumerator_errors[]' <<<"$json")
    if [ -n "$failures" ]; then
        printf '\nWARNING: enumerators that could not answer:\n%s\n' "$failures" >&2
        printf 'This capture is incomplete. Fix the cause and capture again.\n' >&2
        return 1
    fi
}

cmd_check() {
    [ -n "$BEFORE" ] || die "check needs --before"
    [ -f "$BEFORE" ] || die "no such file: $BEFORE"

    # Before enumerating anything: an inventory captured against a different
    # project would exempt this one's resources. Most ids are project-scoped and
    # would simply fail to match, but `enabled_service`, `wif_pool` and
    # `firebase_ruleset` are keyed on names that are not — so a second deployer
    # who skipped the capture step and inherited the committed baseline would
    # have those silently cleared, on the file whose whole purpose is to decide
    # what counts.
    _inventory_project "$BEFORE" "$BEFORE"
    if [ -n "$BASELINE" ] && [ -s "$BASELINE" ]; then
        _inventory_project "$BASELINE" "$BASELINE"
    fi

    collect
    as_json >"$WORK/after.json"

    keys_of "$BEFORE" >"$WORK/before.keys"
    if [ -n "$BASELINE" ] && [ -s "$BASELINE" ]; then
        keys_of "$BASELINE" >"$WORK/baseline.keys"
    else
        : >"$WORK/baseline.keys"
    fi

    # In scope: everything that appeared between baseline and before. That set is
    # what the destroy under test was responsible for.
    comm -23 "$WORK/before.keys" "$WORK/baseline.keys" >"$WORK/in_scope.keys"

    jq -r '.resources[] | "\(.kind)\t\(.id)\t\(.state)"' "$WORK/after.json" |
        sort -u >"$WORK/after.tsv"
    cut -f1,2 "$WORK/after.tsv" | sort -u >"$WORK/after.keys"

    comm -12 "$WORK/in_scope.keys" "$WORK/after.keys" >"$WORK/survivors.keys"

    : >"$WORK/orphans.tsv"
    : >"$WORK/residue.tsv"
    while IFS=$'\t' read -r kind id; do
        [ -n "$kind" ] || continue
        local state reason
        state=$(awk -F'\t' -v k="$kind" -v i="$id" '$1==k && $2==i {print $3; exit}' "$WORK/after.tsv")
        reason=$(classify "$kind" "$id" "$state")
        if [ -n "$reason" ]; then
            printf '%s\t%s\t%s\n' "$kind" "$id" "$reason" >>"$WORK/residue.tsv"
        elif _asset_gone "$kind" "$id"; then
            printf '%s\t%s\t%s\n' "$kind" "$id" \
                "asset index record; the owning API returns 404" >>"$WORK/stale.tsv"
        else
            printf '%s\t%s\t%s\n' "$kind" "$id" "$state" >>"$WORK/orphans.tsv"
        fi
    done <"$WORK/survivors.keys"

    # Anything live now that was in neither inventory. Usually the capture was
    # taken too early; occasionally it is something a destroyed resource created
    # on its way out.
    #
    # Classified on the same rules as a survivor. An `appeared` resource that is
    # by-design residue is residue whichever inventory it is missing from, and
    # the alternative is a check that fails on a build-cache layer written
    # between two captures — which is what run 32294015789 and the local run
    # after it both did.
    comm -23 "$WORK/after.keys" <(sort -u "$WORK/before.keys" "$WORK/baseline.keys") \
        >"$WORK/appeared.all"
    : >"$WORK/appeared.keys"
    while IFS=$'\t' read -r kind id; do
        [ -n "$kind" ] || continue
        local astate areason
        astate=$(awk -F'\t' -v k="$kind" -v i="$id" '$1==k && $2==i {print $3; exit}' "$WORK/after.tsv")
        areason=$(classify "$kind" "$id" "$astate")
        if [ -n "$areason" ]; then
            printf '%s\t%s\t%s\n' "$kind" "$id" "$areason" >>"$WORK/residue.tsv"
        else
            printf '%s\t%s\n' "$kind" "$id" >>"$WORK/appeared.keys"
        fi
    done <"$WORK/appeared.all"

    report
}

# An asset type with no enumerator of its own cannot be contradicted by one, so
# it is asked directly before it is counted. `search-all-resources` returned
# resources deleted weeks earlier, and an orphan nobody re-checked is how a
# report of 190 gets ignored.
#
# Only 404 clears a record. A 403 is what a caller without the right role gets
# for a resource that exists *and* for one that does not, and a host with no
# entry below yields no URL at all — both stay orphans, because the direction to
# fail in is the one that costs a re-run rather than a bill.
PROBE_TOKEN=""

_asset_probe_url() {
    local name=${1#//} host path
    host=${name%%/*}
    path=${name#*/}
    case "$host" in
        # Non-uniform prefixes. compute puts its version after a service
        # segment; the rest take the version first.
        compute.googleapis.com) printf 'https://%s/compute/v1/%s\n' "$host" "$path" ;;
        apikeys.googleapis.com | identitytoolkit.googleapis.com) printf 'https://%s/v2/%s\n' "$host" "$path" ;;
        logging.googleapis.com) printf 'https://%s/v2/%s\n' "$host" "$path" ;;
        cloudresourcemanager.googleapis.com) printf 'https://%s/v3/%s\n' "$host" "$path" ;;
        firebase.googleapis.com) printf 'https://%s/v1beta1/%s\n' "$host" "$path" ;;
        artifactregistry.googleapis.com | dataplex.googleapis.com | secretmanager.googleapis.com | \
            firebaserules.googleapis.com | iam.googleapis.com | cloudbilling.googleapis.com | \
            pubsub.googleapis.com | eventarc.googleapis.com | run.googleapis.com)
            printf 'https://%s/v1/%s\n' "$host" "$path" ;;
        *) printf '\n' ;;
    esac
}

_asset_gone() {
    local kind=$1 id=$2 url code
    case "$kind" in
        asset:*) ;;
        *) return 1 ;;
    esac
    url=$(_asset_probe_url "$id")
    [ -n "$url" ] || return 1

    if [ -z "$PROBE_TOKEN" ]; then
        PROBE_TOKEN=$(gcloud auth print-access-token 2>/dev/null) || return 1
        [ -n "$PROBE_TOKEN" ] || return 1
    fi
    code=$(curl -sS -o /dev/null -w '%{http_code}' \
        -H "Authorization: Bearer $PROBE_TOKEN" \
        -H "x-goog-user-project: $PROJECT" \
        "$url" 2>/dev/null) || return 1
    [ "$code" = "404" ]
}

# Empty output means orphan. Anything else is the reason it is allowed to
# survive, and that reason is printed rather than filtered silently — an
# exemption nobody reads is how a carve-out becomes a leak.
classify() {
    local kind=$1 id=$2 state=$3
    case "$state" in
        deleted)
            echo "soft-deleted; the ID stays reserved for 30 days (spike 0.1 finding 6)"
            return
            ;;
    esac
    case "$kind" in
        enabled_service)
            echo "APIs are left enabled on destroy by design (infra/bootstrap/README.md)"
            return
            ;;
        iam_binding)
            case "$id" in
                *deleted:*)
                    echo "tombstoned member; the binding's principal no longer exists"
                    return
                    ;;
            esac
            # Google grants these to itself when an API is first used, and takes
            # them back only when the API is disabled. The APIs above survive a
            # destroy by design, so calling the API residue and the service
            # agent it created an orphan is the same carve-out reported two
            # ways. Terraform never created these and cannot remove them.
            #
            # Keyed on the member, which is safe only while nothing in `infra/`
            # grants a role to a Google-managed principal — checked, and true.
            # Grant one and this rule starts hiding a binding that is ours.
            if _google_managed_principal "${id#* }"; then
                echo "Google-managed service agent; created with the API, removed only with it"
                return
            fi
            ;;
        service_account)
            case "$id" in
                *@appspot.gserviceaccount.com)
                    echo "Google-created default service account; not provisioned by Terraform"
                    return
                    ;;
            esac
            ;;
        asset:logging.googleapis.com/RecentQuery)
            # A query someone ran in Logs Explorer. Cloud Logging keeps a
            # per-query record with an ID generated at the moment of the query,
            # so this is the DockerImage-digest problem in a second guise: a
            # baseline can only ever bless the query IDs that existed when it
            # was captured, and the next person to open Logs Explorer — during
            # the run, while reading a transcript, or afterwards — creates one
            # the baseline has never seen.
            #
            # Exempted by *kind*, because there is no stable identifier to key
            # on and no narrower rule to write. That is safe on the test this
            # file applies elsewhere: nothing in `infra/` creates a recent
            # query, and no `terraform destroy` can remove one, so its presence
            # can never be evidence of a destroy that failed. Reporting it as
            # an orphan would fail NFR-6 because somebody looked at the logs.
            echo "Cloud Logging recent-query record; created by viewing logs, owned by no apply"
            return
            ;;
        asset:artifactregistry.googleapis.com/DockerImage | asset:artifactregistry.googleapis.com/Repository)
            # The other half of the Cloud Functions gen2 build, and the same
            # carve-out as the staging bucket below. Deploying a gen2 function
            # builds a container and pushes it, plus a `/cache` layer, into a
            # `gcf-artifacts` repository the service creates and no apply owns.
            #
            # Exempted by *repository* rather than by digest, which is the whole
            # point: every rebuild produces new digests, so a baseline can only
            # ever bless the digests that existed when it was captured. The
            # current baseline holds two, both since superseded — and the
            # teardown after each guard deploy would otherwise report the new
            # ones forever, as `orphans` if the inventory saw them and as
            # `appeared` if the index surfaced them mid-run.
            #
            # Keyed on the repository name Google chooses, not on ours, so this
            # stays free of deployment-specific values (POR-6).
            case "$id" in
                */repositories/gcf-artifacts | */repositories/gcf-artifacts/*)
                    echo "Cloud Functions gen2 build artefact; service-created, no apply owns it"
                    return
                    ;;
            esac
            # The arena's own image repository, and the images in it, survive a
            # destroy **by design**. `infra/bootstrap/registry.tf` owns the
            # repository precisely so it outlives the estate and the site:
            # `infra/hosting` needs a digest to exist before it can be applied
            # at all, so an image destroyed with the site could never be
            # redeployed without a rebuild, and NFR-6's teardown would be
            # destroying something it has no business touching.
            #
            # Every push adds a digest the baseline has never seen, so without
            # this every teardown after a deploy reports the site's own image
            # as an orphan — which is what the first real teardown did.
            #
            # The repository name is passed in rather than written here: it is
            # a deployment value (POR-6), unlike `gcf-artifacts`, which is
            # Google's choice and the same everywhere.
            if [ -n "${REGISTRY_REPOSITORY:-}" ]; then
                case "$id" in
                    */repositories/"${REGISTRY_REPOSITORY##*/}" | \
                        */repositories/"${REGISTRY_REPOSITORY##*/}"/*)
                        echo "arena image repository; survives a destroy by design (bootstrap owns it)"
                        return
                        ;;
                esac
            fi
            ;;
        asset:apikeys.googleapis.com/Key)
            # Enabling Identity Platform brings a Firebase project, and Firebase
            # auto-creates a browser API key. No apply owns it, no destroy
            # removes it, and its id is a UUID that changes when Firebase
            # recreates it — so it is TD-29's shape as well: a baseline can only
            # ever bless the id that existed when it was captured.
            #
            # Keyed on the display name Google writes, not on the id and not
            # on the kind. Exempting the whole kind is what must not happen
            # here: two distinct keys exist in a deployed project and each is
            # exempt for its own reason, so a third one — created by hand, or
            # by a module that should not have — has to keep being reported.
            display=$(gcloud services api-keys describe "${id##*/keys/}" \
                --project="$PROJECT" --format='value(displayName)' 2>/dev/null || true)
            case "$display" in
                *"auto created by Firebase"*)
                    echo "Firebase-created browser key; no apply owns it, and its id regenerates"
                    return
                    ;;
            esac
            # The arena's own browser key survives a destroy **by design**, and
            # this comment used to say the opposite. It was created by
            # `infra/hosting` — so a key still present after a teardown was a
            # real leak, and exempting the kind would have hidden it.
            #
            # ADR 0012 moved it to `infra/bootstrap`, because destroying an API
            # key locks its id for 30 days and hosting is destroyed every cycle.
            # It is now the same shape as the image repository above: a
            # bootstrap resource the baseline blesses on a fresh project, and
            # reports as an orphan on any project whose baseline was captured
            # before it existed — which is every project deployed before the
            # move.
            #
            # Keyed on the display name our own resource sets, not on the key
            # id. The id is `<service-name>-identity-platform`, so matching it
            # would need the service name passed in — a deployment value
            # (POR-6) whose default would then be written in three places and
            # able to disagree with itself. The display name is ours, is not
            # deployment-specific, and is the same thing the Firebase carve-out
            # above keys on.
            case "$display" in
                *"public by design"*)
                    echo "arena browser key; survives a destroy by design (bootstrap owns it, ADR 0012)"
                    return
                    ;;
            esac
            ;;
        storage_bucket)
            # Cloud Functions gen2 creates its own build staging bucket and
            # names it after the project number. It cannot be redirected at the
            # Terraform-managed source bucket, so no apply owns it and no
            # destroy removes it. Exempt only while it holds nothing: `empty`
            # here counts soft-deleted objects, which are retained and billed
            # for seven days after the function goes.
            case "$id:$state" in
                gcf-v2-sources-*:empty)
                    echo "Cloud Functions gen2 staging bucket, service-created and verified empty"
                    return
                    ;;
            esac
            ;;
    esac
    echo ""
}

# Google's own principals, by shape rather than by a list of product domains.
# Per-project service agents are `service-<project number>@` on a domain Google
# owns — `gcp-sa-*`, but also `gcf-admin-robot`, `compute-system` and others,
# and enumerating them is a list that goes stale silently. What they all share
# is the `service-` prefix on a domain that is not this project's, and our own
# accounts are always `name@$PROJECT.iam.gserviceaccount.com`. Two older
# per-product accounts predate the convention and are named outright.
#
# The one thing this would misread is a service account borrowed from another
# project, which nothing in `infra/` does.
_google_managed_principal() {
    case "$1" in
        serviceAccount:*@"$PROJECT".iam.gserviceaccount.com) return 1 ;;
        serviceAccount:service-*@*.iam.gserviceaccount.com) return 0 ;;
        serviceAccount:*@cloudbuild.gserviceaccount.com) return 0 ;;
        serviceAccount:*@appspot.gserviceaccount.com) return 0 ;;
    esac
    return 1
}

report() {
    local orphans residue appeared errors blind stale
    orphans=$(wc -l <"$WORK/orphans.tsv" | tr -d ' ')
    residue=$(wc -l <"$WORK/residue.tsv" | tr -d ' ')
    stale=$(wc -l <"$WORK/stale.tsv" | tr -d ' ')
    appeared=$(wc -l <"$WORK/appeared.keys" | tr -d ' ')
    errors=$(jq -r '.enumerator_errors | length' "$WORK/after.json")
    # Counted with the errors, not with the residue. `disable_on_destroy = false`
    # means a destroyed estate leaves its APIs on, so a surface that cannot be
    # read during a check is an unanswered question about that surface.
    blind=$(jq -r '.unobservable | length' "$WORK/after.json")

    printf 'teardown check — project %s\n\n' "$PROJECT"
    printf '  in scope   %s resources created after the baseline\n' \
        "$(wc -l <"$WORK/in_scope.keys" | tr -d ' ')"
    printf '  orphans    %s\n' "$orphans"
    printf '  residue    %s (expected, listed below)\n' "$residue"
    printf '  stale      %s asset index records the owning API contradicts\n' "$stale"
    printf '  appeared   %s (in neither inventory)\n' "$appeared"
    printf '  unread     %s surfaces whose API is not enabled\n' "$blind"
    printf '  errors     %s enumerators could not answer\n\n' "$errors"

    if [ "$orphans" -gt 0 ]; then
        printf 'ORPHANED — destroy did not remove these:\n'
        sed 's/^/  /' "$WORK/orphans.tsv"
        printf '\n'
    fi
    if [ "$appeared" -gt 0 ]; then
        printf 'APPEARED — live, and in neither inventory:\n'
        sed 's/^/  /' "$WORK/appeared.keys"
        printf '\n'
    fi
    if [ "$residue" -gt 0 ]; then
        printf 'Residue, surviving by design:\n'
        sed 's/^/  /' "$WORK/residue.tsv"
        printf '\n'
    fi
    # Printed, not silently discarded. These are the records that would
    # otherwise be orphans, and the reason each was cleared has to be as
    # readable as the carve-outs in `classify`.
    if [ "$stale" -gt 0 ]; then
        printf 'Asset index records contradicted by the API that owns them:\n'
        sed 's/^/  /' "$WORK/stale.tsv"
        printf '\n'
    fi
    if [ "$blind" -gt 0 ]; then
        printf 'Surfaces not read, so not cleared:\n'
        jq -r '.unobservable[] | "  " + .' "$WORK/after.json"
        printf '\n'
    fi
    if [ "$errors" -gt 0 ]; then
        printf 'Enumerators that could not answer:\n'
        jq -r '.enumerator_errors[] | "  " + .' "$WORK/after.json"
        printf '\n'
    fi

    if [ -n "$OUT" ]; then
        jq -n \
            --slurpfile after "$WORK/after.json" \
            --rawfile orphans "$WORK/orphans.tsv" \
            --rawfile residue "$WORK/residue.tsv" \
            --rawfile appeared "$WORK/appeared.keys" \
            --rawfile stale "$WORK/stale.tsv" \
            --arg before "$BEFORE" --arg baseline "${BASELINE:-}" \
            '{
                project_id: $after[0].project_id,
                before: $before,
                baseline: $baseline,
                orphans:  ($orphans  | split("\n") | map(select(length>0)) | map(split("\t") | {kind:.[0], id:.[1], state:.[2]})),
                residue:  ($residue  | split("\n") | map(select(length>0)) | map(split("\t") | {kind:.[0], id:.[1], reason:.[2]})),
                appeared: ($appeared | split("\n") | map(select(length>0)) | map(split("\t") | {kind:.[0], id:.[1]})),
                stale:    ($stale    | split("\n") | map(select(length>0)) | map(split("\t") | {kind:.[0], id:.[1], reason:.[2]})),
                enumerator_errors: $after[0].enumerator_errors,
                unobservable: $after[0].unobservable,
                verdict: (if ($orphans|split("\n")|map(select(length>0))|length) > 0
                             or ($appeared|split("\n")|map(select(length>0))|length) > 0
                             or ($after[0].enumerator_errors|length) > 0
                             or ($after[0].unobservable|length) > 0
                          then "failed" else "clean" end)
            }' >"$OUT"
        printf 'evidence written to %s\n' "$OUT"
    fi

    if [ "$orphans" -gt 0 ] || [ "$appeared" -gt 0 ] || [ "$errors" -gt 0 ] || [ "$blind" -gt 0 ]; then
        printf 'NFR-6 NOT verified.\n'
        return 1
    fi
    printf 'NFR-6 verified: destroy returned the project to its baseline.\n'
}

# ---------------------------------------------------------------------------

COMMAND=${1:-}
[ -n "$COMMAND" ] || { usage; exit 2; }
shift || true
case "$COMMAND" in
    capture | check) ;;
    -h | --help | help) usage; exit 0 ;;
    *) die "unknown command: $COMMAND" ;;
esac

PROJECT=""
BEFORE=""
BASELINE=""
OUT=""
REGISTRY_REPOSITORY="${REGISTRY_REPOSITORY:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --project-id) PROJECT=${2:-}; shift 2 ;;
        --before) BEFORE=${2:-}; shift 2 ;;
        --baseline) BASELINE=${2:-}; shift 2 ;;
        --out) OUT=${2:-}; shift 2 ;;
        --registry-repository) REGISTRY_REPOSITORY=${2:-}; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

[ -n "$PROJECT" ] || die "--project-id is required"
[ -z "$OUT" ] || mkdir -p "$(dirname "$OUT")" || die "cannot create $(dirname "$OUT")"
for tool in gcloud bq jq curl; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is not on PATH"
done

"cmd_$COMMAND"
