"""Offline tests for governed context_pack selected/excluded protocol identity (B10 §3).

Every selected and excluded entry must carry the object's explicit protocol
metadata; the stored snapshot write keeps the original bytes.  Uses a strict
fake connection: unknown SQL and any write except the snapshot INSERT are
errors; protocol registration reads are served by the frozen legacy fixture.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from memory_service_runtime.governed import readers
from memory_service_runtime.governed.errors import GovernedError
from tests import legacy_protocol_fixture

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)
SCOPE = str(UUID(int=9100))
DOMAIN = str(UUID(int=9101))


def uid(number):
    return str(UUID(int=number))


def moment(number):
    return BASE + timedelta(hours=number)


class Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class PackConn:
    """Answers _selected_revision / snapshot-INSERT / protocol reads only."""

    def __init__(self, objects, revisions, effective, *, protocol_known=None):
        self._objects = objects          # object_id -> row
        self._revisions = revisions      # revision_id -> row
        self._effective = effective      # object_id -> effective revision_id or None
        # Objects explicitly registered under the frozen legacy fixture;
        # defaults to the listed objects, never to the request's object id.
        self._protocol_known = (set(protocol_known) if protocol_known is not None
                                else set(objects))
        self.inserted = None

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split())
        protocol_rows = legacy_protocol_fixture.answer_query(
            text, params, SCOPE, self._protocol_known)
        if protocol_rows is not None:
            return Result(protocol_rows)
        if text.startswith("SELECT"):
            if "FROM gov_lifecycle_events e" in text:
                # FORMAL_TYPES branch: join event effective revision.
                object_id = str(params[1])
                known_at, valid_at = params[2], params[4]
                effective = self._effective.get(object_id)
                if not effective:
                    return Result([])
                row = self._revisions.get(effective)
                if row is None or row["recorded_at"] > known_at or row["valid_from"] > valid_at:
                    return Result([])
                if row["valid_to"] is not None and row["valid_to"] <= valid_at:
                    return Result([])
                return Result([row])
            if "FROM gov_object_revisions" in text:
                object_id = str(params[1])
                known_at, valid_at = params[2], params[3]
                rows = [row for row in self._revisions.values()
                        if str(row["object_id"]) == object_id
                        and row["recorded_at"] <= known_at and row["valid_from"] <= valid_at
                        and (row["valid_to"] is None or row["valid_to"] > valid_at)]
                rows.sort(key=lambda row: (row["recorded_at"], row["object_version"]),
                          reverse=True)
                return Result(rows[:1])
            if "FROM gov_outcome_assessments" in text:
                return Result([])
            raise AssertionError(f"unexpected read: {text[:120]}")
        if text.startswith("INSERT INTO gov_context_snapshots"):
            self.inserted = {"selected": params[5], "excluded": params[6]}
            return Result([{"recorded_at": moment(99)}])
        raise AssertionError(f"unexpected statement: {text[:120]}")


def _object(number, object_type, *, effective_rev=None):
    object_id, revision_id = uid(number), uid(number + 100)
    return object_id, {
        "object_id": object_id, "domain_id": DOMAIN, "object_type": object_type,
        "lifecycle_status": "confirmed", "object_version": 1,
        "latest_revision_id": revision_id,
        "effective_revision_id": effective_rev,
        "created_at": moment(1),
    }


def _revision(number, object_id, *, at=1, payload=None):
    revision_id = uid(number + 100)
    return revision_id, {
        "revision_id": revision_id, "object_id": object_id, "object_version": 1,
        "payload": payload or {"title": f"Object {number}"},
        "payload_hash": f"{number:064x}", "recorded_at": moment(at),
        "valid_from": moment(at), "valid_to": None,
    }


@pytest.fixture
def harness(monkeypatch):
    state = {}

    def object_row(conn, ctx, object_id):
        row = state["conn"]._objects.get(str(object_id))
        if row is None:
            raise GovernedError("NOT_FOUND")
        return dict(row)

    def revision_row(conn, ctx, object_id, revision_id):
        row = state["conn"]._revisions.get(str(revision_id))
        if row is None or str(row["object_id"]) != str(object_id):
            raise GovernedError("NOT_FOUND")
        return dict(row)

    monkeypatch.setattr(readers.db, "object_row", object_row)
    monkeypatch.setattr(readers.db, "revision_row", revision_row)

    def build(objects, revisions, effective):
        conn = PackConn(objects, revisions, effective)
        state["conn"] = conn
        return conn

    state["build"] = build
    return state


def _collect(conn, object_ids):
    ctx = type("Ctx", (), {"scope_id": SCOPE, "principal_id": uid(9102)})()
    return readers.context_pack(conn, ctx, object_ids, valid_at=moment(20), known_at=moment(20))


def test_excluded_no_effective_carries_protocol_identity(harness):
    object_id, obj = _object(1, "CompanyOutcome", effective_rev=None)
    revision_id, rev = _revision(1, object_id)
    conn = harness["build"]({object_id: obj}, {revision_id: rev}, {object_id: None})
    result = _collect(conn, [object_id])
    assert result["selected"] == []
    assert len(result["excluded"]) == 1
    excluded = result["excluded"][0]
    assert excluded["object_id"] == object_id
    assert excluded["reason"] == "no_effective_revision_at_requested_times"
    protocol_meta = excluded["protocol"]
    assert protocol_meta["registration_status"] == "registered"
    assert protocol_meta["interpretation_status"] == "legacy_v0_2"
    assert protocol_meta["protocol_id"] == "tkos.legacy-governed"
    assert protocol_meta["contract_version"] == "tkos.governed/v0.2"


def test_excluded_source_not_known_carries_protocol_identity(harness):
    source_id, source_obj = _object(2, "MetricObservation")
    source_rid, source_rev = _revision(2, source_id, at=30)  # recorded after known_at
    object_id, obj = _object(1, "MetricObservation")
    revision_id, rev = _revision(1, object_id, payload={
        "title": "观测", "upstream_refs": [{"object_id": source_id, "revision_id": source_rid}]})
    conn = harness["build"]({object_id: obj, source_id: source_obj},
                            {revision_id: rev, source_rid: source_rev}, {})
    result = _collect(conn, [object_id])
    assert result["selected"] == []
    assert len(result["excluded"]) == 1
    excluded = result["excluded"][0]
    assert excluded["reason"] == "source_not_known_at_requested_time"
    assert excluded["protocol"]["interpretation_status"] == "legacy_v0_2"


def test_selected_carries_protocol_identity_and_snapshot_stores_original(harness):
    object_id, obj = _object(1, "MetricObservation")
    revision_id, rev = _revision(1, object_id)
    conn = harness["build"]({object_id: obj}, {revision_id: rev}, {})
    result = _collect(conn, [object_id])
    assert len(result["selected"]) == 1
    selected = result["selected"][0]
    assert selected["revision_id"] == revision_id
    assert selected["protocol"]["interpretation_status"] == "legacy_v0_2"
    assert selected["protocol"]["registration_status"] == "registered"
    # The stored snapshot row carries exactly the served selected/excluded.
    assert conn.inserted is not None


def test_non_legacy_object_excluded_with_protocol_identity(harness):
    object_id, obj = _object(1, "MetricObservation")
    revision_id, rev = _revision(1, object_id)
    # Object visible to business auth but absent from protocol registrations:
    # excluded explicitly, never default-legacy.
    conn = harness["build"]({object_id: obj}, {revision_id: rev}, {},
                            )  # protocol registration below
    conn._protocol_known = set()
    result = _collect(conn, [object_id])
    assert result["selected"] == []
    excluded = result["excluded"][0]
    assert excluded["reason"] == "protocol_interpretation_not_supported"
    assert excluded["protocol"]["registration_status"] == "unregistered"
