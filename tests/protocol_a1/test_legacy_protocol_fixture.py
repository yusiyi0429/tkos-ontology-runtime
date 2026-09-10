"""Offline tests pinning the legacy protocol fixture's exact-SQL matching.

`tests/legacy_protocol_fixture.answer_query` must accept ONLY the four exact
normalized SELECT statements issued by governed/protocol.py (whitespace
collapsed).  Any drift — an appended filter, a changed LIMIT, a reordered
ORDER BY — must raise AssertionError instead of fabricating registered rows.
The SQL literals below are deliberately duplicated (not imported) so a change
to either side fails here.

Pure standard library in memory: no database, no production imports, no env.
"""
from __future__ import annotations

import pytest

from tests import legacy_protocol_fixture as fixture

SCOPE = "00000000-0000-0000-0000-000000009999"
KNOWN_OBJECT = "00000000-0000-0000-0000-000000000001"
UNKNOWN_OBJECT = "00000000-0000-0000-0000-000000000002"

BINDING_SINGLE_SQL = (
    "SELECT * FROM gov_object_protocol_bindings "
    "WHERE scope_id=%s AND object_id=%s "
    "ORDER BY binding_version DESC, recorded_at DESC, binding_id DESC LIMIT 1")
BINDING_BATCH_SQL = (
    "SELECT DISTINCT ON (object_id) * FROM gov_object_protocol_bindings "
    "WHERE scope_id=%s AND object_id=ANY(%s::uuid[]) "
    "ORDER BY object_id, binding_version DESC, recorded_at DESC, binding_id DESC")
PROFILE_SQL = (
    "SELECT * FROM gov_method_profile_revisions "
    "WHERE scope_id=%s AND profile_id=%s AND revision=%s")
REGISTRY_SQL = (
    "SELECT * FROM gov_protocol_support_registry "
    "WHERE scope_id=%s AND protocol_id=%s "
    "ORDER BY registry_seq DESC, recorded_at DESC, registry_row_id DESC LIMIT 1")


def answer(sql, params, scope=SCOPE, known=(KNOWN_OBJECT,)):
    return fixture.answer_query(sql, params, scope, known)


# ------------------------------------------------------------- exact positives

def test_single_binding_query_returns_frozen_row():
    rows = answer(BINDING_SINGLE_SQL, (SCOPE, KNOWN_OBJECT))
    assert len(rows) == 1
    assert rows[0]["protocol_id"] == fixture.LEGACY_PROTOCOL_ID


def test_batched_binding_query_returns_known_objects_only():
    rows = answer(BINDING_BATCH_SQL, (SCOPE, [KNOWN_OBJECT, UNKNOWN_OBJECT]))
    assert [row["object_id"] for row in rows] == [KNOWN_OBJECT]


def test_profile_query_returns_installed_record():
    rows = answer(PROFILE_SQL, (SCOPE, fixture.LEGACY_PROFILE_ID,
                                fixture.LEGACY_PROFILE_REVISION))
    assert len(rows) == 1
    assert rows[0]["canonical_hash"] == fixture.LEGACY_PROFILE_CANONICAL_HASH


def test_registry_query_returns_support_entry():
    rows = answer(REGISTRY_SQL, (SCOPE, fixture.LEGACY_PROTOCOL_ID))
    assert len(rows) == 1
    assert rows[0]["contract_version"] == fixture.LEGACY_CONTRACT_VERSION


def test_unknown_object_is_unregistered_not_default_legacy():
    assert answer(BINDING_SINGLE_SQL, (SCOPE, UNKNOWN_OBJECT)) == []


def test_other_table_returns_none_for_caller_routing():
    assert answer("SELECT * FROM gov_protocol_policies WHERE scope_id=%s",
                  (SCOPE,)) is None


# --------------------------------------------- malformed or hostile negatives

def test_write_over_registration_table_rejected():
    with pytest.raises(AssertionError):
        answer("UPDATE gov_object_protocol_bindings SET protocol_id='x' "
               "WHERE scope_id=%s", (SCOPE,))


def test_multi_statement_rejected():
    with pytest.raises(AssertionError):
        answer(BINDING_SINGLE_SQL + "; SELECT 1", (SCOPE, KNOWN_OBJECT))


def test_wrong_scope_rejected():
    with pytest.raises(AssertionError):
        answer(BINDING_SINGLE_SQL, ("other-scope", KNOWN_OBJECT))


def test_unknown_shape_over_registration_table_rejected():
    with pytest.raises(AssertionError):
        answer("SELECT count(*) FROM gov_object_protocol_bindings "
               "WHERE scope_id=%s", (SCOPE,))


def test_wrong_param_count_rejected():
    with pytest.raises(AssertionError):
        answer(BINDING_SINGLE_SQL, (SCOPE,))


def test_ambiguous_registration_read_rejected():
    with pytest.raises(AssertionError):
        answer("SELECT * FROM gov_object_protocol_bindings b "
               "JOIN gov_protocol_support_registry r ON r.scope_id=b.scope_id "
               "WHERE b.scope_id=%s", (SCOPE,))


# ------------------------------------------------- exact-match drift negatives

def test_profile_query_with_appended_filter_rejected():
    with pytest.raises(AssertionError):
        answer(PROFILE_SQL + " AND FALSE", (SCOPE, fixture.LEGACY_PROFILE_ID,
                                            fixture.LEGACY_PROFILE_REVISION))


def test_binding_query_with_limit_zero_rejected():
    with pytest.raises(AssertionError):
        answer("SELECT * FROM gov_object_protocol_bindings "
               "WHERE scope_id=%s AND object_id=%s "
               "ORDER BY binding_version DESC LIMIT 0", (SCOPE, KNOWN_OBJECT))


def test_registry_query_with_limit_zero_rejected():
    with pytest.raises(AssertionError):
        answer("SELECT * FROM gov_protocol_support_registry "
               "WHERE scope_id=%s AND protocol_id=%s "
               "ORDER BY registry_seq DESC LIMIT 0", (SCOPE, fixture.LEGACY_PROTOCOL_ID))
