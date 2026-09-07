-- 0016_governed_runtime: governed runtime business kernel (runtime-independent gate).
--
-- This migration creates the governed business model only.  It deliberately does
-- NOT create database roles and does NOT GRANT to any role: the migration must stay
-- independent of a specific test or deployment role.  Test/deploy infrastructure
-- grants precisely, using the mutable vs append-only classification below:
--
--   mutable (SELECT/INSERT/UPDATE, no DELETE):
--     gov_scopes (auth_epoch fence), gov_principals (active), gov_credentials
--     (revoked_at), gov_role_assignments (active), gov_objects (head pointers,
--     lifecycle_status, object_version, processing_cycle_id), gov_feedback_state
--   append-only (SELECT/INSERT only; UPDATE/DELETE rejected by trigger):
--     gov_domains, gov_activation_policies, gov_object_revisions, gov_handshakes,
--     gov_lifecycle_events, gov_action_receipts, gov_acceptances,
--     gov_context_snapshots
--
-- Row Level Security: every table is ENABLE + FORCE ROW LEVEL SECURITY.  Business
-- rows are visible only when the transaction-local setting app.governed_scope_id
-- equals the row scope.  Credential resolution happens before that setting exists,
-- so gov_credentials is instead fenced by app.governed_credential_digest: a
-- credential row is visible only to the digest being authenticated.  The runtime
-- DB user must not be superuser, table owner, or BYPASSRLS.
--
-- Scope mapping: gov_scopes.scope_id is the public UUID; it maps to
-- (tenant_id, company_id).  Infrastructure runtime_tasks.organization_id is
-- explicitly the scope company_id (tenant_id = scope tenant_id).

CREATE TABLE gov_scopes (
    scope_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text NOT NULL,
    company_id      text NOT NULL,
    auth_epoch      bigint NOT NULL DEFAULT 1,
    created_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gov_scope_tenant_company UNIQUE (tenant_id, company_id),
    CONSTRAINT ck_gov_scope_ids CHECK (
        tenant_id = btrim(tenant_id) AND length(tenant_id) BETWEEN 1 AND 200
        AND company_id = btrim(company_id) AND length(company_id) BETWEEN 1 AND 200
    ),
    CONSTRAINT ck_gov_scope_auth_epoch CHECK (auth_epoch >= 1)
);

CREATE TABLE gov_principals (
    principal_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id        uuid NOT NULL REFERENCES gov_scopes (scope_id),
    principal_type  text NOT NULL,
    display_name    text NOT NULL,
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gov_principal_scope UNIQUE (scope_id, principal_id),
    CONSTRAINT ck_gov_principal_type CHECK (principal_type IN ('human', 'agent')),
    CONSTRAINT ck_gov_principal_name CHECK (
        display_name = btrim(display_name) AND length(display_name) BETWEEN 1 AND 200
    )
);

CREATE TABLE gov_credentials (
    credential_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id          uuid NOT NULL,
    principal_id      uuid NOT NULL,
    credential_digest text NOT NULL,
    label             text NOT NULL DEFAULT '',
    created_at        timestamptz NOT NULL DEFAULT now(),
    revoked_at        timestamptz,

    CONSTRAINT uq_gov_credential_digest UNIQUE (credential_digest),
    CONSTRAINT fk_gov_credential_principal
        FOREIGN KEY (scope_id, principal_id)
        REFERENCES gov_principals (scope_id, principal_id),
    CONSTRAINT ck_gov_credential_digest CHECK (
        credential_digest ~ '^[0-9a-f]{64}$'
    )
);

CREATE TABLE gov_domains (
    domain_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id    uuid NOT NULL REFERENCES gov_scopes (scope_id),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gov_domain_scope UNIQUE (scope_id, domain_id),
    CONSTRAINT uq_gov_domain_name UNIQUE (scope_id, name),
    CONSTRAINT ck_gov_domain_name CHECK (
        name = btrim(name) AND length(name) BETWEEN 1 AND 200
    )
);

