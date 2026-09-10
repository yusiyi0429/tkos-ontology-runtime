-- 0018_method_protocol: A1 server-side protocol attribution and write fences.
--
-- Contract basis: tkos Contract-A v0.1 (action_contract_ref content_sha256
-- fff438ca5eb3b2c709d9911929bc0d8d393a27b42f0af62d48767a2f65388fd4).  This
-- migration only registers protocol ownership; it implements no A2 composition
-- or A3 handover business action.
--
-- Like 0016/0017 this file creates no roles and no GRANTs.  Test/deploy
-- infrastructure grants precisely:
--   control plane (application role gets SELECT only; installed by owner via
--   the governed control CLI): gov_method_profile_revisions,
--   gov_protocol_policies, gov_protocol_support_registry,
--   gov_protocol_control_events
--   application-writable (SELECT/INSERT; append-only, no UPDATE/DELETE):
--   gov_object_protocol_bindings
--
-- Control plane authorization (SQL-01): gov_control_plane_on() requires BOTH
-- the transaction setting app.gov_control_plane='on' AND session_user being
-- the database owner role (or a member of it).  The GUC alone can never be
-- self-asserted by the ordinary application role into an administrative
-- capability, and legacy broad GRANT scripts cannot reopen control-plane
-- writes because WITH CHECK still evaluates gov_control_plane_on().
--
-- RLS: ENABLE + FORCE on every new table.  Rows are visible inside their scope
-- (app.governed_scope_id) or to a control-plane session.  Inserts into the
-- four control-plane tables always require the control plane; bindings also
-- accept ordinary in-scope application inserts because object creation writes
-- its binding in the same transaction (version 1 only, see the insert gate).
--
-- Database-level stale-binary fences (they defend against pre-A1
-- implementations that do not know the new conventions; they are NOT a
-- security boundary against arbitrary SQL from the application role, and the
-- capability GUC is a compatibility fence — never an administrative grant):
--   1. gov_objects constraint trigger: every object must have a protocol
--      binding by commit.  Pre-A1 writers never insert bindings and fail.
--   2. gov_require_runtime_capability(): writes to governed business tables
--      and governance.dispatch task enqueue/claim require the transaction
--      capability app.runtime_write_capability='tkos-runtime-a1', which only
--      current runtime code sets.
--   3. RESTRICTIVE SELECT policies on gov_scopes / gov_principals /
--      gov_role_assignments / gov_credentials additionally require
--      gov_runtime_capable() (or the control plane).  Every pre-A1 code path
--      must read gov_scopes (evidence upload before its S3 put, dispatch
--      before its HTTP POST, every authenticated action), so pre-A1 binaries
--      lose row visibility there and stop before any external side effect.
--   4. gov_binding_insert_gate(): binding_version>1 requires the control
--      plane, a first binding is rejected when the object already has one,
--      and profile_canonical_hash must match the installed profile row.
--
-- The migration is replay-safe AT THE RUNNER LEVEL: migrate.py records applied
-- file names in schema_migrations, so a second runner execution is a no-op and
-- changes no data.  Raw re-execution of this file's SQL is NOT guaranteed
-- idempotent: the binding insert gate fires before ON CONFLICT and rejects
-- duplicate version-1 inserts by design.  The legacy backfill is idempotent
-- within one applied run (ON CONFLICT DO NOTHING, per-scope iteration with the
-- scope fence set so a non-BYPASSRLS owner sees the full historical object set
-- under FORCE RLS).  Historical payloads, revisions, receipts, events and
-- hashes are never modified.

-- ---------------------------------------------------------------------------
-- Object type: A1 registration sentinel.  A ProtocolSentinel carries no
-- business semantics; it exists so Contract-A bindings can be exercised
-- without fabricating business success.
-- ---------------------------------------------------------------------------

ALTER TABLE gov_objects DROP CONSTRAINT IF EXISTS ck_gov_object_type;
ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_type CHECK (
    object_type IN (
        'CompanyOutcome', 'BusinessCommitment', 'ExecutionCommitment',
        'FeedbackThread', 'ManagementAdjustment', 'Decision',
        'MetricObservation', 'EvidenceAsset', 'WorkItem', 'Deliverable',
        'ProtocolSentinel'
    )
);

