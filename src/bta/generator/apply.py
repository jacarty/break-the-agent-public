"""Pushing an `EstatePlan` to GCP.

The other half of the split described in `plan.py`. This module **writes and
does not decide** — it takes a finished plan and makes the estate match it. No
value is minted here, no host row is chosen here, and nothing here reads the
seed. Every client library in the project is imported from this file and
nowhere else, which is what keeps the determinism tests runnable with no
project, no credentials and no network.

## Applying is a replace, not an insert

The obvious applier writes the plan and stops. That version is wrong, and
wrong in the way that matters most here: **it leaves the previous seed's flag
values live.**

Reseeding is a normal operation — a value reaches a transcript, or a run is
compromised, and the estate is regenerated with a new seed. If the applier only
writes, the old estate is still there underneath. Objects whose names carry a
version string land beside the new ones rather than over them, old secret
versions stay enabled, and documents from a larger previous run persist. An
agent then finds a value that no manifest describes; the verifier cannot score
it, and the containment story for the seed being retired is simply false.

So each surface is pruned to the plan before or as it is written, and the
counts are reported. What is pruned is scoped to what the generator itself
could have created — see `_GENERATED_DOCUMENT_ID`.

## What runs as whom

This is the provisioning path, not a persona. It authenticates with Application
Default Credentials and is expected to be an identity with write access to the
estate — `terraform apply` then this, per NFR-5.

Firestore writes therefore go through the **server SDK, which bypasses Security
Rules** (spike 0.3). That is correct here and is exactly the client path the L3
tools must *not* use: F5 and F7b are denials produced by rules, and rules do
not apply to this library. Phase 3's document-level authorisation goes through
the REST API with the caller's own token instead.

## Nothing here prints a value

The plan holds live flag values in every surface. Progress output is counts and
resource names only, and the exception paths are written to keep it that way —
a client library traceback that echoes a request body would put a payload in a
terminal and a CI log.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from google.api_core import exceptions
from google.cloud import bigquery, firestore, storage
from google.cloud import secretmanager_v1 as secretmanager

from .config import GeneratorConfig
from .plan import EstatePlan

# Collections the generator owns. Anything outside these two is left alone.
MANAGED_COLLECTIONS = ("employees", "agent_memory")

# Document ids this generator produces, and the only ones it will delete.
#
# `agent_memory` is not exclusively the generator's: at runtime the agents write
# their own records into it (schemas.md §7). Pruning the collection wholesale
# would delete a live session's memory, so pruning is limited to ids of the
# shape the corpus emits. A runtime record with a colliding id would be a bug in
# the memory store's id scheme, not something to defend against here.
_GENERATED_DOCUMENT_ID = re.compile(r"^(emp-\d{4}|mem-[a-z0-9-]+-\d{3})$")

# Bounded retry for propagation lag.
#
# Spike 0.1 finding 2 and spike 0.3 finding 3 both produced denials from
# bindings that were correct but not yet in effect. NFR-5 says one apply from
# clean to seeded with no manual intervention, so a generator run that follows
# `terraform apply` by seconds has to tolerate the lag rather than tell the
# operator to try again. Bounded at roughly a minute: past that, a 403 is a
# permissions problem and waiting longer only delays finding out.
_RETRY_ATTEMPTS = 6
_RETRY_INITIAL_SECONDS = 2.0
_RETRYABLE = (
    exceptions.Forbidden,
    exceptions.NotFound,
    exceptions.ServiceUnavailable,
    exceptions.TooManyRequests,
)


@dataclass(frozen=True, slots=True)
class SurfaceReport:
    surface: str
    written: int
    pruned: int
    note: str = ""

    def line(self) -> str:
        pruned = f", {self.pruned} pruned" if self.pruned else ""
        note = f"  ({self.note})" if self.note else ""
        return f"  {self.surface:<16} {self.written:>5} written{pruned}{note}"


@dataclass(frozen=True, slots=True)
class ApplyReport:
    surfaces: tuple[SurfaceReport, ...]

    def lines(self) -> list[str]:
        return [surface.line() for surface in self.surfaces]


def _with_retry[T](operation: Callable[[], T], what: str, log: Callable[[str], None]) -> T:
    delay = _RETRY_INITIAL_SECONDS
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return operation()
        except _RETRYABLE as error:
            if attempt == _RETRY_ATTEMPTS:
                raise
            # `type(error).__name__` rather than `error`: a client exception
            # carries the request context, and for a load job that means rows.
            log(f"    {what}: {type(error).__name__}, retrying in {delay:.0f}s")
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------
# BigQuery
# --------------------------------------------------------------------------


def apply_bigquery(
    plan: EstatePlan, config: GeneratorConfig, log: Callable[[str], None]
) -> SurfaceReport:
    """Replace the contents of each estate table.

    The schema is read from the table rather than restated here. Terraform owns
    it (`infra/estate/schemas/*.json`) and a second copy in Python would drift —
    `tests/test_placement.py` already checks the generated rows against those
    files, which is the right place for that comparison because it costs nothing
    and needs no project.
    """
    client = bigquery.Client(project=config.project_id)
    written = 0
    replaced = 0
    policies_before: dict[str, int] = {}

    for name in sorted(plan.tables):
        rows = plan.tables[name]
        reference = f"{config.project_id}.{config.dataset_id}.{name}"
        table = _with_retry(lambda ref=reference: client.get_table(ref), f"get_table {name}", log)

        policies_before[name] = _policy_count(client, table, name, log)
        before = table.num_rows or 0
        _load(client, table, rows, name, log)
        written += len(rows)
        replaced += before

    _assert_policies_survived(client, config, policies_before, log)

    return SurfaceReport(
        surface="bigquery",
        written=written,
        pruned=replaced,
        note=(
            "replaced in place; row access policies counted before and after "
            f"({sum(policies_before.values())} policies across "
            f"{len(policies_before)} tables)"
        ),
    )


def _policy_count(
    client: bigquery.Client, table: Any, name: str, log: Callable[[str], None]
) -> int:
    try:
        return len(list(client.list_row_access_policies(table)))
    except Exception as exc:  # pragma: no cover - surfaced, never swallowed
        log(f"    {name}: could not read row access policies ({type(exc).__name__})")
        raise


def _assert_policies_survived(
    client: bigquery.Client,
    config: GeneratorConfig,
    before: dict[str, int],
    log: Callable[[str], None],
) -> None:
    """Fail closed if a load removed row-level security.

    **This exists because the note it replaces was a lie that nobody checked.**
    `apply_bigquery` returned the fixed string "row access policies preserved"
    on both load paths, asserting a property no code had verified. On
    2026-09-15 a `WRITE_TRUNCATE` on `incidents` was *accepted* by BigQuery
    rather than refused, so `_load`'s careful delete-and-append fallback — which
    only triggers on a `BadRequest` naming row access policies — never fired,
    and all four policies were dropped. Terraform state still listed them;
    `bq ls --row_access_policies` returned none.

    The cost was the L3 column. `incidents` carries the per-team policies that
    are the whole of F1's and F3's L3 control, so those cells ran against a
    table with no row-level security and scored `defeated` — which reads as the
    project's headline finding and is in fact a seeding defect (SC-4 `absent`,
    not `insufficient`). `bigquery.tf`'s own warning describes the mirror image
    of this and calls it "the most expensive failure available in this module".

    An estate seeded without row-level security cannot produce valid L3
    evidence, so this raises rather than warns.
    """
    lost = []
    for name, count in sorted(before.items()):
        if count == 0:
            continue
        reference = f"{config.project_id}.{config.dataset_id}.{name}"
        table = _with_retry(lambda ref=reference: client.get_table(ref), f"recheck {name}", log)
        after = _policy_count(client, table, name, log)
        if after < count:
            lost.append(f"{name}: {count} before, {after} after")

    if lost:
        raise RuntimeError(
            "the load removed row access policies — "
            + "; ".join(lost)
            + ". Row-level security is the L3 control for BigQuery-backed flags "
            "(F1, F3), so an estate in this state produces L3 cells that read "
            "`defeated` for a seeding reason rather than an architectural one. "
            "Re-apply infra/estate to restore the policies, verify with "
            "`bq ls --row_access_policies`, and re-run the affected cells."
        )


def _load(
    client: bigquery.Client,
    table: bigquery.Table,
    rows: list[dict[str, Any]],
    name: str,
    log: Callable[[str], None],
) -> None:
    """`WRITE_TRUNCATE`, with a fallback that does not touch the policies.

    A load job replacing a table that carries row access policies is the case
    to be careful about: `incidents` has one, and it is the entire mechanism
    behind F1's and F3's L3 cells. If the truncate is refused, the tempting fix
    is to drop the policies for the duration of the load — which would leave a
    window where the estate has no row-level security and, if anything went
    wrong mid-run, a table that quietly no longer enforces it.

    So the fallback is a DML delete followed by an append. Slower and billed as
    a query, and it leaves the policies exactly where they are.
    """
    truncate = bigquery.LoadJobConfig(
        schema=table.schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    try:
        _with_retry(
            lambda: client.load_table_from_json(rows, table, job_config=truncate).result(),
            f"load {name}",
            log,
        )
        return
    except exceptions.BadRequest as error:
        if "row access polic" not in str(error).lower():
            raise
        log(f"    {name}: truncate refused by row-level security, falling back to delete+append")

    _with_retry(
        lambda: client.query(
            f"DELETE FROM `{table.full_table_id.replace(':', '.')}` WHERE TRUE"
        ).result(),
        f"clear {name}",
        log,
    )
    append = bigquery.LoadJobConfig(
        schema=table.schema,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    _with_retry(
        lambda: client.load_table_from_json(rows, table, job_config=append).result(),
        f"append {name}",
        log,
    )


# --------------------------------------------------------------------------
# Cloud Storage
# --------------------------------------------------------------------------


def apply_storage(
    plan: EstatePlan, config: GeneratorConfig, log: Callable[[str], None]
) -> SurfaceReport:
    """Upload every artefact, then delete anything else in the bucket.

    Prune order matters and it is delete-after-write on purpose: a failure
    between the two leaves an estate with extra objects, which is recoverable by
    running again. Delete-first leaves an estate missing F2.

    The bucket is the generator's alone — Terraform creates it empty and nothing
    else writes to it — so "anything else" is safe to read as "a previous seed".
    """
    client = storage.Client(project=config.project_id)
    bucket = _with_retry(lambda: client.get_bucket(config.artefact_bucket), "get_bucket", log)

    planned = {obj.name for obj in plan.objects}
    for obj in plan.objects:
        blob = bucket.blob(obj.name)
        _with_retry(
            lambda b=blob, o=obj: b.upload_from_string(o.content, content_type=o.content_type),
            "upload",
            log,
        )

    stale = [blob for blob in bucket.list_blobs() if blob.name not in planned]
    for blob in stale:
        blob.delete()
        # The name, never the content. A stale object may hold a retired flag.
        log(f"    pruned gs://{config.artefact_bucket}/{blob.name}")

    return SurfaceReport(surface="cloud_storage", written=len(plan.objects), pruned=len(stale))


# --------------------------------------------------------------------------
# Firestore
# --------------------------------------------------------------------------


def apply_firestore(
    plan: EstatePlan, config: GeneratorConfig, log: Callable[[str], None]
) -> SurfaceReport:
    """Write the documents, then prune generator-shaped ids that are no longer
    in the plan.

    Server SDK, so Security Rules do not apply — see the module docstring. This
    is the provisioning path and the only place in the project where bypassing
    the rules is the correct behaviour.
    """
    client = firestore.Client(project=config.project_id, database=config.firestore_database_id)

    planned: dict[str, set[str]] = {collection: set() for collection in MANAGED_COLLECTIONS}
    for document in plan.documents:
        if document.collection not in planned:
            raise ValueError(
                f"plan writes to {document.collection!r}, which the applier does not manage; "
                f"add it to MANAGED_COLLECTIONS or the prune pass will not cover it"
            )
        planned[document.collection].add(document.document_id)

    for document in plan.documents:
        reference = client.collection(document.collection).document(document.document_id)
        # `set` without merge: a field removed from the corpus has to disappear
        # from the document too. Merging would leave a retired field — and a
        # retired field is where a previous seed's value would survive.
        _with_retry(lambda ref=reference, d=document: ref.set(d.fields), "set document", log)

    pruned = 0
    for collection in MANAGED_COLLECTIONS:
        for snapshot in client.collection(collection).list_documents():
            if snapshot.id in planned[collection]:
                continue
            if not _GENERATED_DOCUMENT_ID.match(snapshot.id):
                continue
            snapshot.delete()
            log(f"    pruned {collection}/{snapshot.id}")
            pruned += 1

    return SurfaceReport(
        surface="firestore",
        written=len(plan.documents),
        pruned=pruned,
        note="server SDK; Security Rules deliberately bypassed",
    )


# --------------------------------------------------------------------------
# Secret Manager
# --------------------------------------------------------------------------


def apply_secrets(
    plan: EstatePlan, config: GeneratorConfig, log: Callable[[str], None]
) -> SurfaceReport:
    """Add the new version, then destroy every earlier one.

    Destroying rather than disabling, and it is the whole reason this function
    is longer than an `add_secret_version` call. Secret Manager keeps versions
    forever by default, so a reseeded estate would serve the new F4 as `latest`
    while every previous flag value stayed retrievable by number. `alice` cannot
    read the secret at L3, but at L1 and L2 the shared credential can, and
    `access_secret_version` takes a version.

    Destroy is irreversible. That is the correct trade for a fixture whose
    entire purpose is to be a value nobody should still hold.
    """
    client = secretmanager.SecretManagerServiceClient()
    written = 0
    destroyed = 0

    for secret in plan.secrets:
        parent = f"projects/{config.project_id}/secrets/{secret.secret_id}"
        created = _with_retry(
            lambda p=parent, s=secret: client.add_secret_version(
                request={"parent": p, "payload": {"data": s.payload.encode("utf-8")}}
            ),
            f"add version to {secret.secret_id}",
            log,
        )
        written += 1

        for version in _earlier_enabled_versions(client, parent, created.name):
            client.destroy_secret_version(request={"name": version})
            log(f"    destroyed superseded {secret.secret_id}/{version.rsplit('/', 1)[-1]}")
            destroyed += 1

    return SurfaceReport(
        surface="secret_manager",
        written=written,
        pruned=destroyed,
        note="superseded versions destroyed, not disabled",
    )


def _earlier_enabled_versions(
    client: secretmanager.SecretManagerServiceClient, parent: str, keep: str
) -> Iterable[str]:
    enabled = secretmanager.SecretVersion.State.ENABLED
    for version in client.list_secret_versions(request={"parent": parent}):
        if version.name != keep and version.state == enabled:
            yield version.name


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

SURFACES: tuple[Callable[..., SurfaceReport], ...] = (
    apply_bigquery,
    apply_storage,
    apply_firestore,
    apply_secrets,
)


def apply(
    plan: EstatePlan,
    config: GeneratorConfig,
    log: Callable[[str], None] = print,
) -> ApplyReport:
    """Make the estate match the plan.

    Surfaces are applied in a fixed order and each is independent — a failure
    part-way leaves the estate half-seeded, and the fix is to run again rather
    than to unpick anything. Every operation here is idempotent for that reason.
    """
    reports = []
    for surface in SURFACES:
        log(f"  applying {surface.__name__.removeprefix('apply_')}...")
        reports.append(surface(plan, config, log))
    return ApplyReport(surfaces=tuple(reports))
