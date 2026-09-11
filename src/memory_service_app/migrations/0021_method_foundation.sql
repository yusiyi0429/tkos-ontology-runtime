-- 0021: independent tkos.method/0.1. No legacy backfill, roles or grants.
-- Content stays in immutable gov_object_revisions. State, collaboration,
-- impact notices and run attempts have independent records and scope FKs.
ALTER TABLE gov_objects DROP CONSTRAINT IF EXISTS ck_gov_object_type;
ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_type CHECK (
    object_type IN (
        'CompanyOutcome', 'BusinessCommitment', 'ExecutionCommitment',
        'FeedbackThread', 'ManagementAdjustment', 'Decision',
        'MetricObservation', 'EvidenceAsset', 'WorkItem', 'Deliverable',
        'ProtocolSentinel',
        'CompanyReference', 'CapacityObservation', 'FormationRound',
        'DomainSubmission', 'CompanyComposition', 'Mission', 'DomainCommitment',
        'ExecutionPlan', 'Signal', 'PotentialIssue', 'StrategicIssue', 'ResearchMemo',
        'ResearchPlan', 'ResearchReport', 'MeetingRound', 'MeetingMinutes',
        'StrategicAgreement', 'StrategyUpdateProposal', 'Strategy', 'StrategicJudgment',
        'BusinessFact', 'PeriodReview', 'LTCO', 'PCO', 'ReviewWindow', 'CandidateSet',
        'LTCOReviewAdvice', 'MethodRun'
    )
);