-- ---------------------------------------------------------------------------
-- A1 registration tables.
-- ---------------------------------------------------------------------------

-- Immutable per-scope install records of method profiles / interpretation
-- records.  The same (profile_id, revision) can never be replaced; installing
-- identical content again is an idempotent no-op at the control plane, and
-- conflicting content is rejected there before any insert is attempted.
CREATE TABLE IF NOT EXISTS gov_method_profile_revisions (
    profile_revision_row_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id         uuid NOT NULL REFERENCES gov_scopes (scope_id),
    profile_id       text NOT NULL,
    revision         text NOT NULL,
    schema_version   text NOT NULL,
    canonical_hash   text NOT NULL,
    action_contract_ref jsonb,
    record_origin    text NOT NULL,
    experimental     boolean NOT NULL,
    content          jsonb NOT NULL,
    installed_by     text NOT NULL,
    install_reason   text NOT NULL,
    recorded_at      timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_profile_row_scope UNIQUE (scope_id, profile_revision_row_id),
    CONSTRAINT uq_gov_profile_install UNIQUE (scope_id, profile_id, revision),
    CONSTRAINT ck_gov_profile_hash CHECK (canonical_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_gov_profile_content CHECK (jsonb_typeof(content) = 'object'),
    CONSTRAINT ck_gov_profile_origin CHECK (record_origin IN ('synthetic', 'legacy')),
    CONSTRAINT ck_gov_profile_contract_ref CHECK (
        action_contract_ref IS NULL OR jsonb_typeof(action_contract_ref) = 'object'
    )
);

-- Versioned scope/domain protocol policy.  domain_id NULL marks the
-- scope-level default.  The current policy is the greatest policy_seq; old
-- rows are never updated or deleted.
CREATE TABLE IF NOT EXISTS gov_protocol_policies (
    policy_row_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id      uuid NOT NULL REFERENCES gov_scopes (scope_id),
    domain_id     uuid,
    policy_seq    integer NOT NULL,
    content       jsonb NOT NULL,
    recorded_by   text NOT NULL,
    reason        text NOT NULL,
    recorded_at   timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_protocol_policy_row_scope UNIQUE (scope_id, policy_row_id),
    CONSTRAINT fk_gov_protocol_policy_domain
        FOREIGN KEY (scope_id, domain_id)
        REFERENCES gov_domains (scope_id, domain_id),
    CONSTRAINT ck_gov_protocol_policy_seq CHECK (policy_seq >= 1),
    CONSTRAINT ck_gov_protocol_policy_content CHECK (
        jsonb_typeof(content) = 'object' AND content ? 'default_protocol'
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gov_protocol_policy_scope_seq
    ON gov_protocol_policies (scope_id, policy_seq) WHERE domain_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_gov_protocol_policy_domain_seq
    ON gov_protocol_policies (scope_id, domain_id, policy_seq) WHERE domain_id IS NOT NULL;

-- Versioned per-scope support matrix: which contract/object types/actions a
-- protocol may read/create/write here, plus the evidence-upload and
-- maintenance freeze switches.  The current entry is the greatest
-- registry_seq for the protocol.  contract_version is a real column so the
-- runtime can require an exact match with the object binding.
CREATE TABLE IF NOT EXISTS gov_protocol_support_registry (
    registry_row_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id         uuid NOT NULL REFERENCES gov_scopes (scope_id),
    protocol_id      text NOT NULL,
    contract_version text NOT NULL,
    registry_seq     integer NOT NULL,
    content          jsonb NOT NULL,
    recorded_by      text NOT NULL,
    recorded_at      timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_protocol_registry_row_scope UNIQUE (scope_id, registry_row_id),
    CONSTRAINT uq_gov_protocol_registry_seq UNIQUE (scope_id, protocol_id, registry_seq),
    CONSTRAINT ck_gov_protocol_registry_seq CHECK (registry_seq >= 1),
    CONSTRAINT ck_gov_protocol_registry_content CHECK (jsonb_typeof(content) = 'object')
);

-- Append-only audit of every control-plane operation.
CREATE TABLE IF NOT EXISTS gov_protocol_control_events (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id    uuid NOT NULL REFERENCES gov_scopes (scope_id),
    event_type  text NOT NULL,
    detail      jsonb NOT NULL,
    actor       text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_protocol_event_scope UNIQUE (scope_id, event_id),
    CONSTRAINT ck_gov_protocol_event_detail CHECK (jsonb_typeof(detail) = 'object')
);

-- Immutable object protocol bindings with history.  binding_version 1 is
-- written in the same transaction as object creation; later explicit
-- control-plane changes append higher versions (none are exposed in A1).
CREATE TABLE IF NOT EXISTS gov_object_protocol_bindings (
    binding_id       uuid NOT NULL DEFAULT gen_random_uuid(),
    scope_id         uuid NOT NULL,
    object_id        uuid NOT NULL,
    binding_version  integer NOT NULL,
    protocol_id      text NOT NULL,
    contract_version text NOT NULL,
    profile_id       text NOT NULL,
    profile_revision text NOT NULL,
    profile_canonical_hash text NOT NULL,
    record_origin    text NOT NULL,
    run_id           uuid,
    registered_by    text NOT NULL,
    receipt_id       uuid,
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at      timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT pk_gov_object_binding PRIMARY KEY (scope_id, object_id, binding_version),
    CONSTRAINT uq_gov_object_binding_id UNIQUE (scope_id, binding_id),
    CONSTRAINT fk_gov_object_binding_object
        FOREIGN KEY (scope_id, object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_object_binding_profile
        FOREIGN KEY (scope_id, profile_id, profile_revision)
        REFERENCES gov_method_profile_revisions (scope_id, profile_id, revision),
    CONSTRAINT fk_gov_object_binding_receipt
        FOREIGN KEY (scope_id, receipt_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_gov_object_binding_version CHECK (binding_version >= 1),
    CONSTRAINT ck_gov_object_binding_hash CHECK (profile_canonical_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_gov_object_binding_detail CHECK (jsonb_typeof(detail) = 'object')
);

-- ---------------------------------------------------------------------------
-- Append-only enforcement for every new table (defense in depth over grants).
-- gov_reject_mutation() comes from 0016.
-- ---------------------------------------------------------------------------

DO $a1_append_only$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_method_profile_revisions', 'gov_protocol_policies',
        'gov_protocol_support_registry', 'gov_protocol_control_events',
        'gov_object_protocol_bindings']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_append_only ON %I', target, target);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_append_only BEFORE UPDATE OR DELETE ON %I'
            || ' FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation()', target, target);
    END LOOP;
END
$a1_append_only$;

-- ---------------------------------------------------------------------------
-- Control plane / capability predicates.
-- ---------------------------------------------------------------------------

-- Control plane = explicit transaction setting AND the session role really
-- being the database owner (or a member of it).  The plain application role
-- can set the GUC but is not the owner, so self-assertion stays rejected even
-- after legacy scripts broadly GRANT INSERT on the control tables.
CREATE OR REPLACE FUNCTION gov_control_plane_on()
RETURNS boolean
LANGUAGE sql
STABLE
AS $gov_control_plane_on$
    -- Total boolean: an unset/empty GUC must yield FALSE, never SQL NULL
    -- (B08 — callers use IS NOT TRUE guards that must not fail open).
    SELECT NULLIF(current_setting('app.gov_control_plane', true), '')
        IS NOT DISTINCT FROM 'on'
       AND (
            session_user = (SELECT pg_get_userbyid(d.datdba)
                              FROM pg_database d
                             WHERE d.datname = current_database())
            OR pg_has_role(session_user,
                           (SELECT pg_get_userbyid(d.datdba)
                              FROM pg_database d
                             WHERE d.datname = current_database()),
                           'MEMBER')
       )
$gov_control_plane_on$;

-- Runtime capability = this connection runs current A1-aware code.  A
-- compatibility fence against stale pre-A1 binaries only; it grants no
-- authority by itself.
CREATE OR REPLACE FUNCTION gov_runtime_capable()
RETURNS boolean
LANGUAGE sql
STABLE
AS $gov_runtime_capable$
    SELECT NULLIF(current_setting('app.runtime_write_capability', true), '')
        IS NOT DISTINCT FROM 'tkos-runtime-a1'
$gov_runtime_capable$;

-- ---------------------------------------------------------------------------
-- Row Level Security on the new tables.
-- ---------------------------------------------------------------------------

DO $a1_rls$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_method_profile_revisions', 'gov_protocol_policies',
        'gov_protocol_support_registry', 'gov_protocol_control_events',
        'gov_object_protocol_bindings']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', target);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', target);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', target || '_scope', target);
        IF target = 'gov_object_protocol_bindings' THEN
            -- Object creation writes its binding in the same transaction as an
            -- ordinary in-scope application insert; control-plane sessions may
            -- also register bindings explicitly (backfill, sentinel fixtures).
            EXECUTE format(
                'CREATE POLICY %I ON %I'
                || ' USING (gov_scope_matches(scope_id) OR gov_control_plane_on())'
                || ' WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on())',
                target || '_scope', target);
        ELSE
            -- Control-plane tables: readable in scope, writable only through
            -- the control plane (owner-checked above), regardless of GRANTs.
            EXECUTE format(
                'CREATE POLICY %I ON %I'
                || ' USING (gov_scope_matches(scope_id) OR gov_control_plane_on())'
                || ' WITH CHECK (gov_control_plane_on())',
                target || '_scope', target);
        END IF;
    END LOOP;
END
$a1_rls$;

-- ---------------------------------------------------------------------------
-- Stale-binary read fences: RESTRICTIVE SELECT policies AND with the existing
-- permissive scope policies, so rows stay visible only to current-code
-- connections (capability set) or control-plane sessions.  Pre-A1 binaries
-- read empty identity/scope sets and stop before S3/HTTP side effects.
-- ---------------------------------------------------------------------------

DO $a1_read_fence$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_scopes', 'gov_principals', 'gov_role_assignments', 'gov_credentials']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', target || '_runtime_capable', target);
        EXECUTE format(
            'CREATE POLICY %I ON %I AS RESTRICTIVE FOR SELECT TO PUBLIC'
            || ' USING (gov_runtime_capable() OR gov_control_plane_on())',
            target || '_runtime_capable', target);
    END LOOP;
END
$a1_read_fence$;

-- Control-plane visibility for enumeration/backfill: a non-BYPASSRLS owner
-- running a controlled backfill must see every historical scope and object.
-- The restrictive policies above still require the control plane or the
-- capability, so this widening only helps explicit control-plane sessions.
DROP POLICY IF EXISTS gov_scopes_scope ON gov_scopes;
CREATE POLICY gov_scopes_scope ON gov_scopes
    USING (gov_scope_matches(scope_id) OR gov_control_plane_on())
    WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());

