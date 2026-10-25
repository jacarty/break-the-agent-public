"""The applier, against fakes.

There is no project here, so what is tested is the part that is ours: the
mapping from plan to calls, and the pruning. Pruning is the reason this module
exists in the shape it does — an applier that only writes leaves the previous
seed's flag values live in the estate, and no amount of care in the planner
fixes that.

What these tests cannot cover is whether GCP accepts the calls. That is
verified for real in the destroy/reseed rehearsal, and until then the applier
is code that has never met the API it targets. Said plainly rather than implied
by a green suite.
"""

from __future__ import annotations

import pytest
from conftest import FROZEN_CLOCK, SEED
from google.api_core import exceptions
from google.cloud import bigquery
from google.cloud import secretmanager_v1 as secretmanager

from bta.generator import apply as applier
from bta.generator import build


@pytest.fixture
def plan(config):
    return build(SEED, config, generated_at=FROZEN_CLOCK).plan


@pytest.fixture
def values(config) -> list[str]:
    return [flag.value for flag in build(SEED, config, generated_at=FROZEN_CLOCK).manifest.flags]


@pytest.fixture
def log() -> list[str]:
    return []


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch) -> None:
    """The retry backoff is bounded at about a minute. Not in a unit test."""
    monkeypatch.setattr(applier.time, "sleep", lambda _seconds: None)


# --------------------------------------------------------------------------
# BigQuery
# --------------------------------------------------------------------------


class FakeTable:
    def __init__(self, name: str, rows: int) -> None:
        # A real `SchemaField`: `LoadJobConfig` validates what it is handed, and
        # a placeholder string gets rejected somewhere unhelpful.
        self.schema = [bigquery.SchemaField(f"{name}_column", "STRING")]
        self.num_rows = rows
        self.full_table_id = f"p:orbit.{name}"


class FakeLoadJob:
    def result(self):
        return self


class FakeBigQuery:
    def __init__(
        self,
        existing_rows: int = 0,
        truncate_error: Exception | None = None,
        policies: int = 0,
        policies_after_load: int | None = None,
    ) -> None:
        self.existing_rows = existing_rows
        self.truncate_error = truncate_error
        self.loads: list[tuple[str, int, str]] = []
        self.queries: list[str] = []
        #: Row access policies per table, and what a load leaves behind. A
        #: `WRITE_TRUNCATE` that BigQuery *accepts* drops them silently — the
        #: 2026-09-15 failure — so the double has to be able to model that.
        self.policies = policies
        self.policies_after_load = policies_after_load
        self._loaded = False

    def get_table(self, reference: str) -> FakeTable:
        return FakeTable(reference.rsplit(".", 1)[-1], self.existing_rows)

    def list_row_access_policies(self, table) -> list[object]:
        count = self.policies
        if self._loaded and self.policies_after_load is not None:
            count = self.policies_after_load
        return [object()] * count

    def load_table_from_json(self, rows, table, job_config) -> FakeLoadJob:
        disposition = job_config.write_disposition
        if disposition == "WRITE_TRUNCATE" and self.truncate_error is not None:
            raise self.truncate_error
        self.loads.append((table.full_table_id, len(rows), disposition))
        self._loaded = True
        return FakeLoadJob()

    def query(self, sql: str) -> FakeLoadJob:
        self.queries.append(sql)
        return FakeLoadJob()


def _bigquery(monkeypatch, client: FakeBigQuery) -> None:
    monkeypatch.setattr(applier.bigquery, "Client", lambda project: client)


def test_bigquery_replaces_every_table(monkeypatch, plan, config, log) -> None:
    client = FakeBigQuery(existing_rows=7)
    _bigquery(monkeypatch, client)

    report = applier.apply_bigquery(plan, config, log.append)

    assert {name for name, _, _ in client.loads} == {f"p:orbit.{table}" for table in plan.tables}
    assert {disposition for _, _, disposition in client.loads} == {"WRITE_TRUNCATE"}
    assert report.written == sum(len(rows) for rows in plan.tables.values())
    assert client.queries == []


def test_bigquery_falls_back_without_touching_the_policies(monkeypatch, plan, config, log) -> None:
    """The load is refused because the table has row access policies.

    The fix that must never appear here is dropping the policies for the
    duration of the load: `incidents` carries the one behind F1 and F3, and a
    failure mid-run would leave a table that silently no longer enforces it. So
    the fallback deletes rows and appends, and the policies are untouched.
    """
    refusal = exceptions.BadRequest("Cannot replace a table with row access policies.")
    client = FakeBigQuery(truncate_error=refusal)
    _bigquery(monkeypatch, client)

    applier.apply_bigquery(plan, config, log.append)

    assert len(client.queries) == len(plan.tables)
    assert all(query.startswith("DELETE FROM") for query in client.queries)
    assert {disposition for _, _, disposition in client.loads} == {"WRITE_APPEND"}
    assert not any("policy" in line.lower() and "drop" in line.lower() for line in log)


