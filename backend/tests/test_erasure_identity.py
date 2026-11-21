"""Synthetic, independently retained registry; no app hooks, S3 or real identifiers."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
import shutil
import os
import socket
import sqlite3
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.backup import erasure_identity as identity
from services.account_closure_policy import POLICY_VERSION
from test_erasure_preparation import preparation_db  # noqa: F401 — accepted two-table fixture

SCOPE = "synthetic_identity"
GENERATION = "90b914d6-43a9-4989-a434-29723087003b"
REGISTRY = "639e2e4d-c3a8-4906-b5fd-97738db87618"
EPOCH = "2026-10-22T12:00:00.000000Z"
NON_V4 = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"  # Fixed example, never a host MAC/timestamp.


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    # The accepted in-memory fixture creates the event loop. Windows implements
    # its self-pipe using a local socketpair handshake. Initialize that fixture
    # first, then keep the full socket ban active throughout the actual test.
    if "preparation_db" in request.fixturenames:
        request.getfixturevalue("preparation_db")
    def deny(*args, **kwargs):
        raise AssertionError("identity proof forbids external IO")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)


def registry(path, **overrides):
    options = dict(scope=SCOPE, generation=GENERATION, registry_id=REGISTRY)
    options.update(overrides)
    return identity.IdentityRegistry(path, **options)


def candidate(**changes):
    result = dict(account_id=str(uuid4()), draft_id=str(uuid4()), closure_epoch=EPOCH,
                  policy=POLICY_VERSION)
    result.update(changes)
    return result


def initialize(tmp_path):
    value = registry(tmp_path / "identity.sqlite")
    assert value.create() == 0
    return value


def test_exact_binding_survives_reopen_without_dispatch_or_aliasing(tmp_path):
    value = initialize(tmp_path)
    request = candidate()
    first = value.record_reviewed(**request)
    assert first.status == "RECORDED_BINDING_ONLY"
    binding = first.binding
    assert (binding.scope, binding.generation, binding.registry_id) == (SCOPE, GENERATION, REGISTRY)
    assert (binding.account_id, binding.draft_id, binding.closure_epoch, binding.policy) == tuple(request.values())
    assert len({binding.account_id, binding.draft_id, binding.subject_id, binding.purge_id}) == 4
    assert registry(value._storage._path).inspect(request["account_id"]) == binding
    again = value.record_reviewed(**request)
    assert again.status == "OBSERVED_BINDING_ONLY" and again.binding == binding
    assert value.inspect() == 1
    for receipt in (first, again):
        assert receipt.journal_dispatch_admitted is False
        assert receipt.purge_or_restore_admitted is False
        assert binding.account_id not in repr(receipt) and binding.subject_id not in repr(binding)
        with pytest.raises(FrozenInstanceError):
            receipt.journal_dispatch_admitted = True
    with pytest.raises(FrozenInstanceError):
        binding.purge_id = str(uuid4())


@pytest.mark.parametrize("field,new", [
    ("draft_id", str(uuid4())), ("closure_epoch", "2026-10-23T12:00:00.000000Z"),
])
def test_app_rollback_cannot_replace_an_independently_held_binding(tmp_path, field, new):
    value = initialize(tmp_path)
    request = candidate()
    app = tmp_path / "app.sqlite"
    with sqlite3.connect(app) as db:
        db.execute("CREATE TABLE draft(id TEXT)")
    old_app = tmp_path / "old-app.sqlite"
    shutil.copyfile(app, old_app)
    recorded = value.record_reviewed(**request).binding
    with sqlite3.connect(app) as db:
        db.execute("INSERT INTO draft VALUES(?)", (request["draft_id"],))
    shutil.copyfile(old_app, app)
    with sqlite3.connect(app) as db:
        assert db.execute("SELECT count(*) FROM draft").fetchone()[0] == 0
    with pytest.raises(identity.IdentityError, match="independent review"):
        value.record_reviewed(**dict(request, **{field: new}))
    assert value.inspect(request["account_id"]) == recorded


def test_joint_registry_rollback_is_not_claimed_as_detected(tmp_path):
    value = initialize(tmp_path)
    old = tmp_path / "custody-old.sqlite"
    shutil.copyfile(value._storage._path, old)
    request = candidate()
    value.record_reviewed(**request)
    shutil.copyfile(old, value._storage._path)
    # Explicit residual: independent placement/freshness is a later host gate.
    assert value.inspect(request["account_id"]) is None


@pytest.mark.parametrize("change", [dict(scope="other"), dict(generation=str(uuid4())), dict(registry_id=str(uuid4()))])
def test_scope_generation_and_registry_pins_are_not_adopted_from_file(tmp_path, change):
    value = initialize(tmp_path)
    with pytest.raises(identity.IdentityError, match="binding mismatch"):
        registry(value._storage._path, **change).inspect()


def test_missing_state_never_bootstraps_and_bootstrap_never_overwrites(tmp_path):
    value = registry(tmp_path / "identity.sqlite")
    with pytest.raises(identity.IdentityError):
        value.inspect()
    assert not value._storage._path.exists()
    value.create()
    before = value._storage._path.read_bytes()
    with pytest.raises(identity.IdentityError, match="no overwrite"):
        value.create()
    assert value._storage._path.read_bytes() == before
    assert value.inspect(str(uuid4())) is None


def test_hardlinked_registry_is_refused_without_repair(tmp_path):
    value = initialize(tmp_path)
    alias = tmp_path / "alias.sqlite"
    os.link(value._storage._path, alias)
    with pytest.raises(identity.IdentityError):
        value.inspect()
    assert alias.exists() and value._storage._path.exists()


def test_half_bootstrap_remains_quarantined(tmp_path):
    value = registry(tmp_path / "partial.sqlite")
    value._storage._path.write_bytes(b"")
    with pytest.raises(identity.IdentityError):
        value.inspect()
    with pytest.raises(identity.IdentityError, match="no overwrite"):
        value.create()
    assert value._storage._path.stat().st_size == 0


def test_registry_reuses_only_reviewed_sqlite_connection_safeguards(tmp_path):
    value = initialize(tmp_path)
    with value._connection() as connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 3
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert value.inspect() == 0


@pytest.mark.parametrize("change", [dict(scope="bad scope"), dict(generation="not-a-uuid"), dict(generation=True)])
def test_invalid_independent_binding_is_rejected_before_io(tmp_path, change):
    with pytest.raises(identity.IdentityError):
        registry(tmp_path / "missing.sqlite", **change)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("bad", [None, True, 1, "x", NON_V4, str(uuid4()).upper()])
def test_invalid_registry_identity_is_rejected_before_file_creation(tmp_path, bad):
    with pytest.raises(identity.IdentityError):
        registry(tmp_path / "missing.sqlite", registry_id=bad)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("field,bad", [
    ("account_id", True), ("account_id", "name@example.invalid"), ("draft_id", NON_V4),
    ("closure_epoch", "2026-10-22T12:00:00Z"), ("closure_epoch", "2026-02-30T12:00:00.000000Z"),
    ("closure_epoch", "2026-10-22T12:00:00.000000+00:00"), ("policy", "other"), ("policy", None),
])
def test_invalid_candidate_is_rejected_without_mutation(tmp_path, field, bad):
    value = initialize(tmp_path)
    before = value._storage._path.read_bytes()
    with pytest.raises(identity.IdentityError):
        value.record_reviewed(**candidate(**{field: bad}))
    assert value._storage._path.read_bytes() == before


def test_draft_cannot_be_rebound_to_other_account_or_used_as_an_identity(tmp_path):
    value = initialize(tmp_path)
    request = candidate()
    recorded = value.record_reviewed(**request).binding
    for altered in (dict(request, account_id=str(uuid4())),
                    candidate(account_id=recorded.subject_id),
                    candidate(draft_id=recorded.purge_id),
                    candidate(account_id=request["draft_id"])):
        with pytest.raises(identity.IdentityError):
            value.record_reviewed(**altered)
    assert value.inspect() == 1


def test_uuid_collision_never_allocates_a_second_identity(tmp_path, monkeypatch):
    value = initialize(tmp_path)
    first = value.record_reviewed(**candidate()).binding
    monkeypatch.setattr(identity, "uuid4", lambda: first.purge_id)
    with pytest.raises(identity.IdentityError, match="allocation failed"):
        value.record_reviewed(**candidate())
    assert value.inspect() == 1


@pytest.mark.parametrize("tamper", [
    "PRAGMA user_version=9", "PRAGMA application_id=9", "CREATE TABLE extra(id INTEGER)",
    "UPDATE identity_meta SET revision=7", "UPDATE identity_subjects SET subject_id='bad'",
    "UPDATE identity_requests SET policy='other'", "DELETE FROM identity_requests",
    "UPDATE identity_requests SET closure_epoch='bad'",
])
def test_corrupt_unknown_or_partial_evidence_fails_closed(tmp_path, tamper):
    value = initialize(tmp_path)
    value.record_reviewed(**candidate())
    with sqlite3.connect(value._storage._path) as db:
        db.execute(tamper)
    with pytest.raises(identity.IdentityError):
        value.inspect()
    with pytest.raises(identity.IdentityError):
        value.record_reviewed(**candidate())


def test_storage_lock_is_bounded_no_retry_and_no_partial_binding(tmp_path):
    value = initialize(tmp_path)
    with sqlite3.connect(value._storage._path, isolation_level=None) as held:
        held.execute("BEGIN IMMEDIATE")
        with pytest.raises(identity.IdentityError, match="no retry"):
            value.record_reviewed(**candidate())
        held.execute("ROLLBACK")
    assert value.inspect() == 0


def test_capacity_retains_old_bindings_and_allows_only_observation(tmp_path):
    value = initialize(tmp_path)
    first = None
    for _ in range(identity.MAX_BINDINGS):
        request = candidate()
        value.record_reviewed(**request)
        first = first or request
    with pytest.raises(identity.IdentityError, match="capacity"):
        value.record_reviewed(**candidate())
    assert value.record_reviewed(**first).status == "OBSERVED_BINDING_ONLY"
    assert value.inspect() == identity.MAX_BINDINGS


def test_commit_ack_loss_is_unknown_and_inspection_does_not_upgrade_authority(tmp_path, monkeypatch):
    value = initialize(tmp_path)
    original = value._storage._connection

    class LostAck:
        def __init__(self, connection): self.connection = connection
        def execute(self, sql, *args):
            result = self.connection.execute(sql, *args)
            if sql == "COMMIT":
                raise sqlite3.OperationalError("SYNTHETIC-SENSITIVE-PAYLOAD")
            return result

    @contextmanager
    def fail(*args, **kwargs):
        with original(*args, **kwargs) as connection:
            yield LostAck(connection)

    monkeypatch.setattr(value._storage, "_connection", fail)
    request = candidate()
    with pytest.raises(identity.IdentityCommitUnknown) as caught:
        value.record_reviewed(**request)
    assert "SYNTHETIC-SENSITIVE-PAYLOAD" not in str(caught.value)
    monkeypatch.setattr(value._storage, "_connection", original)
    binding = value.inspect(request["account_id"])
    assert binding is not None
    observed = value.record_reviewed(**request)
    assert observed.binding == binding and observed.status == "OBSERVED_BINDING_ONLY"
    assert not observed.journal_dispatch_admitted and not observed.purge_or_restore_admitted


def test_registry_does_not_offer_dispatch_reset_supersession_or_application_hooks():
    assert identity.POLICY == POLICY_VERSION
    for name in ("append", "prepare", "transition", "reset", "delete", "purge", "restore", "dispatch", "supersede"):
        assert not hasattr(identity.IdentityRegistry, name)
    root = Path(__file__).resolve().parents[2]
    for folder in (root / "backend/routers", root / "backend/tasks"):
        assert not any("erasure_identity" in file.read_text(encoding="utf8") for file in folder.rglob("*.py"))


def test_actual_committed_draft_binding_rejects_a_lost_app_row_replacement(preparation_db, tmp_path):
    from sqlalchemy import delete, select
    from models.erasure_preparation import ErasurePreparation
    from services.erasure_preparation import prepare_erasure_draft

    fixture = preparation_db
    value = initialize(tmp_path)

    async def prepare_commit():
        async with fixture.db.begin():
            return await prepare_erasure_draft(fixture.db, fixture.uid)

    draft = fixture.run(prepare_commit())

    async def independently_read_committed_row():
        async with fixture.db.begin():
            row = await fixture.db.scalar(select(ErasurePreparation).where(ErasurePreparation.id == draft.request_id))
            assert row is not None and row.state == "DRAFT"
            return dict(account_id=row.user_id, draft_id=row.id, policy=row.closure_policy_version,
                        closure_epoch=row.closure_started_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))

    # Trusted operator read is explicit in this fixture, not an application hook.
    reviewed = fixture.run(independently_read_committed_row())
    recorded = value.record_reviewed(**reviewed)
    assert recorded.binding.account_id == fixture.uid and recorded.binding.draft_id == draft.request_id
    assert recorded.binding.purge_id != draft.request_id

    async def simulate_lost_app_row():
        async with fixture.db.begin():
            await fixture.db.execute(delete(ErasurePreparation))

    fixture.run(simulate_lost_app_row())
    replacement = fixture.run(prepare_commit())
    assert replacement.request_id != draft.request_id
    with pytest.raises(identity.IdentityError, match="independent review"):
        value.record_reviewed(**dict(reviewed, draft_id=replacement.request_id))
    assert value.inspect(fixture.uid) == recorded.binding