DROP POLICY IF EXISTS gov_objects_scope ON gov_objects;
CREATE POLICY gov_objects_scope ON gov_objects
    USING (gov_scope_matches(scope_id) OR gov_control_plane_on())
    WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());

-- ---------------------------------------------------------------------------
-- Stale-binary write fences.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION gov_require_runtime_capability()
RETURNS trigger
LANGUAGE plpgsql
AS $gov_require_runtime_capability$
BEGIN
    IF gov_runtime_capable() IS NOT TRUE THEN
        RAISE EXCEPTION 'governed writes require runtime write capability tkos-runtime-a1; pre-A1 binaries cannot write governed state'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$gov_require_runtime_capability$;

DO $a1_capability$
DECLARE target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'gov_objects', 'gov_object_revisions', 'gov_handshakes',
        'gov_lifecycle_events', 'gov_action_receipts', 'gov_acceptances',
        'gov_delivery_acceptances', 'gov_outcome_assessments',
        'gov_work_item_state', 'gov_feedback_state', 'gov_context_snapshots']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_write_capability ON %I', target, target);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_write_capability BEFORE INSERT OR UPDATE ON %I'
            || ' FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability()', target, target);
    END LOOP;
END
$a1_capability$;

-- The dispatch fence: pre-A1 workers must not enqueue, claim or update
-- governance.dispatch tasks after this migration.
DROP TRIGGER IF EXISTS trg_runtime_tasks_dispatch_capability ON runtime_tasks;
CREATE TRIGGER trg_runtime_tasks_dispatch_capability
    BEFORE INSERT OR UPDATE ON runtime_tasks
    FOR EACH ROW WHEN (NEW.task_type = 'governance.dispatch')
    EXECUTE FUNCTION gov_require_runtime_capability();