CREATE TABLE gov_role_assignments (
    assignment_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id       uuid NOT NULL,
    principal_id   uuid NOT NULL,
    domain_id      uuid NOT NULL,
    role           text NOT NULL,
    active         boolean NOT NULL DEFAULT true,
    valid_from     timestamptz NOT NULL DEFAULT now(),
    valid_to       timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gov_assignment_scope UNIQUE (scope_id, assignment_id),
    CONSTRAINT uq_gov_assignment_principal_ref UNIQUE (scope_id, assignment_id, principal_id),
    CONSTRAINT fk_gov_assignment_principal
        FOREIGN KEY (scope_id, principal_id)
        REFERENCES gov_principals (scope_id, principal_id),
    CONSTRAINT fk_gov_assignment_domain
        FOREIGN KEY (scope_id, domain_id)
        REFERENCES gov_domains (scope_id, domain_id),
    CONSTRAINT ck_gov_assignment_role CHECK (
        role IN ('CEO', 'DOMAIN_DRI', 'MISSION_DRI', 'VERIFIER', 'IC', 'AGENT')
    ),
    CONSTRAINT ck_gov_assignment_validity CHECK (
        valid_to IS NULL OR valid_to > valid_from
    )
);

CREATE INDEX idx_gov_assignments_principal
    ON gov_role_assignments (scope_id, principal_id)
    WHERE active;

-- Activation policies are stored, versioned rows; the current policy for a domain
-- is the greatest policy_seq.  Rows are immutable.
CREATE TABLE gov_activation_policies (
    policy_revision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id       uuid NOT NULL,
    domain_id      uuid NOT NULL,
    policy_id      uuid NOT NULL,
    policy_seq     integer NOT NULL,
    content        jsonb NOT NULL,
    recorded_by    uuid,
    recorded_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gov_policy_scope UNIQUE (scope_id, policy_revision_id),
    CONSTRAINT fk_gov_policy_domain
        FOREIGN KEY (scope_id, domain_id)
        REFERENCES gov_domains (scope_id, domain_id),
    CONSTRAINT uq_gov_policy_seq UNIQUE (scope_id, policy_id, policy_seq),
    CONSTRAINT uq_gov_policy_domain_seq UNIQUE (scope_id, domain_id, policy_seq),
    CONSTRAINT fk_gov_policy_author FOREIGN KEY (scope_id, recorded_by)
        REFERENCES gov_principals (scope_id, principal_id),
    CONSTRAINT ck_gov_policy_seq CHECK (policy_seq >= 1),
    CONSTRAINT ck_gov_policy_content CHECK (
        jsonb_typeof(content) = 'object' AND content ? 'action_roles'
        AND jsonb_typeof(content->'action_roles') = 'object'
    )
);

CREATE TABLE gov_objects (
    object_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id             uuid NOT NULL REFERENCES gov_scopes (scope_id),
    domain_id            uuid NOT NULL,
    object_type          text NOT NULL,
    lifecycle_status     text NOT NULL,
    object_version       integer NOT NULL DEFAULT 1,
    latest_revision_id   uuid,
    effective_revision_id uuid,
    processing_cycle_id  uuid NOT NULL DEFAULT gen_random_uuid(),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gov_object_scope UNIQUE (scope_id, object_id),
    CONSTRAINT fk_gov_object_domain
        FOREIGN KEY (scope_id, domain_id)
        REFERENCES gov_domains (scope_id, domain_id),
    CONSTRAINT ck_gov_object_type CHECK (
        object_type IN (
            'CompanyOutcome', 'BusinessCommitment', 'ExecutionCommitment',
            'FeedbackThread', 'ManagementAdjustment', 'Decision',
            'MetricObservation', 'EvidenceAsset'
        )
    ),
    CONSTRAINT ck_gov_object_status CHECK (
        lifecycle_status IN (
            'draft', 'offered', 'proposed', 'open', 'routed', 'accepted',
            'investigating', 'awaiting_acceptance', 'active', 'superseded',
            'applied', 'confirmed', 'closed', 'dismissed', 'recorded', 'stored'
        )
    ),
    CONSTRAINT ck_gov_object_version CHECK (object_version >= 1)
);