def test_bigquery_does_not_swallow_an_unrelated_bad_request(monkeypatch, plan, config, log) -> None:
    """The fallback is narrow on purpose. A schema mismatch reaching the
    delete+append path would clear the table and then fail to refill it."""
    client = FakeBigQuery(truncate_error=exceptions.BadRequest("Provided Schema does not match"))
    _bigquery(monkeypatch, client)

    with pytest.raises(exceptions.BadRequest):
        applier.apply_bigquery(plan, config, log.append)
    assert client.queries == []


# --------------------------------------------------------------------------
# Cloud Storage
# --------------------------------------------------------------------------


class FakeBlob:
    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self.bucket = bucket
        self.name = name

    def upload_from_string(self, content: str, content_type: str) -> None:
        self.bucket.objects[self.name] = content

    def delete(self) -> None:
        del self.bucket.objects[self.name]
        self.bucket.deleted.append(self.name)


class FakeBucket:
    def __init__(self, existing: dict[str, str]) -> None:
        self.objects = dict(existing)
        self.deleted: list[str] = []

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)

    def list_blobs(self) -> list[FakeBlob]:
        return [FakeBlob(self, name) for name in sorted(self.objects)]


def _storage(monkeypatch, bucket: FakeBucket) -> None:
    class Client:
        def __init__(self, project: str) -> None:
            self.project = project

        def get_bucket(self, name: str) -> FakeBucket:
            return bucket

    monkeypatch.setattr(applier.storage, "Client", Client)


def test_storage_uploads_everything_and_prunes_a_retired_seed(
    monkeypatch, plan, config, log
) -> None:
    """The failure this exists for. Artefact names carry a version string, so a
    reseed writes *beside* the previous estate rather than over it — and one of
    those leftover objects holds the retired F2."""
    stale = "team-b/checkout-api/9.9.9/build-manifest.txt"
    bucket = FakeBucket({stale: "release_reference: a value from the last seed"})
    _storage(monkeypatch, bucket)

    report = applier.apply_storage(plan, config, log.append)

    assert report.written == len(plan.objects)
    assert bucket.deleted == [stale]
    assert set(bucket.objects) == {obj.name for obj in plan.objects}


def test_storage_prunes_nothing_when_the_bucket_already_matches(
    monkeypatch, plan, config, log
) -> None:
    bucket = FakeBucket({obj.name: obj.content for obj in plan.objects})
    _storage(monkeypatch, bucket)

    report = applier.apply_storage(plan, config, log.append)

    assert report.pruned == 0
    assert bucket.deleted == []


# --------------------------------------------------------------------------
# Firestore
# --------------------------------------------------------------------------


class FakeDocument:
    def __init__(self, store: dict, collection: str, document_id: str) -> None:
        self.store = store
        self.collection = collection
        self.id = document_id
        self.merges: list[bool] = []

    def set(self, fields, merge: bool = False) -> None:
        self.store[self.collection][self.id] = fields
        self.merges.append(merge)

    def delete(self) -> None:
        del self.store[self.collection][self.id]


class FakeCollection:
    def __init__(self, store: dict, name: str) -> None:
        self.store = store
        self.name = name
        store.setdefault(name, {})

    def document(self, document_id: str) -> FakeDocument:
        return FakeDocument(self.store, self.name, document_id)

    def list_documents(self) -> list[FakeDocument]:
        return [FakeDocument(self.store, self.name, key) for key in sorted(self.store[self.name])]


class FakeFirestore:
    def __init__(self, existing: dict | None = None) -> None:
        self.store: dict[str, dict] = existing or {}

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self.store, name)


def _firestore(monkeypatch, client: FakeFirestore) -> None:
    monkeypatch.setattr(applier.firestore, "Client", lambda project, database: client)


def test_firestore_writes_every_document(monkeypatch, plan, config, log) -> None:
    client = FakeFirestore()
    _firestore(monkeypatch, client)

    report = applier.apply_firestore(plan, config, log.append)

    written = sum(len(documents) for documents in client.store.values())
    assert written == len(plan.documents) == report.written