-- Binding insert gate: first bindings belong to object creation (or an
-- explicit control-plane backfill of unbound historical objects); higher
-- versions are control-plane-only rebinding; the declared profile hash must
-- match the installed profile row (the FK alone does not cover the hash); and
-- the profile must semantically belong to the binding's protocol — a correct
-- hash of the wrong profile kind is rejected (compiled A1 mapping, incl. the
-- pinned Contract-A main-contract SHA256).
CREATE OR REPLACE FUNCTION gov_binding_insert_gate()
RETURNS trigger
LANGUAGE plpgsql
AS $gov_binding_insert_gate$
DECLARE
    prow record;
BEGIN
    IF NEW.binding_version <> 1 THEN
        IF gov_control_plane_on() IS NOT TRUE THEN
            RAISE EXCEPTION 'rebinding (binding_version>1) requires the control plane'
                USING ERRCODE = '55000';
        END IF;
    ELSIF EXISTS (
        SELECT 1 FROM gov_object_protocol_bindings b
         WHERE b.scope_id = NEW.scope_id AND b.object_id = NEW.object_id
    ) THEN
        RAISE EXCEPTION 'object % already has a protocol binding; rebinding requires the control plane', NEW.object_id
            USING ERRCODE = '55000';
    END IF;
    SELECT p.schema_version, p.content INTO prow
      FROM gov_method_profile_revisions p
     WHERE p.scope_id = NEW.scope_id
       AND p.profile_id = NEW.profile_id
       AND p.revision = NEW.profile_revision
       AND p.canonical_hash = NEW.profile_canonical_hash;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'bound method profile % revision % hash % is not installed in this scope',
            NEW.profile_id, NEW.profile_revision, NEW.profile_canonical_hash
            USING ERRCODE = '23514';
    END IF;
    IF NEW.protocol_id = 'tkos.legacy-governed' AND NEW.contract_version = 'tkos.governed/v0.2' THEN
        -- IS NOT TRUE: any NULL operand (defensive; these columns are NOT
        -- NULL) must reject, never skip the guard (B08).
        IF (NEW.profile_id = 'urn:tkos:legacy:governed-v0.2'
                AND NEW.profile_revision = '0.2.0'
                AND NEW.profile_canonical_hash = '93c5278a70c70af453e2f86c6d2b298caefe15b1e14b736c006c817c8a182598'
                AND prow.schema_version = 'tkos.legacy-interpretation-record/0.2') IS NOT TRUE THEN
            RAISE EXCEPTION 'legacy protocol bindings require the pinned legacy interpretation record'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.contract-a' AND NEW.contract_version = 'tkos.contract-a/0.1' THEN
        -- IS NOT TRUE: a missing/JSON-null action_contract_ref field yields
        -- SQL NULL and must reject, never skip the guard (B08).
        IF (prow.schema_version = 'tkos.profile-core/0.1'
                AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.contract-a'
                AND prow.content->'action_contract_ref'->>'revision' = '0.1'
                AND prow.content->'action_contract_ref'->>'content_sha256'
                    = 'fff438ca5eb3b2c709d9911929bc0d8d393a27b42f0af62d48767a2f65388fd4') IS NOT TRUE THEN
            RAISE EXCEPTION 'contract-a bindings require a ProfileCore bound to the exact main contract bytes'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'protocol % contract version % is not implemented by this schema',
            NEW.protocol_id, NEW.contract_version
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$gov_binding_insert_gate$;