ALTER TABLE gov_role_assignments DROP CONSTRAINT ck_gov_assignment_role;
ALTER TABLE gov_role_assignments ADD CONSTRAINT ck_gov_assignment_role CHECK (
    role IN ('CEO','DOMAIN_DRI','MISSION_DRI','VERIFIER','IC','AGENT',
             'CEO_AGENT','CO_AGENT','PERSONAL_AGENT')
);
CREATE TABLE gov_method_state (
    scope_id uuid NOT NULL, object_id uuid NOT NULL,
    state jsonb NOT NULL CHECK (jsonb_typeof(state)='object'),
    updated_by_action_id uuid NOT NULL,
    PRIMARY KEY(scope_id,object_id),
    FOREIGN KEY(scope_id,object_id) REFERENCES gov_objects(scope_id,object_id),
    FOREIGN KEY(scope_id,updated_by_action_id) REFERENCES gov_action_receipts(scope_id,receipt_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE gov_method_reviews (
    record_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), scope_id uuid NOT NULL,
    kind text NOT NULL, target_object_id uuid NOT NULL, target_revision_id uuid NOT NULL,
    window_id uuid, principal_id uuid NOT NULL, content jsonb NOT NULL,
    action_id uuid NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(scope_id,record_id),
    FOREIGN KEY(scope_id,target_object_id,target_revision_id) REFERENCES gov_object_revisions(scope_id,object_id,revision_id),
    FOREIGN KEY(scope_id,window_id) REFERENCES gov_objects(scope_id,object_id),
    FOREIGN KEY(scope_id,principal_id) REFERENCES gov_principals(scope_id,principal_id),
    FOREIGN KEY(scope_id,action_id) REFERENCES gov_action_receipts(scope_id,receipt_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ON gov_method_reviews(scope_id,window_id,recorded_at,record_id);
CREATE TABLE gov_method_strategy_heads (
    scope_id uuid NOT NULL, domain_id uuid NOT NULL, object_id uuid NOT NULL, revision_id uuid NOT NULL,
    action_id uuid NOT NULL,
    PRIMARY KEY(scope_id,domain_id),
    FOREIGN KEY(scope_id,domain_id) REFERENCES gov_domains(scope_id,domain_id),
    FOREIGN KEY(scope_id,object_id,revision_id) REFERENCES gov_object_revisions(scope_id,object_id,revision_id),
    FOREIGN KEY(scope_id,action_id) REFERENCES gov_action_receipts(scope_id,receipt_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE gov_method_impacts (
    impact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), scope_id uuid NOT NULL,
    strategy_object_id uuid NOT NULL, strategy_revision_id uuid NOT NULL,
    target_object_id uuid NOT NULL, target_revision_id uuid NOT NULL,
    old_strategy_ref jsonb NOT NULL, action_id uuid NOT NULL,
    effect text NOT NULL DEFAULT 'review_required_effectiveness_preserved' CHECK(effect='review_required_effectiveness_preserved'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(scope_id,strategy_revision_id,target_revision_id),
    FOREIGN KEY(scope_id,strategy_object_id,strategy_revision_id) REFERENCES gov_object_revisions(scope_id,object_id,revision_id),
    FOREIGN KEY(scope_id,target_object_id,target_revision_id) REFERENCES gov_object_revisions(scope_id,object_id,revision_id),
    FOREIGN KEY(scope_id,action_id) REFERENCES gov_action_receipts(scope_id,receipt_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE gov_method_agent_bindings (
    binding_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), scope_id uuid NOT NULL,
    agent_principal_id uuid NOT NULL, owner_principal_id uuid NOT NULL, assignment_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(scope_id,agent_principal_id,owner_principal_id,assignment_id),
    FOREIGN KEY(scope_id,agent_principal_id) REFERENCES gov_principals(scope_id,principal_id),
    FOREIGN KEY(scope_id,owner_principal_id) REFERENCES gov_principals(scope_id,principal_id),
    FOREIGN KEY(scope_id,assignment_id,agent_principal_id) REFERENCES gov_role_assignments(scope_id,assignment_id,principal_id),
    CHECK(agent_principal_id<>owner_principal_id)
);
CREATE TABLE gov_method_runs (
    scope_id uuid NOT NULL, run_id uuid NOT NULL, owner_principal_id uuid NOT NULL,
    phase text NOT NULL CHECK(phase IN ('running','paused')), action_id uuid NOT NULL,
    PRIMARY KEY(scope_id,run_id),
    FOREIGN KEY(scope_id,run_id) REFERENCES gov_objects(scope_id,object_id),
    FOREIGN KEY(scope_id,owner_principal_id) REFERENCES gov_principals(scope_id,principal_id),
    FOREIGN KEY(scope_id,action_id) REFERENCES gov_action_receipts(scope_id,receipt_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE gov_method_run_members (
    scope_id uuid NOT NULL, run_id uuid NOT NULL, object_id uuid NOT NULL, action_id uuid NOT NULL,
    PRIMARY KEY(scope_id,object_id),
    FOREIGN KEY(scope_id,run_id) REFERENCES gov_method_runs(scope_id,run_id),
    FOREIGN KEY(scope_id,object_id) REFERENCES gov_objects(scope_id,object_id),
    FOREIGN KEY(scope_id,action_id) REFERENCES gov_action_receipts(scope_id,receipt_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE gov_method_run_attempts (
    attempt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), scope_id uuid NOT NULL, run_id uuid NOT NULL,
    step_key text NOT NULL, outcome text NOT NULL CHECK(outcome IN ('started','failed','abandoned','succeeded')),
    target_object_id uuid, principal_id uuid NOT NULL, note text NOT NULL,
    action_id uuid NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY(scope_id,run_id) REFERENCES gov_method_runs(scope_id,run_id),
    FOREIGN KEY(scope_id,target_object_id) REFERENCES gov_objects(scope_id,object_id),
    FOREIGN KEY(scope_id,principal_id) REFERENCES gov_principals(scope_id,principal_id),
    FOREIGN KEY(scope_id,action_id) REFERENCES gov_action_receipts(scope_id,receipt_id) DEFERRABLE INITIALLY DEFERRED
);
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
    ELSIF NEW.protocol_id = 'tkos.method' AND NEW.contract_version = 'tkos.method/0.1' THEN
        IF (prow.schema_version = 'tkos.method-profile/0.1'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.method'
            AND prow.content->'action_contract_ref'->>'revision' = '0.1'
            AND prow.content->'action_contract_ref'->>'content_sha256' = 'd108d228e903384182b6945181aa5bcafde4a3f64b4c564d897372805588d1c3'
            AND prow.content->>'m1a_source_revision' = '21'
            AND prow.content->>'m1b_source_revision' = '837') IS NOT TRUE THEN
            RAISE EXCEPTION 'method bindings require the independently frozen Method profile' USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'protocol % contract version % is not implemented by this schema',
            NEW.protocol_id, NEW.contract_version
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$gov_binding_insert_gate$;

ALTER TABLE gov_method_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_state FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_state USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_method_state FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE DELETE ON gov_method_state FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
ALTER TABLE gov_method_strategy_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_strategy_heads FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_strategy_heads USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_method_strategy_heads FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE DELETE ON gov_method_strategy_heads FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
ALTER TABLE gov_method_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_runs USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_method_runs FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE DELETE ON gov_method_runs FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
ALTER TABLE gov_method_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_reviews FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_reviews USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_method_reviews FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON gov_method_reviews FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
ALTER TABLE gov_method_impacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_impacts FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_impacts USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_method_impacts FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON gov_method_impacts FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
ALTER TABLE gov_method_run_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_run_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_run_attempts USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_method_run_attempts FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON gov_method_run_attempts FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
ALTER TABLE gov_method_run_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_run_members FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_run_members USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_method_run_members FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON gov_method_run_members FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
ALTER TABLE gov_method_agent_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_agent_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_agent_bindings USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_method_agent_bindings FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON gov_method_agent_bindings FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
CREATE OR REPLACE FUNCTION gov_method_agent_binding_control() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF gov_control_plane_on() IS NOT TRUE THEN
        RAISE EXCEPTION 'personal Agent bindings require identity control plane' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER method_agent_binding_control BEFORE INSERT ON gov_method_agent_bindings FOR EACH ROW EXECUTE FUNCTION gov_method_agent_binding_control();