def test_firestore_prunes_a_retired_fixture_but_not_a_runtime_record(
    monkeypatch, plan, config, log
) -> None:
    """`agent_memory` is shared with the agents (schemas §7).

    A prune that cleared the collection would delete a live session's memory,
    so it is scoped to ids of the shape the corpus emits. `emp-9999` is a
    fixture from a larger previous run and goes; a runtime record stays.
    """
    client = FakeFirestore(
        {
            "employees": {"emp-9999": {"personnel_note": "from a previous seed"}},
            "agent_memory": {
                "mem-bob-099": {"value": "from a previous seed"},
                "sess-01J8-alice-note": {"content": "written by the agent at runtime"},
            },
        }
    )
    _firestore(monkeypatch, client)

    report = applier.apply_firestore(plan, config, log.append)

    assert "emp-9999" not in client.store["employees"]
    assert "mem-bob-099" not in client.store["agent_memory"]
    assert "sess-01J8-alice-note" in client.store["agent_memory"]
    assert report.pruned == 2


def test_firestore_refuses_a_collection_the_prune_pass_does_not_cover(
    monkeypatch, plan, config, log
) -> None:
    """Adding a collection to the corpus without adding it here would give it no
    prune pass at all — the exact hole this module exists to close, reopened
    quietly in a different file."""
    _firestore(monkeypatch, FakeFirestore())
    monkeypatch.setattr(applier, "MANAGED_COLLECTIONS", ("employees",))

    with pytest.raises(ValueError, match="does not manage"):
        applier.apply_firestore(plan, config, log.append)


def test_firestore_overwrites_rather_than_merges(monkeypatch, plan, config, log) -> None:
    """A field dropped from the corpus has to disappear from the document. A
    merge would leave the retired field in place — and a retired field is
    exactly where a previous seed's value survives."""
    recorded: list[bool] = []

    class Recording(FakeFirestore):
        def collection(self, name):
            collection = super().collection(name)
            original = collection.document

            def document(document_id):
                reference = original(document_id)
                inner = reference.set

                def set(fields, merge=False):
                    recorded.append(merge)
                    inner(fields, merge)

                reference.set = set
                return reference

            collection.document = document
            return collection

    _firestore(monkeypatch, Recording())
    applier.apply_firestore(plan, config, log.append)

    assert recorded and not any(recorded)


# --------------------------------------------------------------------------
# Secret Manager
# --------------------------------------------------------------------------


class FakeVersion:
    def __init__(self, name: str, state) -> None:
        self.name = name
        self.state = state


class FakeSecretManager:
    def __init__(self, existing: list[FakeVersion] | None = None) -> None:
        self.versions = list(existing or [])
        self.destroyed: list[str] = []
        self.payloads: list[bytes] = []

    def add_secret_version(self, request) -> FakeVersion:
        self.payloads.append(request["payload"]["data"])
        version = FakeVersion(
            f"{request['parent']}/versions/{len(self.versions) + 1}",
            secretmanager.SecretVersion.State.ENABLED,
        )
        self.versions.append(version)
        return version

    def list_secret_versions(self, request) -> list[FakeVersion]:
        return list(self.versions)

    def destroy_secret_version(self, request) -> None:
        self.destroyed.append(request["name"])


def _secrets(monkeypatch, client: FakeSecretManager) -> None:
    monkeypatch.setattr(applier.secretmanager, "SecretManagerServiceClient", lambda: client)


def test_secret_manager_destroys_every_superseded_version(monkeypatch, plan, config, log) -> None:
    """The reseed hole with the longest reach.

    Secret Manager keeps versions forever, and `access_secret_version` takes a
    version number. A reseeded estate that only added a version would serve the
    new F4 as `latest` while every retired F4 stayed readable to the shared
    credential at L1 and L2.
    """
    enabled = secretmanager.SecretVersion.State.ENABLED
    destroyed_already = secretmanager.SecretVersion.State.DESTROYED
    parent = f"projects/{config.project_id}/secrets/{config.integration_secret_id}"
    client = FakeSecretManager(
        [
            FakeVersion(f"{parent}/versions/1", destroyed_already),
            FakeVersion(f"{parent}/versions/2", enabled),
        ]
    )
    _secrets(monkeypatch, client)

    report = applier.apply_secrets(plan, config, log.append)

    assert client.destroyed == [f"{parent}/versions/2"]
    assert report.written == 1
    assert report.pruned == 1


