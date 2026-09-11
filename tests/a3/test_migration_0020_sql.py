"""Static checks on migration 0020 (no database): the seven frozen A3 table
names, RLS, write fences, append-only vs mutable split, exact-revision FK
triples, uniqueness rules, the extended object-type CHECK list, and no
roles/grants/backfill/secrets.
"""
from __future__ import annotations

from pathlib import Path

MIGRATION = (Path(__file__).resolve().parents[2]
             / "src/memory_service_app/migrations/0020_execution_handover.sql")

A3_TABLES = (
    "gov_execution_state", "gov_execution_authorities",
    "gov_acceptance_appointments", "gov_a3_work_item_state",
    "gov_work_receipts", "gov_a3_delivery_acceptances",
    "gov_a3_outcome_assessments",
)
APPEND_ONLY_TABLES = (
    "gov_execution_authorities", "gov_acceptance_appointments",
    "gov_work_receipts", "gov_a3_delivery_acceptances",
    "gov_a3_outcome_assessments",
)
MUTABLE_TABLES = ("gov_execution_state", "gov_a3_work_item_state")

PRIOR_OBJECT_TYPES = (
    "CompanyOutcome", "BusinessCommitment", "ExecutionCommitment",
    "FeedbackThread", "ManagementAdjustment", "Decision",
    "MetricObservation", "EvidenceAsset", "WorkItem", "Deliverable",
    "ProtocolSentinel",
    "CompanyReference", "CapacityObservation", "FormationRound",
    "DomainSubmission", "CompanyComposition", "Mission", "DomainCommitment",
)


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_file_exists():
    assert MIGRATION.is_file()


def test_new_tables_created_with_forced_rls():
    text = _text()
    for table in A3_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in text
        assert f"'{table}'" in text  # RLS / trigger loops
    assert "ENABLE ROW LEVEL SECURITY" in text
    assert "FORCE ROW LEVEL SECURITY" in text
    assert text.count("gov_scope_matches(scope_id) OR gov_control_plane_on()") >= 2


def test_every_new_table_requires_runtime_capability():
    text = _text()
    assert "gov_require_runtime_capability()" in text
    loop = text.split("$a3_capability$")[1]
    for table in A3_TABLES:
        assert f"'{table}'" in loop


def test_append_only_tables_reject_update_and_delete():
    text = _text()
    loop = text.split("$a3_append_only$")[1]
    for table in APPEND_ONLY_TABLES:
        assert f"'{table}'" in loop
    assert "gov_execution_state" not in loop
    assert "gov_a3_work_item_state" not in loop
    assert "BEFORE UPDATE OR DELETE" in loop
    assert "gov_reject_mutation()" in loop


def test_mutable_tables_reject_delete():
    text = _text()
    loop = text.split("$a3_no_delete$")[1]
    for table in MUTABLE_TABLES:
        assert f"'{table}'" in loop
    assert "BEFORE DELETE" in loop


def test_object_type_check_adds_execution_plan_only():
    text = _text()
    block = text.split("ADD CONSTRAINT ck_gov_object_type")[1]
    for object_type in PRIOR_OBJECT_TYPES:
        assert f"'{object_type}'" in block
    assert "'ExecutionPlan'" in block


def test_authority_and_appointment_are_independent_records():
    text = _text()
    assert "UNIQUE (scope_id, commitment_object_id, execution_epoch)" in text
    assert "UNIQUE (scope_id, commitment_object_id, appointment_version)" in text
    assert "CHECK (execution_epoch >= 1)" in text
    assert "CHECK (appointment_version >= 1)" in text
    assert "valid_from < valid_to" in text
    # The mutable state carries both gates as independent pointers AND
    # independently stored current generations (0 before first release).
    assert "current_authority_id" in text
    assert "current_appointment_id" in text
    assert "current_execution_epoch integer NOT NULL DEFAULT 0" in text
    assert "current_appointment_version integer NOT NULL DEFAULT 0" in text
    assert "CHECK (current_execution_epoch >= 0)" in text
    assert "CHECK (current_appointment_version >= 0)" in text
    assert "(current_authority_id IS NULL) = (current_execution_epoch = 0)" in text
    assert "(current_appointment_id IS NULL) = (current_appointment_version = 0)" in text
    # Standards revision is an exact Mission revision FK triple.
    assert ("FOREIGN KEY (scope_id, mission_object_id, standards_revision_id)\n"
            "        REFERENCES gov_object_revisions (scope_id, object_id, revision_id)") in text


