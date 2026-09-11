"""Static checks on migration 0019 (no database): table set, RLS, write
fences, append-only/no-delete triggers, the extended object-type and
lifecycle-status CHECK lists, and no secrets or host-local absolute paths.
"""
from __future__ import annotations

from pathlib import Path

MIGRATION = (Path(__file__).resolve().parents[2]
             / "src/memory_service_app/migrations/0019_company_composition.sql")

A2_TABLES = (
    "gov_formation_round_state", "gov_round_formal_submissions",
    "gov_composition_confirmations", "gov_mission_index",
    "gov_activation_records",
)
APPEND_ONLY_TABLES = (
    "gov_composition_confirmations", "gov_mission_index", "gov_activation_records",
)
MUTABLE_TABLES = ("gov_formation_round_state", "gov_round_formal_submissions")

A2_OBJECT_TYPES = (
    "CompanyReference", "CapacityObservation", "FormationRound",
    "DomainSubmission", "CompanyComposition", "Mission", "DomainCommitment",
)
PRE_A2_OBJECT_TYPES = (
    "CompanyOutcome", "BusinessCommitment", "ExecutionCommitment",
    "FeedbackThread", "ManagementAdjustment", "Decision",
    "MetricObservation", "EvidenceAsset", "WorkItem", "Deliverable",
    "ProtocolSentinel",
)
PRE_A2_STATUSES = (
    "draft", "offered", "proposed", "open", "routed", "accepted",
    "investigating", "awaiting_acceptance", "active", "superseded",
    "applied", "confirmed", "closed", "dismissed", "recorded", "stored",
    "in_progress", "submitted", "changes_requested", "delivery_accepted",
)


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_file_exists():
    assert MIGRATION.is_file()


def test_new_tables_created_with_forced_rls():
    text = _text()
    for table in A2_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in text
        assert f"'{table}'" in text  # RLS / trigger loops
    assert "ENABLE ROW LEVEL SECURITY" in text
    assert "FORCE ROW LEVEL SECURITY" in text
    assert text.count("gov_scope_matches(scope_id) OR gov_control_plane_on()") >= 2


def test_every_new_table_requires_runtime_capability():
    text = _text()
    assert "gov_require_runtime_capability()" in text
    loop = text.split("$a2_capability$")[1]
    for table in A2_TABLES:
        assert f"'{table}'" in loop


def test_append_only_tables_reject_update_and_delete():
    text = _text()
    loop = text.split("$a2_append_only$")[1]
    for table in APPEND_ONLY_TABLES:
        assert f"'{table}'" in loop
    assert "BEFORE UPDATE OR DELETE" in loop
    assert "gov_reject_mutation()" in loop


def test_mutable_tables_reject_delete():
    text = _text()
    loop = text.split("$a2_no_delete$")[1]
    for table in MUTABLE_TABLES:
        assert f"'{table}'" in loop
    assert "BEFORE DELETE" in loop


def test_object_type_check_covers_eighteen_types():
    text = _text()
    block = text.split("ADD CONSTRAINT ck_gov_object_type")[1]
    for object_type in PRE_A2_OBJECT_TYPES + A2_OBJECT_TYPES:
        assert f"'{object_type}'" in block


def test_status_check_keeps_all_prior_statuses_and_adds_formed():
    text = _text()
    block = text.split("ADD CONSTRAINT ck_gov_object_status")[1]
    for status in PRE_A2_STATUSES:
        assert f"'{status}'" in block
    assert "'formed'" in block


def test_confirmations_double_uniqueness():
    text = _text()
    assert "UNIQUE (scope_id, composition_object_id, composition_revision_id, assignment_id)" in text
    assert "UNIQUE (scope_id, composition_object_id, composition_revision_id, principal_id)" in text
    assert "responsibility_role IN ('company_decider', 'area_accountable')" in text
    assert "manifest_hash ~ '^[0-9a-f]{64}$'" in text


def test_round_state_uniqueness_and_generation_checks():
    text = _text()
    assert "UNIQUE (scope_id, company_id, period_id)" in text
    assert "CHECK (member_set_version >= 1)" in text
    assert "CHECK (input_set_version >= 1)" in text


def test_activation_records_single_per_round():
    text = _text()
    assert "UNIQUE (scope_id, round_object_id)" in text


def test_action_receipt_fks_are_deferrable():
    text = _text()
    assert text.count("REFERENCES gov_action_receipts (scope_id, receipt_id) "
                      "DEFERRABLE INITIALLY DEFERRED") >= 3


def test_no_roles_grants_or_backfill():
    text = _text()
    assert "CREATE ROLE" not in text
    assert "GRANT " not in text
    assert "INSERT INTO" not in text  # no data backfill in this migration


def test_no_secrets_or_host_absolute_paths():
    text = _text().lower()
    for needle in ("/users/", "/semantica", "password", "postgres://",
                   "postgresql://", "secret", "token"):
        assert needle not in text, f"migration text must not contain {needle!r}"
