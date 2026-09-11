-- 0019_company_composition: A2 company composition (Contract-A) schema.
--
-- Contract basis: tkos Contract-A v0.1 (action_contract_ref content_sha256
-- fff438ca5eb3b2c709d9911929bc0d8d393a27b42f0af62d48767a2f65388fd4) and
-- docs/runtime-a2-api.md (interface spec incl. the 14 interface-review
-- corrections).  This migration adds the seven A2 object types, the 'formed'
-- lifecycle status, and the five composition tables.  It implements no
-- business handler by itself; the runtime handlers enforce every rule above
-- these constraints.
--
-- Like 0016/0017/0018 this file creates no roles and no GRANTs.  Test/deploy
-- infrastructure grants precisely:
--   application-writable, mutable head/pointer rows (SELECT/INSERT/UPDATE;
--   no DELETE):
--     gov_formation_round_state, gov_round_formal_submissions
--   application-writable, append-only (SELECT/INSERT; no UPDATE/DELETE):
--     gov_composition_confirmations, gov_mission_index, gov_activation_records
--
-- Definitional content (member set, period, adopted profile/reference
-- revisions, submission content, manifests) lives exclusively in immutable
-- gov_object_revisions payloads.  The tables below hold only mutable
-- head/pointer state and append-only records; each carries scope_id and uses
-- scope-qualified composite FKs so an independent oracle can audit cross-table
-- consistency inside one scope.
--
-- RLS: ENABLE + FORCE on every new table.  Rows are visible inside their scope
-- (app.governed_scope_id) or to a control-plane session, and writes go through
-- the same predicate.  Every new table is covered by the
-- gov_require_runtime_capability() write fence (INSERT/UPDATE), the three
-- append-only tables additionally by gov_reject_mutation() (UPDATE/DELETE),
-- and the two mutable tables by gov_reject_mutation() on DELETE (head/pointer
-- rows may be replaced by newer revisions of state, never removed).
--
-- The migration is replay-safe AT THE RUNNER LEVEL: migrate.py records applied
-- file names in schema_migrations, so a second runner execution is a no-op and
-- changes no data.  Raw re-execution of this file's SQL is written to be
-- idempotent (IF NOT EXISTS / DROP ... IF EXISTS + CREATE), but the runner
-- record remains the only supported replay mechanism.  No historical payloads,
-- revisions, receipts, events or hashes are touched; there is no backfill.

-- ---------------------------------------------------------------------------
-- Object types: the seven A2 composition types.  Only CompanyReference and
-- CapacityObservation are accepted through the generic create_object /
-- propose_revision input path under Contract-A; the other five are created or
-- derived exclusively inside the six A2 actions (runtime-enforced, docs
-- runtime-a2-api.md section 1).
-- ---------------------------------------------------------------------------

ALTER TABLE gov_objects DROP CONSTRAINT IF EXISTS ck_gov_object_type;
ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_type CHECK (
    object_type IN (
        'CompanyOutcome', 'BusinessCommitment', 'ExecutionCommitment',
        'FeedbackThread', 'ManagementAdjustment', 'Decision',
        'MetricObservation', 'EvidenceAsset', 'WorkItem', 'Deliverable',
        'ProtocolSentinel',
        'CompanyReference', 'CapacityObservation', 'FormationRound',
        'DomainSubmission', 'CompanyComposition', 'Mission', 'DomainCommitment'
    )
);

-- Lifecycle: 'submitted' already exists since 0017 (Deliverable) and is reused
-- for DomainSubmission; 'formed' is the only new status (CompanyComposition
-- between formation and activation).
ALTER TABLE gov_objects DROP CONSTRAINT IF EXISTS ck_gov_object_status;
ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_status CHECK (
    lifecycle_status IN (
        'draft', 'offered', 'proposed', 'open', 'routed', 'accepted',
        'investigating', 'awaiting_acceptance', 'active', 'superseded',
        'applied', 'confirmed', 'closed', 'dismissed', 'recorded', 'stored',
        'in_progress', 'submitted', 'changes_requested', 'delivery_accepted',
        'formed'
    )
);

-- ---------------------------------------------------------------------------
-- A2 composition tables.
-- ---------------------------------------------------------------------------