def test_scope_commitment_qualified_state_pointers():
    text = _text()
    # State pointers can never reference another commitment's grant.
    assert ("FOREIGN KEY (scope_id, commitment_object_id, current_authority_id)\n"
            "        REFERENCES gov_execution_authorities (scope_id, commitment_object_id, authority_id)") in text
    assert ("FOREIGN KEY (scope_id, commitment_object_id, current_appointment_id)\n"
            "        REFERENCES gov_acceptance_appointments (scope_id, commitment_object_id, appointment_id)") in text


def test_compound_identity_binding_fks():
    text = _text()
    # Work item state and work receipts bind authority id + exact commitment
    # revision + epoch + IC identity to the SAME authority record.
    assert text.count(
        "REFERENCES gov_execution_authorities (scope_id, authority_id,\n"
        "                     commitment_object_id, commitment_revision_id,\n"
        "                     execution_epoch, ic_assignment_id, ic_principal_id)") == 2
    # Reviews bind appointment id + version + verifier identity to the SAME
    # appointment record.
    assert ("REFERENCES gov_acceptance_appointments (scope_id, appointment_id,\n"
            "                     appointment_version, acceptor_assignment_id,\n"
            "                     acceptor_principal_id)") in text
    # accepted_by is a scoped principal FK on the receiving IC assignment.
    assert ("FOREIGN KEY (scope_id, ic_assignment_id, accepted_by)\n"
            "        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id)") in text


def test_grant_appointment_receipt_actions_are_not_null():
    text = _text()
    for table in ("gov_execution_authorities", "gov_acceptance_appointments",
                  "gov_work_receipts"):
        block = text.split(f"CREATE TABLE IF NOT EXISTS {table}")[1].split(");")[0]
        assert "action_id            uuid NOT NULL" in block, table


def test_exact_revision_fk_triples():
    text = _text()
    # Every business record pins exact scope-qualified revision triples.
    assert text.count("REFERENCES gov_object_revisions (scope_id, object_id, revision_id)") >= 8
    assert text.count(
        "REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id)") >= 3


def test_reviewed_submission_revision_is_terminal():
    text = _text()
    assert "UNIQUE (scope_id, deliverable_revision_id)" in text
    assert "verification_result IN ('accepted', 'changes_requested')" in text
    assert "payload_hash ~ '^[0-9a-f]{64}$'" in text


def test_work_receipt_single_reception_per_work_item():
    text = _text()
    assert "UNIQUE (scope_id, work_item_object_id)" in text


def test_a3_outcome_assessment_fixed_mapping():
    text = _text()
    assert "company_reference_object_id" in text
    assert "composition_revision_id" in text
    assert "CHECK (qualified_customer_count >= 0)" in text
    assert "CHECK (target_count >= 1)" in text
    assert "assessment_result IN ('achieved', 'not_achieved', 'inconclusive')" in text
    # The DB trigger validates in-scope EvidenceAsset revisions and A3 review
    # rows; it never references CompanyOutcome / MetricObservation.
    trigger = text.split("$gov_validate_a3_outcome_assessment_refs$")[1]
    assert "o.object_type='EvidenceAsset'" in trigger
    assert "object_type='CompanyReference'" in trigger
    assert "object_type='CompanyComposition'" in trigger
    assert "gov_a3_delivery_acceptances" in trigger
    assert "MetricObservation" not in trigger
    assert "CompanyOutcome" not in trigger


def test_action_receipt_fks_are_deferrable():
    text = _text()
    assert text.count("REFERENCES gov_action_receipts (scope_id, receipt_id) "
                      "DEFERRABLE INITIALLY DEFERRED") >= 5


def test_no_roles_grants_backfill_or_seeded_authority():
    text = _text()
    assert "CREATE ROLE" not in text
    assert "GRANT " not in text
    assert "INSERT INTO" not in text  # no data backfill / no seeded success


def test_no_secrets_or_host_absolute_paths():
    text = _text().lower()
    for needle in ("/users/", "/semantica", "password", "postgres://",
                   "postgresql://", "secret", "token"):
        assert needle not in text, f"migration text must not contain {needle!r}"
