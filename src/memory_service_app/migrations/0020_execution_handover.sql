-- 0020_execution_handover: A3 DRI-IC execution handover (Contract-A) schema.
--
-- Contract basis: docs/runtime-a3-engineering.md (FROZEN sections 1-6) and
-- docs/runtime-a3-api.md.  This migration adds the 'ExecutionPlan' object
-- type and the seven A3 execution tables named in engineering section 6:
--   mutable (UPDATE allowed, no DELETE):
--     gov_execution_state, gov_a3_work_item_state
--   append-only (INSERT only; no UPDATE/DELETE):
--     gov_execution_authorities, gov_acceptance_appointments,
--     gov_work_receipts, gov_a3_delivery_acceptances,
--     gov_a3_outcome_assessments
-- It implements no business handler by itself; the runtime handlers enforce
-- every rule above these constraints.  ExecutionAuthority and
-- AcceptanceAppointment are independent gates with independent generations
-- and clocks; they are never merged into a single state field, and the
-- legacy gov_work_item_state.dri_assignment_id is NOT reinterpreted as IC.
--
-- Like 0016-0019 this file creates no roles and no GRANTs.  Test/deploy
-- infrastructure grants precisely:
--   application-writable, mutable head/pointer rows (SELECT/INSERT/UPDATE;
--   no DELETE):
--     gov_execution_state, gov_a3_work_item_state
--   application-writable, append-only (SELECT/INSERT; no UPDATE/DELETE):
--     gov_execution_authorities, gov_acceptance_appointments,
--     gov_work_receipts, gov_a3_delivery_acceptances,
--     gov_a3_outcome_assessments
--
-- Definitional content (What, plan steps, deliverable content, observation
-- event packets) lives exclusively in immutable gov_object_revisions payloads
-- and object-store evidence bytes.  The tables below hold only mutable
-- head/pointer state and append-only records; each carries scope_id and uses
-- scope-qualified composite FKs so an independent oracle can audit
-- cross-table consistency inside one scope.  Compound FKs bind every work /
-- reception / review record to the SAME authority or appointment record that
-- carries its commitment revision, generation and principal identity — not
-- to independent unrelated references.
--
-- RLS: ENABLE + FORCE on every new table.  Rows are visible inside their
-- scope (app.governed_scope_id) or to a control-plane session, and writes go
-- through the same predicate.  Every new table is covered by the
-- gov_require_runtime_capability() write fence (INSERT/UPDATE), the five
-- append-only tables additionally by gov_reject_mutation() (UPDATE/DELETE),
-- and the two mutable tables by gov_reject_mutation() on DELETE.
--
-- Replay safety lives at THE RUNNER LEVEL: migrate.py records applied file
-- names in schema_migrations, so a second runner execution is a no-op and
-- changes no data.  No historical payloads, revisions, receipts, events or
-- hashes are touched; there is no backfill and no seeded
-- authority/appointment of any kind.

-- ---------------------------------------------------------------------------
-- Object types: add 'ExecutionPlan' (A3).  ExecutionCommitment and WorkItem
-- already exist as object types; A3 reuses them with Contract-A payloads.
-- The lifecycle status list is unchanged: A3 reuses
-- offered/active/in_progress/submitted/changes_requested/delivery_accepted/
-- recorded.
-- ---------------------------------------------------------------------------

ALTER TABLE gov_objects DROP CONSTRAINT IF EXISTS ck_gov_object_type;
ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_type CHECK (
    object_type IN (
        'CompanyOutcome', 'BusinessCommitment', 'ExecutionCommitment',
        'FeedbackThread', 'ManagementAdjustment', 'Decision',
        'MetricObservation', 'EvidenceAsset', 'WorkItem', 'Deliverable',
        'ProtocolSentinel',
        'CompanyReference', 'CapacityObservation', 'FormationRound',
        'DomainSubmission', 'CompanyComposition', 'Mission', 'DomainCommitment',
        'ExecutionPlan'
    )
);