CREATE INDEX idx_gov_objects_domain ON gov_objects (scope_id, domain_id);

CREATE TABLE gov_object_revisions (
    revision_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id       uuid NOT NULL,
    object_id      uuid NOT NULL,
    object_version integer NOT NULL,
    payload        jsonb NOT NULL,
    payload_hash   text NOT NULL,
    bundle_id      uuid,
    recorded_by    uuid NOT NULL,
    action_id      uuid,
    valid_from     timestamptz NOT NULL DEFAULT clock_timestamp(),
    valid_to       timestamptz,
    recorded_at    timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_revision_scope UNIQUE (scope_id, revision_id),
    CONSTRAINT uq_gov_revision_object_ref UNIQUE (scope_id, object_id, revision_id),
    CONSTRAINT uq_gov_revision_version
        UNIQUE (scope_id, object_id, object_version),
    CONSTRAINT fk_gov_revision_object
        FOREIGN KEY (scope_id, object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_revision_author FOREIGN KEY (scope_id, recorded_by)
        REFERENCES gov_principals (scope_id, principal_id),
    CONSTRAINT fk_gov_revision_bundle FOREIGN KEY (scope_id, bundle_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT ck_gov_revision_validity CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT ck_gov_revision_version CHECK (object_version >= 1),
    CONSTRAINT ck_gov_revision_hash CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_gov_revision_payload CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX idx_gov_revisions_object
    ON gov_object_revisions (scope_id, object_id, recorded_at);

-- Object head self-references, added after both tables exist.
ALTER TABLE gov_objects
    ADD CONSTRAINT fk_gov_object_latest_revision
        FOREIGN KEY (scope_id, object_id, latest_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    ADD CONSTRAINT fk_gov_object_effective_revision
        FOREIGN KEY (scope_id, object_id, effective_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id);

CREATE TABLE gov_handshakes (
    handshake_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id       uuid NOT NULL,
    object_id      uuid NOT NULL,
    revision_id    uuid NOT NULL,
    assignment_id  uuid NOT NULL,
    principal_id   uuid NOT NULL,
    terms_hash     text NOT NULL,
    understanding  text NOT NULL,
    action_id      uuid,
    recorded_at    timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_handshake_scope UNIQUE (scope_id, handshake_id),
    CONSTRAINT uq_gov_handshake_vote
        UNIQUE (scope_id, object_id, revision_id, assignment_id),
    CONSTRAINT fk_gov_handshake_revision
        FOREIGN KEY (scope_id, object_id, revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_handshake_assignment
        FOREIGN KEY (scope_id, assignment_id, principal_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT ck_gov_handshake_hash CHECK (terms_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE gov_lifecycle_events (
    event_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id     uuid NOT NULL,
    object_id    uuid NOT NULL,
    event_type   text NOT NULL,
    from_status  text,
    to_status    text NOT NULL,
    action_id    uuid,
    principal_id uuid NOT NULL,
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at  timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT fk_gov_event_object
        FOREIGN KEY (scope_id, object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_event_principal FOREIGN KEY (scope_id, principal_id)
        REFERENCES gov_principals (scope_id, principal_id),
    CONSTRAINT ck_gov_event_detail CHECK (jsonb_typeof(detail) = 'object')
);

CREATE INDEX idx_gov_events_object
    ON gov_lifecycle_events (scope_id, object_id, recorded_at);

CREATE TABLE gov_action_receipts (
    receipt_id       uuid PRIMARY KEY,
    scope_id         uuid NOT NULL REFERENCES gov_scopes (scope_id),
    principal_id     uuid NOT NULL,
    idempotency_key  text NOT NULL,
    request_hash     text NOT NULL,
    action_type      text NOT NULL,
    auth_epoch       bigint NOT NULL,
    status           text NOT NULL DEFAULT 'committed',
    result           jsonb NOT NULL,
    object_versions  jsonb NOT NULL,
    effect_task_ids  jsonb NOT NULL DEFAULT '[]'::jsonb,
    target_object_id uuid,
    recorded_at      timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_receipt_scope UNIQUE (scope_id, receipt_id),
    CONSTRAINT fk_gov_receipt_principal FOREIGN KEY (scope_id, principal_id)
        REFERENCES gov_principals (scope_id, principal_id),
    CONSTRAINT fk_gov_receipt_target FOREIGN KEY (scope_id, target_object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT uq_gov_receipt_idempotency
        UNIQUE (scope_id, principal_id, idempotency_key),
    CONSTRAINT ck_gov_receipt_key CHECK (
        idempotency_key = btrim(idempotency_key)
        AND length(idempotency_key) BETWEEN 16 AND 128
    ),
    CONSTRAINT ck_gov_receipt_hash CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_gov_receipt_status CHECK (status = 'committed'),
    CONSTRAINT ck_gov_receipt_result CHECK (jsonb_typeof(result) = 'object'),
    CONSTRAINT ck_gov_receipt_versions CHECK (jsonb_typeof(object_versions) = 'array'),
    CONSTRAINT ck_gov_receipt_effects CHECK (jsonb_typeof(effect_task_ids) = 'array')
);

-- Mutable per-cycle processing state for FeedbackThread objects.  Cycle identity
-- invalidates earlier acceptances/closures after reopen.
CREATE TABLE gov_feedback_state (
    object_id                 uuid PRIMARY KEY,
    scope_id                  uuid NOT NULL,
    cycle_id                  uuid NOT NULL,
    assignee_assignment_id    uuid,
    resolution_source         text,
    resolution_decision_revision_id uuid,
    resolution_adjustment_object_id uuid,
    resolution_adjustment_receipt_id uuid,
    updated_at                timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gov_feedback_state_scope UNIQUE (scope_id, object_id),
    CONSTRAINT fk_gov_feedback_state_object
        FOREIGN KEY (scope_id, object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_feedback_assignee FOREIGN KEY (scope_id, assignee_assignment_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id),
    CONSTRAINT fk_gov_feedback_decision FOREIGN KEY (scope_id, resolution_decision_revision_id)
        REFERENCES gov_object_revisions (scope_id, revision_id),
    CONSTRAINT fk_gov_feedback_adjustment FOREIGN KEY (scope_id, resolution_adjustment_object_id)
        REFERENCES gov_objects (scope_id, object_id),
    CONSTRAINT fk_gov_feedback_receipt FOREIGN KEY (scope_id, resolution_adjustment_receipt_id)
        REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_gov_feedback_source CHECK (
        resolution_source IS NULL
        OR resolution_source IN ('adjustment', 'decision')
    )
);

CREATE TABLE gov_acceptances (
    acceptance_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id           uuid NOT NULL,
    feedback_object_id uuid NOT NULL,
    cycle_id           uuid NOT NULL,
    feedback_revision_id uuid NOT NULL,
    decision_revision_id uuid NOT NULL,
    evidence_revision_ids jsonb NOT NULL,
    verification_result text NOT NULL,
    verifier_assignment_id uuid NOT NULL,
    verifier_principal_id uuid NOT NULL,
    action_id          uuid,
    recorded_at        timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_acceptance_scope UNIQUE (scope_id, acceptance_id),
    CONSTRAINT fk_gov_acceptance_feedback
        FOREIGN KEY (scope_id, feedback_object_id, feedback_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    CONSTRAINT fk_gov_acceptance_decision FOREIGN KEY (scope_id, decision_revision_id)
        REFERENCES gov_object_revisions (scope_id, revision_id),
    CONSTRAINT fk_gov_acceptance_verifier
        FOREIGN KEY (scope_id, verifier_assignment_id, verifier_principal_id)
        REFERENCES gov_role_assignments (scope_id, assignment_id, principal_id),
    CONSTRAINT ck_gov_acceptance_result CHECK (
        verification_result IN ('accepted', 'changes_requested')
    ),
    CONSTRAINT ck_gov_acceptance_evidence CHECK (
        jsonb_typeof(evidence_revision_ids) = 'array'
    )
);

CREATE TABLE gov_context_snapshots (
    snapshot_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id     uuid NOT NULL REFERENCES gov_scopes (scope_id),
    principal_id uuid NOT NULL,
    valid_at     timestamptz NOT NULL,
    known_at     timestamptz NOT NULL,
    selected     jsonb NOT NULL,
    excluded     jsonb NOT NULL DEFAULT '[]'::jsonb,
    recorded_at  timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_gov_snapshot_scope UNIQUE (scope_id, snapshot_id),
    CONSTRAINT fk_gov_snapshot_principal FOREIGN KEY (scope_id, principal_id)
        REFERENCES gov_principals (scope_id, principal_id),
    CONSTRAINT ck_gov_snapshot_selected CHECK (jsonb_typeof(selected) = 'array'),
    CONSTRAINT ck_gov_snapshot_excluded CHECK (jsonb_typeof(excluded) = 'array')
);

-- Commands reserve the receipt UUID before mutations and insert the immutable
-- receipt last in the same transaction. These FKs are checked at commit.
ALTER TABLE gov_object_revisions ADD CONSTRAINT fk_gov_revision_receipt
    FOREIGN KEY (scope_id, action_id) REFERENCES gov_action_receipts (scope_id, receipt_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE gov_handshakes ADD CONSTRAINT fk_gov_handshake_receipt
    FOREIGN KEY (scope_id, action_id) REFERENCES gov_action_receipts (scope_id, receipt_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE gov_lifecycle_events ADD CONSTRAINT fk_gov_event_receipt
    FOREIGN KEY (scope_id, action_id) REFERENCES gov_action_receipts (scope_id, receipt_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE gov_acceptances ADD CONSTRAINT fk_gov_acceptance_receipt
    FOREIGN KEY (scope_id, action_id) REFERENCES gov_action_receipts (scope_id, receipt_id)
    DEFERRABLE INITIALLY DEFERRED;

-- An acceptance's evidence array cannot have an ordinary FK; validate each
-- exact revision under the same scope rather than trusting JSON identifiers.
CREATE FUNCTION gov_validate_acceptance_evidence()
RETURNS trigger LANGUAGE plpgsql AS $gov_validate_acceptance_evidence$
DECLARE evidence jsonb;
BEGIN
    FOR evidence IN SELECT value FROM jsonb_array_elements(NEW.evidence_revision_ids)
    LOOP
        IF jsonb_typeof(evidence) <> 'string' OR NOT EXISTS (
            SELECT 1 FROM gov_object_revisions r
            JOIN gov_objects o ON (o.scope_id,o.object_id)=(r.scope_id,r.object_id)
            WHERE r.scope_id=NEW.scope_id AND r.revision_id=(evidence #>> '{}')::uuid
              AND o.object_type='EvidenceAsset'
        ) THEN
            RAISE EXCEPTION 'acceptance evidence must reference an in-scope evidence revision'
                USING ERRCODE='23503';
        END IF;
    END LOOP;
    RETURN NEW;
END
$gov_validate_acceptance_evidence$;
CREATE TRIGGER trg_gov_acceptance_evidence
    BEFORE INSERT ON gov_acceptances
    FOR EACH ROW EXECUTE FUNCTION gov_validate_acceptance_evidence();

-- ---------------------------------------------------------------------------
-- Append-only enforcement (defense in depth on top of role grants).
-- ---------------------------------------------------------------------------

CREATE FUNCTION gov_reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $gov_reject_mutation$
BEGIN
    RAISE EXCEPTION 'governed append-only table % rejects %', TG_TABLE_NAME, TG_OP
        USING ERRCODE = '55000';
END
$gov_reject_mutation$;

CREATE TRIGGER trg_gov_domains_append_only
    BEFORE UPDATE OR DELETE ON gov_domains
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
CREATE TRIGGER trg_gov_policies_append_only
    BEFORE UPDATE OR DELETE ON gov_activation_policies
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
CREATE TRIGGER trg_gov_revisions_append_only
    BEFORE UPDATE OR DELETE ON gov_object_revisions
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
CREATE TRIGGER trg_gov_handshakes_append_only
    BEFORE UPDATE OR DELETE ON gov_handshakes
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
CREATE TRIGGER trg_gov_events_append_only
    BEFORE UPDATE OR DELETE ON gov_lifecycle_events
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
CREATE TRIGGER trg_gov_receipts_append_only
    BEFORE UPDATE OR DELETE ON gov_action_receipts
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
CREATE TRIGGER trg_gov_acceptances_append_only
    BEFORE UPDATE OR DELETE ON gov_acceptances
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
CREATE TRIGGER trg_gov_snapshots_append_only
    BEFORE UPDATE OR DELETE ON gov_context_snapshots
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();

-- ---------------------------------------------------------------------------
-- Row Level Security.  The scope setting is established only after credential
-- resolution; when unset every policy evaluates false.
-- ---------------------------------------------------------------------------

CREATE FUNCTION gov_scope_matches(row_scope uuid)
RETURNS boolean
LANGUAGE sql
STABLE
AS $gov_scope_matches$
    SELECT row_scope = NULLIF(current_setting('app.governed_scope_id', true), '')::uuid
$gov_scope_matches$;

CREATE FUNCTION gov_credential_matches(row_digest text)
RETURNS boolean
LANGUAGE sql
STABLE
AS $gov_credential_matches$
    SELECT row_digest = NULLIF(current_setting('app.governed_credential_digest', true), '')
$gov_credential_matches$;

ALTER TABLE gov_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_scopes FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_scopes_scope ON gov_scopes
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_principals ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_principals FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_principals_scope ON gov_principals
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_credentials FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_credentials_digest ON gov_credentials
    USING (gov_credential_matches(credential_digest))
    WITH CHECK (gov_credential_matches(credential_digest));

ALTER TABLE gov_domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_domains FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_domains_scope ON gov_domains
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_role_assignments FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_assignments_scope ON gov_role_assignments
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_activation_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_activation_policies FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_policies_scope ON gov_activation_policies
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_objects FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_objects_scope ON gov_objects
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_object_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_object_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_revisions_scope ON gov_object_revisions
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_handshakes ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_handshakes FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_handshakes_scope ON gov_handshakes
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_lifecycle_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_lifecycle_events FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_events_scope ON gov_lifecycle_events
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_action_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_action_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_receipts_scope ON gov_action_receipts
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_feedback_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_feedback_state FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_feedback_state_scope ON gov_feedback_state
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_acceptances ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_acceptances FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_acceptances_scope ON gov_acceptances
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

ALTER TABLE gov_context_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_context_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_snapshots_scope ON gov_context_snapshots
    USING (gov_scope_matches(scope_id))
    WITH CHECK (gov_scope_matches(scope_id));

COMMENT ON TABLE gov_scopes IS
    'Governed scope fence; auth_epoch serializes revocation against authorized transactions.';
COMMENT ON TABLE gov_action_receipts IS
    'Immutable committed-action receipts; the idempotency authority for governed writes.';
COMMENT ON TABLE gov_object_revisions IS
    'Immutable versioned business content; corrections create new revisions.';
