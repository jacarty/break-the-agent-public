#!/usr/bin/env bash
#
# Can the CI deploy identity actually perform this apply?
#
# Local applies run as an operator holding `roles/owner`, where every form of a
# grant works. CI runs as `ci-deploy@`, which holds a deliberately narrow set of
# roles (`ci_deploy_roles` in infra/bootstrap/variables.tf). The two disagree
# exactly where it matters: a resource an operator can create locally and CI
# cannot is not visible until the dispatch fails.
#
# That happened on CI run 32292027232 — a `google_project_iam_custom_role` that
# applied cleanly in the SEC-3 rehearsal under owner, and failed in CI on
# `iam.roles.get`. This script is the check that would have caught it, and it
# needs no impersonation and no API enablement: it expands the roles CI holds
# and asks whether the union covers the permissions a change requires.
#
#   scripts/check-ci-authority.sh iam.roles.create resourcemanager.projects.setIamPolicy
#
# Exits non-zero if any named permission is not covered. Reads role definitions
# from the live API, so it reflects what the roles actually contain rather than
# what anyone remembers.

set -uo pipefail

PROGRAM=$(basename "$0")
[ $# -gt 0 ] || {
    echo "usage: $PROGRAM <permission> [permission ...]" >&2
    echo "  e.g. $PROGRAM iam.roles.create" >&2
    exit 2
}

PROJECT=${CI_AUTHORITY_PROJECT:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}}
ACCOUNT=${CI_DEPLOY_ACCOUNT:-ci-deploy@${PROJECT}.iam.gserviceaccount.com}
[ -n "$PROJECT" ] || { echo "$PROGRAM: no project; set CI_AUTHORITY_PROJECT" >&2; exit 2; }

# Read the bindings the project actually carries, not the Terraform variable.
#
# Parsing `ci_deploy_roles` out of variables.tf was the first attempt and it was
# wrong in the worst direction: the variable block also contains a `validation`
# listing the roles that are *refused* — owner, editor, serviceAccountKeyAdmin,
# workloadIdentityPoolAdmin. A naive parse swept those in, `roles/owner` expands
# to some thirteen thousand permissions, and the check reported every permission
# as granted. A check that cannot fail is worse than no check, because it is
# trusted. The live policy cannot drift from itself.
roles=$(gcloud projects get-iam-policy "$PROJECT" \
    --flatten="bindings[].members" \
    --filter="bindings.members:$ACCOUNT" \
    --format="value(bindings.role)" 2>/dev/null | sort -u)

[ -n "$roles" ] || {
    echo "$PROGRAM: no bindings found for $ACCOUNT on $PROJECT" >&2
    echo "$PROGRAM: (is the deploy identity applied? bootstrap creates it)" >&2
    exit 2
}

echo "$ACCOUNT holds $(wc -l <<<"$roles" | tr -d ' ') roles on $PROJECT. Expanding..."

granted=$(mktemp); trap 'rm -f "$granted"' EXIT
while read -r role; do
    [ -n "$role" ] || continue
    gcloud iam roles describe "$role" --project="$PROJECT" \
        --format="value(includedPermissions)" 2>/dev/null |
        tr ';' '\n'
    gcloud iam roles describe "$role" --format="value(includedPermissions)" 2>/dev/null |
        tr ';' '\n'
done <<<"$roles" | sed '/^$/d' | sort -u >"$granted"

echo "  $(wc -l <"$granted" | tr -d ' ') distinct permissions"
echo

status=0
for perm in "$@"; do
    if grep -qxF "$perm" "$granted"; then
        printf '  GRANTED  %s\n' "$perm"
    else
        printf '  MISSING  %s\n' "$perm"
        status=1
    fi
done

echo
if [ $status -ne 0 ]; then
    echo "CI cannot perform this apply as configured."
    echo "Either use a resource CI is already authorised for, or widen"
    echo "ci_deploy_roles deliberately — that is a change to CI's authority,"
    echo "a local bootstrap apply (TD-6), and it leaves no audit trail."
else
    echo "All required permissions are covered by ci_deploy_roles."
fi
exit $status