-- Mutable FormationRound head.  One open round per (company, period): the
-- second open for the same pair is rejected at creation by the unique
-- constraint below.  Generations: member_set_version increments on member-set
-- or adopted-reference change (round definition revision); input_set_version
-- increments on every formal submission / member / reference change that
-- invalidates pending compositions.
CREATE TABLE IF NOT EXISTS gov_formation_round_state (
    object_id          uuid PRIMARY KEY,
    scope_id           uuid NOT NULL,
    company_id         uuid NOT NULL,
    company_domain_id  uuid NOT NULL,
    period_id          uuid NOT NULL,
    member_set_version integer NOT NULL,
    input_set_version  integer NOT NULL,
    activated_composition_object_id  uuid,
    activated_composition_revision_id uuid,
    activated_at       timestamptz,
    updated_at         timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_round_state_scope UNIQUE (scope_id, object_id),
    CONSTRAINT fk_gov_round_state_object
        FOREIGN KEY (scope_id, object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_round_state_company_domain
        FOREIGN KEY (scope_id, company_domain_id)
        REFERENCES gov_domains (scope_id, domain_id),
    CONSTRAINT fk_gov_round_state_activated
        FOREIGN KEY (scope_id, activated_composition_object_id,
                     activated_composition_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT uq_gov_round_state_company_period
        UNIQUE (scope_id, company_id, period_id),
    CONSTRAINT ck_gov_round_state_member_set_version CHECK (member_set_version >= 1),
    CONSTRAINT ck_gov_round_state_input_set_version CHECK (input_set_version >= 1),
    CONSTRAINT ck_gov_round_state_activated_pair CHECK (
        (activated_composition_object_id IS NULL)
        = (activated_composition_revision_id IS NULL)
    )
);

-- Mutable pointer: the current formal DomainSubmission of each member domain
-- of a round.  Republishing replaces the pointer (row UPDATE) while the old
-- submission revisions stay immutable in gov_object_revisions.
CREATE TABLE IF NOT EXISTS gov_round_formal_submissions (
    scope_id          uuid NOT NULL,
    round_object_id   uuid NOT NULL,
    domain_id         uuid NOT NULL,
    submission_object_id  uuid NOT NULL,
    submission_revision_id uuid NOT NULL,
    published_by_principal_id  uuid NOT NULL,
    published_by_assignment_id uuid NOT NULL,
    action_id         uuid,
    published_at      timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT pk_gov_round_submission
        PRIMARY KEY (scope_id, round_object_id, domain_id),
    CONSTRAINT fk_gov_round_submission_round
        FOREIGN KEY (scope_id, round_object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_round_submission_domain
        FOREIGN KEY (scope_id, domain_id)
        REFERENCES gov_domains (scope_id, domain_id),
    CONSTRAINT fk_gov_round_submission_submission
        FOREIGN KEY (scope_id, submission_object_id, submission_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_round_submission_publisher
        FOREIGN KEY (scope_id, published_by_assignment_id, published_by_principal_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_round_submission_action
        FOREIGN KEY (scope_id, action_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED
);

-- Append-only confirmations of one exact composition revision (manifest hash
-- pinned per row).  Double uniqueness: one vote per assignment slot AND one
-- vote per natural person per composition revision, so the same principal
-- cannot obtain a second vote through another assignment (review 4).
CREATE TABLE IF NOT EXISTS gov_composition_confirmations (
    confirmation_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id         uuid NOT NULL,
    composition_object_id  uuid NOT NULL,
    composition_revision_id uuid NOT NULL,
    manifest_hash    text NOT NULL,
    principal_id     uuid NOT NULL,
    assignment_id    uuid NOT NULL,
    responsibility_role text NOT NULL,
    confirmation_statement text NOT NULL,
    action_id        uuid,
    recorded_at      timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_confirmation_scope UNIQUE (scope_id, confirmation_id),
    CONSTRAINT fk_gov_confirmation_composition
        FOREIGN KEY (scope_id, composition_object_id, composition_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_confirmation_signer
        FOREIGN KEY (scope_id, assignment_id, principal_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_confirmation_action
        FOREIGN KEY (scope_id, action_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT uq_gov_confirmation_assignment
        UNIQUE (scope_id, composition_object_id, composition_revision_id, assignment_id),
    CONSTRAINT uq_gov_confirmation_principal
        UNIQUE (scope_id, composition_object_id, composition_revision_id, principal_id),
    CONSTRAINT ck_gov_confirmation_manifest_hash
        CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_gov_confirmation_role
        CHECK (responsibility_role IN ('company_decider', 'area_accountable')),
    CONSTRAINT ck_gov_confirmation_statement CHECK (
        confirmation_statement = btrim(confirmation_statement)
        AND length(confirmation_statement) BETWEEN 1 AND 2000
    )
);

-- Append-only stable identity index: (round, domain, mission_key) resolves to
-- the server-assigned Mission object.  Formal resubmission appends a new
-- Mission revision; the identity row never moves.  This is an identity index,
-- NOT the activation list (activation consults the manifest submission refs).
CREATE TABLE IF NOT EXISTS gov_mission_index (
    scope_id         uuid NOT NULL,
    round_object_id  uuid NOT NULL,
    domain_id        uuid NOT NULL,
    mission_key      text NOT NULL,
    mission_object_id uuid NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT pk_gov_mission_index
        PRIMARY KEY (scope_id, round_object_id, domain_id, mission_key),
    CONSTRAINT fk_gov_mission_index_round
        FOREIGN KEY (scope_id, round_object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_mission_index_domain
        FOREIGN KEY (scope_id, domain_id)
        REFERENCES gov_domains (scope_id, domain_id),
    CONSTRAINT fk_gov_mission_index_mission
        FOREIGN KEY (scope_id, mission_object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT ck_gov_mission_index_key CHECK (
        mission_key = btrim(mission_key) AND length(mission_key) BETWEEN 1 AND 200
    )
);

-- Append-only activation records.  At most one activation per round; the
-- record pins the exact composition revision, its manifest hash and both
-- generations it was checked against.  detail carries the DomainCommitment /
-- Mission effectivation mapping for audit and read projection.
CREATE TABLE IF NOT EXISTS gov_activation_records (
    activation_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id         uuid NOT NULL,
    round_object_id  uuid NOT NULL,
    composition_object_id  uuid NOT NULL,
    composition_revision_id uuid NOT NULL,
    manifest_hash    text NOT NULL,
    member_set_version integer NOT NULL,
    input_set_version  integer NOT NULL,
    detail           jsonb NOT NULL,
    action_id        uuid,
    activated_at     timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_activation_scope UNIQUE (scope_id, activation_id),
    CONSTRAINT uq_gov_activation_round UNIQUE (scope_id, round_object_id),
    CONSTRAINT fk_gov_activation_round
        FOREIGN KEY (scope_id, round_object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_activation_composition
        FOREIGN KEY (scope_id, composition_object_id, composition_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_activation_action
        FOREIGN KEY (scope_id, action_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_gov_activation_manifest_hash
        CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_gov_activation_member_set_version CHECK (member_set_version >= 1),
    CONSTRAINT ck_gov_activation_input_set_version CHECK (input_set_version >= 1),
    CONSTRAINT ck_gov_activation_detail CHECK (jsonb_typeof(detail) = 'object')
);

-- ---------------------------------------------------------------------------
-- Row Level Security on the new tables (ENABLE + FORCE; in-scope or control
-- plane for both reads and writes — these are application-writable tables).
-- ---------------------------------------------------------------------------

DO $a2_rls$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_formation_round_state', 'gov_round_formal_submissions',
        'gov_composition_confirmations', 'gov_mission_index',
        'gov_activation_records']
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
$a2_rls$;

-- ---------------------------------------------------------------------------
-- Write fences (0018 functions): every new table requires the runtime write
-- capability for INSERT/UPDATE; append-only tables reject UPDATE/DELETE; the
-- two mutable tables reject DELETE (state may advance, never disappear).
-- ---------------------------------------------------------------------------

DO $a2_capability$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_formation_round_state', 'gov_round_formal_submissions',
        'gov_composition_confirmations', 'gov_mission_index',
        'gov_activation_records']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_write_capability ON %I', target, target);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_write_capability BEFORE INSERT OR UPDATE ON %I'
            || ' FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability()', target, target);
    END LOOP;
END
$a2_capability$;

DO $a2_append_only$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_composition_confirmations', 'gov_mission_index',
        'gov_activation_records']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_append_only ON %I', target, target);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_append_only BEFORE UPDATE OR DELETE ON %I'
            || ' FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation()', target, target);
    END LOOP;
END
$a2_append_only$;

DO $a2_no_delete$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_formation_round_state', 'gov_round_formal_submissions']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_no_delete ON %I', target, target);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_no_delete BEFORE DELETE ON %I'
            || ' FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation()', target, target);
    END LOOP;
END
$a2_no_delete$;

COMMENT ON TABLE gov_formation_round_state IS
    'Mutable FormationRound head: one round per (scope, company, period); member/input generations and the activated composition pointer. Definitional content lives in immutable gov_object_revisions payloads.';
COMMENT ON TABLE gov_round_formal_submissions IS
    'Mutable pointer to each member domain''s current formal DomainSubmission of a round; republication replaces the pointer, old submission revisions stay immutable.';
COMMENT ON TABLE gov_composition_confirmations IS
    'Append-only signer confirmations of one exact composition revision (manifest hash pinned); one vote per assignment and per principal per revision.';
COMMENT ON TABLE gov_mission_index IS
    'Append-only identity index mapping (round, domain, mission_key) to the stable Mission object; not the activation list.';
COMMENT ON TABLE gov_activation_records IS
    'Append-only activation records: at most one per round, pinning composition revision, manifest hash and both checked generations.';