-- ---------------------------------------------------------------------------
-- A3 execution handover tables (engineering section 6, frozen names).
-- ---------------------------------------------------------------------------

-- Append-only ExecutionAuthority releases.  One row per (commitment,
-- execution_epoch): the DRI releases execution authority to one exact IC
-- assignment+principal for an independent validity window.  Epochs make
-- authority revocation/re-issue explicit without mutating history; the
-- current pointer AND current generation live in gov_execution_state.
-- uq_gov_authority_binding lets WorkItem state / work receipts bind their
-- authority id, exact commitment revision, epoch and IC identity to the SAME
-- authority record with one composite FK.
CREATE TABLE IF NOT EXISTS gov_execution_authorities (
    authority_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id             uuid NOT NULL,
    commitment_object_id uuid NOT NULL,
    commitment_revision_id uuid NOT NULL,
    execution_epoch      integer NOT NULL,
    ic_assignment_id     uuid NOT NULL,
    ic_principal_id      uuid NOT NULL,
    released_by_assignment_id uuid NOT NULL,
    released_by_principal_id  uuid NOT NULL,
    valid_from           timestamptz NOT NULL,
    valid_to             timestamptz NOT NULL,
    action_id            uuid NOT NULL,
    recorded_at          timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_authority_scope UNIQUE (scope_id, authority_id),
    CONSTRAINT uq_gov_authority_commitment_epoch
        UNIQUE (scope_id, commitment_object_id, execution_epoch),
    CONSTRAINT uq_gov_authority_commitment
        UNIQUE (scope_id, commitment_object_id, authority_id),
    CONSTRAINT uq_gov_authority_binding
        UNIQUE (scope_id, authority_id, commitment_object_id,
                commitment_revision_id, execution_epoch,
                ic_assignment_id, ic_principal_id),
    CONSTRAINT fk_gov_authority_commitment
        FOREIGN KEY (scope_id, commitment_object_id, commitment_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_authority_ic
        FOREIGN KEY (scope_id, ic_assignment_id, ic_principal_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_authority_releaser
        FOREIGN KEY (scope_id, released_by_assignment_id, released_by_principal_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_authority_action
        FOREIGN KEY (scope_id, action_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_gov_authority_epoch CHECK (execution_epoch >= 1),
    CONSTRAINT ck_gov_authority_window CHECK (valid_from < valid_to)
);

-- Append-only AcceptanceAppointments.  One row per (commitment,
-- appointment_version): an independent acceptor assignment+principal is
-- appointed with its own acceptance window and the exact Mission revision
-- whose acceptance standards apply.  Independent of ExecutionAuthority: own
-- gate, own versions, own clock.  uq_gov_appointment_binding lets review
-- records bind appointment id, version and verifier identity to the SAME
-- appointment record with one composite FK.
CREATE TABLE IF NOT EXISTS gov_acceptance_appointments (
    appointment_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id             uuid NOT NULL,
    commitment_object_id uuid NOT NULL,
    commitment_revision_id uuid NOT NULL,
    appointment_version  integer NOT NULL,
    acceptor_assignment_id uuid NOT NULL,
    acceptor_principal_id  uuid NOT NULL,
    mission_object_id    uuid NOT NULL,
    standards_revision_id uuid NOT NULL,
    valid_from           timestamptz NOT NULL,
    valid_to             timestamptz NOT NULL,
    appointed_by_assignment_id uuid NOT NULL,
    action_id            uuid NOT NULL,
    recorded_at          timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_appointment_scope UNIQUE (scope_id, appointment_id),
    CONSTRAINT uq_gov_appointment_commitment_version
        UNIQUE (scope_id, commitment_object_id, appointment_version),
    CONSTRAINT uq_gov_appointment_commitment
        UNIQUE (scope_id, commitment_object_id, appointment_id),
    CONSTRAINT uq_gov_appointment_binding
        UNIQUE (scope_id, appointment_id, appointment_version,
                acceptor_assignment_id, acceptor_principal_id),
    CONSTRAINT fk_gov_appointment_commitment
        FOREIGN KEY (scope_id, commitment_object_id, commitment_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_appointment_acceptor
        FOREIGN KEY (scope_id, acceptor_assignment_id, acceptor_principal_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_appointment_standards
        FOREIGN KEY (scope_id, mission_object_id, standards_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_appointment_appointer
        FOREIGN KEY (scope_id, appointed_by_assignment_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id),
    CONSTRAINT fk_gov_appointment_action
        FOREIGN KEY (scope_id, action_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_gov_appointment_version CHECK (appointment_version >= 1),
    CONSTRAINT ck_gov_appointment_window CHECK (valid_from < valid_to)
);

-- Mutable per-ExecutionCommitment execution state: INDEPENDENT current
-- pointers AND current generations for the current ExecutionAuthority and
-- the current AcceptanceAppointment.  The two gates advance independently; a
-- new authority epoch never implies a new appointment and vice versa.  Both
-- generations start at 0 (no release / no appointment yet) and become 1 at
-- the first release/appointment.  The authority/appointment FKs are
-- scope+commitment-qualified so a pointer can never reference another
-- commitment's grant.
CREATE TABLE IF NOT EXISTS gov_execution_state (
    commitment_object_id uuid PRIMARY KEY,
    scope_id             uuid NOT NULL,
    current_authority_id uuid,
    current_execution_epoch integer NOT NULL DEFAULT 0,
    current_appointment_id uuid,
    current_appointment_version integer NOT NULL DEFAULT 0,
    updated_at           timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_execution_state_scope UNIQUE (scope_id, commitment_object_id),
    CONSTRAINT fk_gov_execution_state_commitment
        FOREIGN KEY (scope_id, commitment_object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_execution_state_authority
        FOREIGN KEY (scope_id, commitment_object_id, current_authority_id)
        REFERENCES gov_execution_authorities (scope_id, commitment_object_id, authority_id),
    CONSTRAINT fk_gov_execution_state_appointment
        FOREIGN KEY (scope_id, commitment_object_id, current_appointment_id)
        REFERENCES gov_acceptance_appointments (scope_id, commitment_object_id, appointment_id),
    CONSTRAINT ck_gov_execution_state_epoch CHECK (current_execution_epoch >= 0),
    CONSTRAINT ck_gov_execution_state_appointment_version
        CHECK (current_appointment_version >= 0),
    CONSTRAINT ck_gov_execution_state_authority_pair CHECK (
        (current_authority_id IS NULL) = (current_execution_epoch = 0)
    ),
    CONSTRAINT ck_gov_execution_state_appointment_pair CHECK (
        (current_appointment_id IS NULL) = (current_appointment_version = 0)
    )
);

-- Mutable A3 WorkItem head.  Pins the exact accepted WorkItem revision, and
-- — via one composite FK into uq_gov_authority_binding — the SAME authority
-- record that carries the governing commitment revision, the receiving epoch
-- and the IC identity.  Holds the current ExecutionPlan pointer (Plan is
-- REQUIRED and published by the IC as immutable revisions plus this current
-- pointer) and the delivery submission pointers.  accepted_by binds the
-- receiving IC principal to the same scoped assignment the reception used.
-- Business criteria live in the frozen WorkItem revision, never in this row.
CREATE TABLE IF NOT EXISTS gov_a3_work_item_state (
    object_id            uuid PRIMARY KEY,
    scope_id             uuid NOT NULL,
    work_item_revision_id uuid NOT NULL,
    commitment_object_id uuid NOT NULL,
    commitment_revision_id uuid NOT NULL,
    authority_id         uuid NOT NULL,
    execution_epoch      integer NOT NULL,
    ic_assignment_id     uuid NOT NULL,
    ic_principal_id      uuid NOT NULL,
    plan_object_id       uuid,
    current_plan_revision_id uuid,
    accepted_by          uuid,
    accepted_at          timestamptz,
    deliverable_object_id uuid,
    submission_seq       integer NOT NULL DEFAULT 0,
    latest_submission_revision_id uuid,
    latest_review_id     uuid,
    updated_at           timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_a3_work_item_state_scope UNIQUE (scope_id, object_id),
    CONSTRAINT uq_gov_a3_work_item_state_revision
        UNIQUE (scope_id, object_id, work_item_revision_id),
    CONSTRAINT fk_gov_a3_work_item_revision
        FOREIGN KEY (scope_id, object_id, work_item_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_a3_work_item_commitment
        FOREIGN KEY (scope_id, commitment_object_id, commitment_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_a3_work_item_authority
        FOREIGN KEY (scope_id, authority_id, commitment_object_id,
                     commitment_revision_id, execution_epoch,
                     ic_assignment_id, ic_principal_id)
        REFERENCES gov_execution_authorities (scope_id, authority_id,
                     commitment_object_id, commitment_revision_id,
                     execution_epoch, ic_assignment_id, ic_principal_id),
    CONSTRAINT fk_gov_a3_work_item_accepted_by
        FOREIGN KEY (scope_id, ic_assignment_id, accepted_by)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_a3_work_item_plan
        FOREIGN KEY (scope_id, plan_object_id, current_plan_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_a3_work_item_submission
        FOREIGN KEY (scope_id, deliverable_object_id, latest_submission_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT ck_gov_a3_work_item_epoch CHECK (execution_epoch >= 1),
    CONSTRAINT ck_gov_a3_work_item_accepted CHECK (
        (accepted_by IS NULL) = (accepted_at IS NULL)
    ),
    CONSTRAINT ck_gov_a3_work_item_plan CHECK (
        (plan_object_id IS NULL) = (current_plan_revision_id IS NULL)
    ),
    CONSTRAINT ck_gov_a3_work_item_submission CHECK (
        (submission_seq = 0 AND deliverable_object_id IS NULL
            AND latest_submission_revision_id IS NULL AND latest_review_id IS NULL)
        OR (submission_seq >= 1 AND accepted_by IS NOT NULL
            AND deliverable_object_id IS NOT NULL
            AND latest_submission_revision_id IS NOT NULL)
    )
);

-- Append-only WorkItem reception records.  Reception is NOT a new What
-- handshake: it binds the exact received WorkItem revision and — via the
-- same composite binding FK — the SAME authority record (commitment
-- revision, epoch, IC identity) the receiving IC holds.  At most one
-- reception per WorkItem.
CREATE TABLE IF NOT EXISTS gov_work_receipts (
    work_receipt_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id             uuid NOT NULL,
    work_item_object_id  uuid NOT NULL,
    work_item_revision_id uuid NOT NULL,
    commitment_object_id uuid NOT NULL,
    commitment_revision_id uuid NOT NULL,
    authority_id         uuid NOT NULL,
    execution_epoch      integer NOT NULL,
    ic_assignment_id     uuid NOT NULL,
    ic_principal_id      uuid NOT NULL,
    action_id            uuid NOT NULL,
    recorded_at          timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_work_receipt_scope UNIQUE (scope_id, work_receipt_id),
    CONSTRAINT uq_gov_work_receipt_work_item UNIQUE (scope_id, work_item_object_id),
    CONSTRAINT fk_gov_work_receipt_work_item
        FOREIGN KEY (scope_id, work_item_object_id, work_item_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_work_receipt_commitment
        FOREIGN KEY (scope_id, commitment_object_id, commitment_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_work_receipt_authority
        FOREIGN KEY (scope_id, authority_id, commitment_object_id,
                     commitment_revision_id, execution_epoch,
                     ic_assignment_id, ic_principal_id)
        REFERENCES gov_execution_authorities (scope_id, authority_id,
                     commitment_object_id, commitment_revision_id,
                     execution_epoch, ic_assignment_id, ic_principal_id),
    CONSTRAINT fk_gov_work_receipt_action
        FOREIGN KEY (scope_id, action_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_gov_work_receipt_epoch CHECK (execution_epoch >= 1)
);

-- Append-only A3 delivery reviews.  Mirrors 0017's exact-version judgment
-- shape but binds — via one composite FK into uq_gov_appointment_binding —
-- the SAME AcceptanceAppointment record (id + version + acceptor identity)
-- under which the independent verifier acted.  UNIQUE (scope_id,
-- deliverable_revision_id) makes a reviewed submission revision terminal: it
-- can never be reviewed again, under no new idempotency key.
CREATE TABLE IF NOT EXISTS gov_a3_delivery_acceptances (
    acceptance_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id             uuid NOT NULL,
    work_item_object_id  uuid NOT NULL,
    work_item_revision_id uuid NOT NULL,
    deliverable_object_id uuid NOT NULL,
    deliverable_revision_id uuid NOT NULL,
    submission_seq       integer NOT NULL,
    payload_hash         text NOT NULL,
    verification_result  text NOT NULL,
    criterion_results    jsonb NOT NULL,
    review_note          text NOT NULL,
    appointment_id       uuid NOT NULL,
    appointment_version  integer NOT NULL,
    verifier_assignment_id uuid NOT NULL,
    verifier_principal_id  uuid NOT NULL,
    action_id            uuid NOT NULL,
    recorded_at          timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_a3_delivery_acceptance_scope UNIQUE (scope_id, acceptance_id),
    CONSTRAINT uq_gov_a3_delivery_acceptance_work_item
        UNIQUE (scope_id, work_item_object_id, acceptance_id),
    CONSTRAINT uq_gov_a3_delivery_acceptance_submission
        UNIQUE (scope_id, deliverable_revision_id),
    CONSTRAINT fk_gov_a3_delivery_acceptance_work_item
        FOREIGN KEY (scope_id, work_item_object_id, work_item_revision_id)
        REFERENCES gov_a3_work_item_state (scope_id, object_id, work_item_revision_id),
    CONSTRAINT fk_gov_a3_delivery_acceptance_deliverable
        FOREIGN KEY (scope_id, deliverable_object_id, deliverable_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_a3_delivery_acceptance_appointment
        FOREIGN KEY (scope_id, appointment_id, appointment_version,
                     verifier_assignment_id, verifier_principal_id)
        REFERENCES gov_acceptance_appointments (scope_id, appointment_id,
                     appointment_version, acceptor_assignment_id,
                     acceptor_principal_id),
    CONSTRAINT fk_gov_a3_delivery_acceptance_receipt
        FOREIGN KEY (scope_id, action_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_gov_a3_delivery_acceptance_seq CHECK (submission_seq >= 1),
    CONSTRAINT ck_gov_a3_delivery_acceptance_hash
        CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_gov_a3_delivery_acceptance_result CHECK (
        verification_result IN ('accepted', 'changes_requested')
    ),
    CONSTRAINT ck_gov_a3_delivery_acceptance_criteria CHECK (
        jsonb_typeof(criterion_results) = 'array' AND jsonb_array_length(criterion_results) >= 1
    ),
    CONSTRAINT ck_gov_a3_delivery_acceptance_appointment_version
        CHECK (appointment_version >= 1),
    CONSTRAINT ck_gov_a3_delivery_acceptance_note CHECK (length(btrim(review_note)) >= 1)
);

DO $a3_latest_review_fk$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_gov_a3_work_item_latest_review'
          AND conrelid = 'gov_a3_work_item_state'::regclass
    ) THEN
        ALTER TABLE gov_a3_work_item_state
            ADD CONSTRAINT fk_gov_a3_work_item_latest_review
            FOREIGN KEY (scope_id, object_id, latest_review_id)
            REFERENCES gov_a3_delivery_acceptances (scope_id, work_item_object_id, acceptance_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END
$a3_latest_review_fk$;

-- Append-only A3 outcome assessments (engineering section 5 fixed mapping):
-- the target is the exact CompanyReference revision adopted by an active
-- composition, never a new generic CompanyOutcome/MetricObservation.  The
-- adopting composition revision, the round period and the server-computed
-- qualified customer count are pinned per row.
CREATE TABLE IF NOT EXISTS gov_a3_outcome_assessments (
    assessment_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id             uuid NOT NULL,
    company_reference_object_id uuid NOT NULL,
    company_reference_revision_id uuid NOT NULL,
    composition_object_id uuid NOT NULL,
    composition_revision_id uuid NOT NULL,
    period_id            uuid NOT NULL,
    assessment_result    text NOT NULL,
    qualified_customer_count integer NOT NULL,
    target_count         integer NOT NULL,
    observation_revision_ids jsonb NOT NULL,
    evidence_revision_ids jsonb NOT NULL,
    delivery_acceptance_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    assessment_note      text NOT NULL,
    assessor_assignment_id uuid NOT NULL,
    assessor_principal_id  uuid NOT NULL,
    action_id            uuid NOT NULL,
    recorded_at          timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_a3_outcome_assessment_scope UNIQUE (scope_id, assessment_id),
    CONSTRAINT fk_gov_a3_outcome_assessment_target
        FOREIGN KEY (scope_id, company_reference_object_id, company_reference_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_a3_outcome_assessment_composition
        FOREIGN KEY (scope_id, composition_object_id, composition_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_a3_outcome_assessment_assessor
        FOREIGN KEY (scope_id, assessor_assignment_id, assessor_principal_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_a3_outcome_assessment_receipt
        FOREIGN KEY (scope_id, action_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_gov_a3_outcome_assessment_result CHECK (
        assessment_result IN ('achieved', 'not_achieved', 'inconclusive')
    ),
    CONSTRAINT ck_gov_a3_outcome_assessment_qualified
        CHECK (qualified_customer_count >= 0),
    CONSTRAINT ck_gov_a3_outcome_assessment_target CHECK (target_count >= 1),
    CONSTRAINT ck_gov_a3_outcome_assessment_observations CHECK (
        jsonb_typeof(observation_revision_ids) = 'array'
        AND jsonb_array_length(observation_revision_ids) >= 1
    ),
    CONSTRAINT ck_gov_a3_outcome_assessment_evidence CHECK (
        jsonb_typeof(evidence_revision_ids) = 'array'
        AND jsonb_array_length(evidence_revision_ids) >= 1
    ),
    CONSTRAINT ck_gov_a3_outcome_assessment_deliveries CHECK (
        jsonb_typeof(delivery_acceptance_ids) = 'array'
    ),
    CONSTRAINT ck_gov_a3_outcome_assessment_note
        CHECK (length(btrim(assessment_note)) >= 1)
);

CREATE INDEX IF NOT EXISTS idx_gov_a3_delivery_acceptances_work_item
    ON gov_a3_delivery_acceptances (scope_id, work_item_object_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_gov_a3_outcome_assessments_target
    ON gov_a3_outcome_assessments (scope_id, company_reference_object_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_gov_execution_authorities_commitment
    ON gov_execution_authorities (scope_id, commitment_object_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_gov_acceptance_appointments_commitment
    ON gov_acceptance_appointments (scope_id, commitment_object_id, recorded_at);

-- JSON arrays cannot carry ordinary FKs.  Resolve each exact reference in its
-- scope, and reject duplicate references rather than treating them as
-- evidence.  The A3 mapping fixes the target type to CompanyReference and
-- the observations/evidence to in-scope EvidenceAsset revisions (the hashed
-- JSON event packets), the delivery references to A3 review rows only.
CREATE OR REPLACE FUNCTION gov_validate_a3_outcome_assessment_refs()
RETURNS trigger LANGUAGE plpgsql AS $gov_validate_a3_outcome_assessment_refs$
DECLARE ref jsonb;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM gov_objects
        WHERE scope_id=NEW.scope_id AND object_id=NEW.company_reference_object_id
          AND object_type='CompanyReference'
    ) THEN
        RAISE EXCEPTION 'a3 outcome assessment must reference an in-scope CompanyReference'
            USING ERRCODE='23503';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM gov_objects
        WHERE scope_id=NEW.scope_id AND object_id=NEW.composition_object_id
          AND object_type='CompanyComposition'
    ) THEN
        RAISE EXCEPTION 'a3 outcome assessment must reference an in-scope CompanyComposition'
            USING ERRCODE='23503';
    END IF;
    IF (SELECT count(*) <> count(DISTINCT value)
        FROM jsonb_array_elements(NEW.observation_revision_ids))
        OR (SELECT count(*) <> count(DISTINCT value)
            FROM jsonb_array_elements(NEW.evidence_revision_ids))
        OR (SELECT count(*) <> count(DISTINCT value)
            FROM jsonb_array_elements(NEW.delivery_acceptance_ids)) THEN
        RAISE EXCEPTION 'a3 outcome assessment references must be unique'
            USING ERRCODE='23514';
    END IF;
    FOR ref IN SELECT value FROM jsonb_array_elements(NEW.observation_revision_ids)
    LOOP
        IF jsonb_typeof(ref) <> 'string' OR NOT EXISTS (
            SELECT 1 FROM gov_object_revisions r
            JOIN gov_objects o ON (o.scope_id,o.object_id)=(r.scope_id,r.object_id)
            WHERE r.scope_id=NEW.scope_id AND r.revision_id=(ref #>> '{}')::uuid
              AND o.object_type='EvidenceAsset'
        ) THEN
            RAISE EXCEPTION 'a3 outcome observation must reference an in-scope evidence revision'
                USING ERRCODE='23503';
        END IF;
    END LOOP;
    FOR ref IN SELECT value FROM jsonb_array_elements(NEW.evidence_revision_ids)
    LOOP
        IF jsonb_typeof(ref) <> 'string' OR NOT EXISTS (
            SELECT 1 FROM gov_object_revisions r
            JOIN gov_objects o ON (o.scope_id,o.object_id)=(r.scope_id,r.object_id)
            WHERE r.scope_id=NEW.scope_id AND r.revision_id=(ref #>> '{}')::uuid
              AND o.object_type='EvidenceAsset'
        ) THEN
            RAISE EXCEPTION 'a3 outcome evidence must reference an in-scope evidence revision'
                USING ERRCODE='23503';
        END IF;
    END LOOP;
    FOR ref IN SELECT value FROM jsonb_array_elements(NEW.delivery_acceptance_ids)
    LOOP
        IF jsonb_typeof(ref) <> 'string' OR NOT EXISTS (
            SELECT 1 FROM gov_a3_delivery_acceptances
            WHERE scope_id=NEW.scope_id AND acceptance_id=(ref #>> '{}')::uuid
        ) THEN
            RAISE EXCEPTION 'a3 outcome delivery reference must be an in-scope a3 acceptance'
                USING ERRCODE='23503';
        END IF;
    END LOOP;
    RETURN NEW;
END
$gov_validate_a3_outcome_assessment_refs$;

DROP TRIGGER IF EXISTS trg_gov_a3_outcome_assessment_refs ON gov_a3_outcome_assessments;
CREATE TRIGGER trg_gov_a3_outcome_assessment_refs
    BEFORE INSERT ON gov_a3_outcome_assessments
    FOR EACH ROW EXECUTE FUNCTION gov_validate_a3_outcome_assessment_refs();

-- ---------------------------------------------------------------------------
-- Row Level Security on the new tables (ENABLE + FORCE; in-scope or control
-- plane for both reads and writes — these are application-writable tables).
-- ---------------------------------------------------------------------------

DO $a3_rls$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_execution_state', 'gov_execution_authorities',
        'gov_acceptance_appointments', 'gov_a3_work_item_state',
        'gov_work_receipts', 'gov_a3_delivery_acceptances',
        'gov_a3_outcome_assessments']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', target);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', target);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', target || '_scope', target);
        EXECUTE format(
            'CREATE POLICY %I ON %I'
            || ' USING (gov_scope_matches(scope_id) OR gov_control_plane_on())'
            || ' WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on())',
            target || '_scope', target);
    END LOOP;
END
$a3_rls$;

-- ---------------------------------------------------------------------------
-- Write fences (0018 functions): every new table requires the runtime write
-- capability for INSERT/UPDATE; the five append-only tables reject
-- UPDATE/DELETE; the two mutable state tables reject DELETE (state may
-- advance, never disappear).
-- ---------------------------------------------------------------------------

DO $a3_capability$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_execution_state', 'gov_execution_authorities',
        'gov_acceptance_appointments', 'gov_a3_work_item_state',
        'gov_work_receipts', 'gov_a3_delivery_acceptances',
        'gov_a3_outcome_assessments']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_write_capability ON %I', target, target);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_write_capability BEFORE INSERT OR UPDATE ON %I'
            || ' FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability()', target, target);
    END LOOP;
END
$a3_capability$;

DO $a3_append_only$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_execution_authorities', 'gov_acceptance_appointments',
        'gov_work_receipts', 'gov_a3_delivery_acceptances',
        'gov_a3_outcome_assessments']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_append_only ON %I', target, target);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_append_only BEFORE UPDATE OR DELETE ON %I'
            || ' FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation()', target, target);
    END LOOP;
END
$a3_append_only$;

DO $a3_no_delete$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_execution_state', 'gov_a3_work_item_state']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_no_delete ON %I', target, target);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_no_delete BEFORE DELETE ON %I'
            || ' FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation()', target, target);
    END LOOP;
END
$a3_no_delete$;

COMMENT ON TABLE gov_execution_state IS
    'Mutable per-ExecutionCommitment execution state: independent current pointers and current generations (0 before first release/appointment) for the current ExecutionAuthority and AcceptanceAppointment; the two gates advance independently.';
COMMENT ON TABLE gov_execution_authorities IS
    'Append-only ExecutionAuthority releases: one per (commitment, execution_epoch), binding an exact IC assignment+principal, the exact commitment revision and an independent validity window.';
COMMENT ON TABLE gov_acceptance_appointments IS
    'Append-only AcceptanceAppointments: one per (commitment, appointment_version), binding an independent acceptor, its acceptance window and the exact Mission standards revision.';
COMMENT ON TABLE gov_a3_work_item_state IS
    'Mutable A3 WorkItem head: exact accepted revision, composite binding to the SAME receiving authority record, current ExecutionPlan pointer and submission pointers; business criteria live only in the frozen WorkItem revision.';
COMMENT ON TABLE gov_work_receipts IS
    'Append-only WorkItem reception records (not a new What handshake): exact received revision plus the SAME receiving authority record; at most one reception per WorkItem.';
COMMENT ON TABLE gov_a3_delivery_acceptances IS
    'Append-only A3 exact-version delivery judgments bound to the SAME AcceptanceAppointment record (id+version+acceptor identity); a reviewed submission revision is terminal and never reviewable again.';
COMMENT ON TABLE gov_a3_outcome_assessments IS
    'Append-only A3 outcome assessments against the exact CompanyReference revision adopted by an active composition; stores evidence-backed event-packet counts, never creates CompanyOutcome/MetricObservation.';