DROP TRIGGER IF EXISTS trg_gov_binding_insert_gate ON gov_object_protocol_bindings;
CREATE TRIGGER trg_gov_binding_insert_gate
    BEFORE INSERT ON gov_object_protocol_bindings
    FOR EACH ROW EXECUTE FUNCTION gov_binding_insert_gate();

-- Every governed object needs a protocol binding by commit time.  DEFERRABLE
-- so the creating transaction can insert the binding after the object row.
CREATE OR REPLACE FUNCTION gov_objects_require_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $gov_objects_require_binding$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM gov_object_protocol_bindings
        WHERE scope_id = NEW.scope_id AND object_id = NEW.object_id
    ) THEN
        RAISE EXCEPTION 'governed object % has no protocol binding; pre-A1 writers cannot create objects', NEW.object_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END
$gov_objects_require_binding$;

DROP TRIGGER IF EXISTS trg_gov_objects_binding_required ON gov_objects;
CREATE CONSTRAINT TRIGGER trg_gov_objects_binding_required
    AFTER INSERT ON gov_objects
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION gov_objects_require_binding();

-- ---------------------------------------------------------------------------
-- Legacy registration backfill for pre-migration scopes/domains/objects.
-- Explicit, per-scope (the scope fence is set for each iteration so the
-- non-BYPASSRLS owner sees the full historical set under FORCE RLS),
-- idempotent, and never rewriting historical payloads, revisions, receipts,
-- events or hashes.  The content literals below are pinned to
-- memory_service_runtime.governed.profile constants (tests assert equality).
-- ---------------------------------------------------------------------------