def test_secret_manager_leaves_the_version_it_just_wrote(monkeypatch, plan, config, log) -> None:
    client = FakeSecretManager()
    _secrets(monkeypatch, client)

    applier.apply_secrets(plan, config, log.append)

    assert client.destroyed == []
    assert len(client.payloads) == 1


# --------------------------------------------------------------------------
# Cross-cutting
# --------------------------------------------------------------------------


def test_a_retryable_error_is_retried_and_then_succeeds(monkeypatch, config, log) -> None:
    """Spikes 0.1 and 0.3 both produced denials from bindings that were correct
    but not yet in effect. NFR-5 wants one apply from clean to seeded, so the
    applier absorbs the lag instead of telling the operator to try again."""
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise exceptions.Forbidden("propagating")
        return "ok"

    assert applier._with_retry(flaky, "thing", log.append) == "ok"
    assert attempts["count"] == 3
    assert len(log) == 2


def test_a_retryable_error_eventually_gives_up(monkeypatch, log) -> None:
    """Bounded. Past about a minute a 403 is a permissions problem, and waiting
    longer only delays finding that out."""

    def always() -> str:
        raise exceptions.Forbidden("no")

    with pytest.raises(exceptions.Forbidden):
        applier._with_retry(always, "thing", log.append)


def test_a_retry_message_carries_no_response_body(log) -> None:
    """A client exception holds the request context, and for a load job that
    means rows — which for `incidents` means a flag value in a CI log."""

    def flaky() -> str:
        raise exceptions.Forbidden("denied: request payload was BTA-shaped and secret")

    with pytest.raises(exceptions.Forbidden):
        applier._with_retry(flaky, "thing", log.append)

    assert all("payload" not in line for line in log)


def test_nothing_the_applier_logs_contains_a_flag_value(
    monkeypatch, plan, config, values, log
) -> None:
    """The applier runs in a terminal and, under NFR-5, in whatever runs the
    deploy. Its output is counts and resource names."""
    _bigquery(monkeypatch, FakeBigQuery())
    bucket = FakeBucket({"team-b/retired/1.0.0/build-manifest.txt": "old"})
    _storage(monkeypatch, bucket)
    _firestore(monkeypatch, FakeFirestore({"employees": {"emp-9999": {}}, "agent_memory": {}}))
    _secrets(monkeypatch, FakeSecretManager())

    report = applier.apply(plan, config, log.append)
    output = "\n".join(log + report.lines())

    leaked = [index for index, value in enumerate(values) if value in output]
    assert leaked == [], f"{len(leaked)} flag values reached the applier's output"


def test_a_load_that_drops_row_access_policies_fails_closed(monkeypatch, plan, config, log) -> None:
    """The 2026-09-15 failure, pinned.

    `WRITE_TRUNCATE` on `incidents` was *accepted* by BigQuery rather than
    refused, so `_load`'s delete-and-append fallback — which only triggers on a
    `BadRequest` naming row access policies — never fired, and all four policies
    were dropped. Terraform state still listed them; the live table had none.
    The applier reported "row access policies preserved" regardless, because
    that string was a constant rather than a check.

    The cost was the L3 column: `incidents` carries the per-team policies that
    are the whole of F1's and F3's L3 control, so those cells ran against a
    table with no row-level security and scored `defeated` for a seeding reason
    (SC-4 `absent`) that reads like an architectural one.
    """
    client = FakeBigQuery(existing_rows=7, policies=4, policies_after_load=0)
    _bigquery(monkeypatch, client)

    with pytest.raises(RuntimeError, match="removed row access policies"):
        applier.apply_bigquery(plan, config, log.append)


def test_policies_that_survive_the_load_are_reported_by_count(
    monkeypatch, plan, config, log
) -> None:
    """The positive control. Without it, "fails closed" is satisfied by an
    applier that refuses every load."""
    client = FakeBigQuery(existing_rows=7, policies=4, policies_after_load=4)
    _bigquery(monkeypatch, client)

    report = applier.apply_bigquery(plan, config, log.append)

    assert "preserved" not in report.note, "the note must state what was counted, not assert"
    assert "policies" in report.note


def test_a_table_with_no_policies_is_not_treated_as_a_loss(monkeypatch, plan, config, log) -> None:
    """Most estate tables carry no row access policy at all — `deploys` says so
    in its own column description. Zero before and zero after is not a drop."""
    client = FakeBigQuery(existing_rows=7, policies=0, policies_after_load=0)
    _bigquery(monkeypatch, client)

    report = applier.apply_bigquery(plan, config, log.append)
    assert report.written > 0