DO $a1_backfill$
DECLARE
    scope_row record;
    bound_count integer;
BEGIN
    PERFORM set_config('app.gov_control_plane', 'on', true);
    PERFORM set_config('app.runtime_write_capability', 'tkos-runtime-a1', true);
    FOR scope_row IN SELECT scope_id FROM gov_scopes ORDER BY scope_id LOOP
        PERFORM set_config('app.governed_scope_id', scope_row.scope_id::text, true);

        -- Legacy interpretation record.
        INSERT INTO gov_method_profile_revisions
            (scope_id, profile_id, revision, schema_version, canonical_hash,
             action_contract_ref, record_origin, experimental, content,
             installed_by, install_reason)
        VALUES (scope_row.scope_id,
                'urn:tkos:legacy:governed-v0.2', '0.2.0',
                'tkos.legacy-interpretation-record/0.2',
                '93c5278a70c70af453e2f86c6d2b298caefe15b1e14b736c006c817c8a182598',
                NULL, 'legacy', false,
                '{"contract_version": "tkos.governed/v0.2", "corporate_approved": false, "display_name": "Legacy governed runtime v0.2 interpretation record", "experimental": false, "profile_id": "urn:tkos:legacy:governed-v0.2", "profile_kind": "tkos.legacy-interpretation-record", "protocol_id": "tkos.legacy-governed", "record_origin": "legacy", "revision": "0.2.0", "semantics": "Pre-contract-A governed runtime semantics. MISSION_DRI retains its legacy meaning; no IC handover, company composition or MethodProfile interpretation is implied. Historical payloads, receipts and hashes are preserved unchanged.", "source": "runtime baseline 3cd9109d726a9a9069a7960a2f2665ce677785d2 behavior"}'::jsonb,
                'migration-0018',
                'A1 migration legacy registration backfill')
        ON CONFLICT (scope_id, profile_id, revision) DO NOTHING;

        -- Scope-level legacy default policy, seq 1.
        INSERT INTO gov_protocol_policies
            (scope_id, domain_id, policy_seq, content, recorded_by, reason)
        VALUES (scope_row.scope_id, NULL, 1,
                '{"allow_legacy_create": true, "default_contract_version": "tkos.governed/v0.2", "default_profile_ref": {"profile_id": "urn:tkos:legacy:governed-v0.2", "revision": "0.2.0"}, "default_protocol": "tkos.legacy-governed", "experimental": false, "notes": "Pre-A1 scope registered as legacy by migration 0018 or controlled bootstrap.", "record_origin": "legacy"}'::jsonb,
                'migration-0018',
                'A1 migration legacy registration backfill')
        ON CONFLICT (scope_id, policy_seq) WHERE domain_id IS NULL DO NOTHING;

        -- Support registry seq 1: legacy full handlers; Contract-A read-only
        -- (A1 provides metadata/read support only and rejects every business
        -- write).
        INSERT INTO gov_protocol_support_registry
            (scope_id, protocol_id, contract_version, registry_seq, content, recorded_by)
        VALUES (scope_row.scope_id, 'tkos.legacy-governed', 'tkos.governed/v0.2', 1,
                '{"actions": ["accept_commitment", "accept_feedback", "accept_work_item", "activate_commitment", "confirm_adjustment", "confirm_closure", "confirm_decision", "confirm_outcome", "create_object", "investigate_feedback", "propose_revision", "record_acceptance", "record_outcome_assessment", "reopen_feedback", "request_feedback_acceptance", "review_deliverable", "revoke_assignment", "route_feedback", "submit_deliverable"], "can_create": true, "can_read": true, "can_write": true, "evidence_upload": true, "notes": "Legacy v0.2 handlers remain the only executable business handlers in A1.", "object_types": ["BusinessCommitment", "CompanyOutcome", "Decision", "Deliverable", "EvidenceAsset", "ExecutionCommitment", "FeedbackThread", "ManagementAdjustment", "MetricObservation", "WorkItem"], "readonly_compat": ["tkos.governed/v0.2"]}'::jsonb,
                'migration-0018')
        ON CONFLICT (scope_id, protocol_id, registry_seq) DO NOTHING;

        INSERT INTO gov_protocol_support_registry
            (scope_id, protocol_id, contract_version, registry_seq, content, recorded_by)
        VALUES (scope_row.scope_id, 'tkos.contract-a', 'tkos.contract-a/0.1', 1,
                '{"actions": [], "can_create": false, "can_read": true, "can_write": false, "evidence_upload": true, "notes": "Contract-A protocol registered. A1 provides metadata/read support only; business writes are rejected with ACTION_NOT_SUPPORTED_FOR_PROTOCOL.", "object_types": [], "readonly_compat": ["tkos.contract-a/0.1"]}'::jsonb,
                'migration-0018')
        ON CONFLICT (scope_id, protocol_id, registry_seq) DO NOTHING;

        -- Legacy bindings for every pre-existing object in this scope.
        INSERT INTO gov_object_protocol_bindings
            (scope_id, object_id, binding_version, protocol_id, contract_version,
             profile_id, profile_revision, profile_canonical_hash, record_origin,
             run_id, registered_by, receipt_id, detail)
        SELECT o.scope_id, o.object_id, 1,
               'tkos.legacy-governed', 'tkos.governed/v0.2',
               'urn:tkos:legacy:governed-v0.2', '0.2.0',
               '93c5278a70c70af453e2f86c6d2b298caefe15b1e14b736c006c817c8a182598',
               'legacy', NULL, 'migration-0018', NULL,
               '{"registration": "legacy_backfill", "migration": "0018_method_protocol"}'::jsonb
          FROM gov_objects o
         WHERE o.scope_id = scope_row.scope_id
        ON CONFLICT (scope_id, object_id, binding_version) DO NOTHING;
        GET DIAGNOSTICS bound_count = ROW_COUNT;

        -- One audit event per scope, recorded once even on replay; detail
        -- carries the object/binding counts of the first run for review.
        INSERT INTO gov_protocol_control_events (scope_id, event_type, detail, actor)
        SELECT scope_row.scope_id, 'migration_0018_backfill',
               jsonb_build_object(
                   'kind', 'legacy_registration',
                   'migration', '0018_method_protocol',
                   'scope_id', scope_row.scope_id::text,
                   'object_bindings_inserted', bound_count,
                   'object_count', (SELECT count(*) FROM gov_objects o
                                     WHERE o.scope_id = scope_row.scope_id)),
               'migration-0018'
         WHERE NOT EXISTS (
               SELECT 1 FROM gov_protocol_control_events e
                WHERE e.scope_id = scope_row.scope_id
                  AND e.event_type = 'migration_0018_backfill'
                  AND e.detail->>'kind' = 'legacy_registration');
    END LOOP;
    PERFORM set_config('app.governed_scope_id', '', true);
END
$a1_backfill$;

COMMENT ON TABLE gov_method_profile_revisions IS
    'Immutable per-scope method profile / interpretation record installs; identical content re-install is a no-op, conflicting content is rejected by the control plane.';
COMMENT ON TABLE gov_protocol_policies IS
    'Versioned scope/domain creation policy; domain overrides scope; current row is the greatest policy_seq.';
COMMENT ON TABLE gov_protocol_support_registry IS
    'Versioned per-scope protocol support matrix incl. create/write/evidence freeze switches; current row is the greatest registry_seq.';
COMMENT ON TABLE gov_protocol_control_events IS
    'Append-only audit of controlled protocol registration operations.';
COMMENT ON TABLE gov_object_protocol_bindings IS
    'Immutable object protocol bindings with versioned history; one binding is written in the object creation transaction.';
