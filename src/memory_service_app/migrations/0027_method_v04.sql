-- 0027: tkos.method/0.4 formal governance support.
--
-- No new object_type is needed: 0.4 reuses StrategicIssue, StrategicAgreement,
-- Strategy, StrategicArchitecture, StrategyUpdateProposal, LTCO, PCO, Mission,
-- ReviewWindow, CandidateSet, OperatingState, OperatingProblem and PeriodReview.
-- The one new invariant that existing tables cannot express is the unique
-- per-responsibility candidate commitment, so it gets its own append-only
-- scoped table.  No roles or GRANTs are created here; deployment grant helpers
-- apply SELECT, INSERT to non-mutable gov_* tables.
CREATE TABLE gov_method_commitments (
    commitment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    candidate_object_id uuid NOT NULL,
    candidate_revision_id uuid NOT NULL,
    responsibility_object_id uuid NOT NULL,
    responsibility_revision_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    assignment_id uuid NOT NULL,
    statement text NOT NULL CHECK (length(btrim(statement)) > 0),
    action_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (scope_id, candidate_revision_id, responsibility_object_id),
    FOREIGN KEY (scope_id, candidate_object_id, candidate_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    FOREIGN KEY (scope_id, responsibility_object_id, responsibility_revision_id)
        REFERENCES gov_object_revisions (scope_id, object_id, revision_id),
    FOREIGN KEY (scope_id, principal_id) REFERENCES gov_principals (scope_id, principal_id),
    FOREIGN KEY (scope_id, assignment_id) REFERENCES gov_role_assignments (scope_id, assignment_id),
    FOREIGN KEY (scope_id, action_id) REFERENCES gov_action_receipts (scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_gov_method_commitments_candidate
    ON gov_method_commitments (scope_id, candidate_revision_id, recorded_at, commitment_id);

ALTER TABLE gov_method_commitments ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_commitments FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_commitments USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_method_commitments FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON gov_method_commitments FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();

-- Mirror the role that already has runtime SELECT on the Method state table:
-- commitments are append-only, so SELECT, INSERT is the exact grant.
DO $grants$
DECLARE grantee_name text;
BEGIN
    FOR grantee_name IN
        SELECT DISTINCT grantee FROM information_schema.role_table_grants
         WHERE table_schema='public' AND table_name='gov_method_state'
           AND privilege_type='SELECT' AND grantee <> CURRENT_USER
    LOOP
        EXECUTE format('GRANT SELECT, INSERT ON gov_method_commitments TO %I', grantee_name);
    END LOOP;
END
$grants$;

-- Preserve prior binding contracts; add the exact 0.4 formal-governance identity.
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
        IF (NEW.profile_id = 'urn:tkos:legacy:governed-v0.2'
                AND NEW.profile_revision = '0.2.0'
                AND NEW.profile_canonical_hash = '93c5278a70c70af453e2f86c6d2b298caefe15b1e14b736c006c817c8a182598'
                AND prow.schema_version = 'tkos.legacy-interpretation-record/0.2') IS NOT TRUE THEN
            RAISE EXCEPTION 'legacy protocol bindings require the pinned legacy interpretation record'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.contract-a' AND NEW.contract_version = 'tkos.contract-a/0.1' THEN
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
    ELSIF NEW.protocol_id = 'tkos.method' AND NEW.contract_version = 'tkos.method/0.2' THEN
        IF (prow.schema_version = 'tkos.method-profile/0.2'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.method'
            AND prow.content->'action_contract_ref'->>'revision' = '0.2'
            AND prow.content->'action_contract_ref'->>'content_sha256' = '82568bdff37c711207a1a010ae553f72b2f036122b1f88a3b92aec286f9fbbf3') IS NOT TRUE THEN
            RAISE EXCEPTION 'method 0.2 requires its exact lifecycle contract' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.method' AND NEW.contract_version = 'tkos.method/0.3' THEN
        IF (prow.schema_version = 'tkos.method-profile/0.3'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.method'
            AND prow.content->'action_contract_ref'->>'revision' = '0.3'
            AND prow.content->'action_contract_ref'->>'content_sha256' = '17f0896993ac91bf232aa8d60de618ccec9b61b1d81fa68f815414943574381d') IS NOT TRUE THEN
            RAISE EXCEPTION 'method 0.3 requires its exact Anchor contract' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.method' AND NEW.contract_version = 'tkos.method/0.4' THEN
        IF (prow.schema_version = 'tkos.method-profile/0.4'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.method'
            AND prow.content->'action_contract_ref'->>'revision' = '0.4'
            AND prow.content->'action_contract_ref'->>'content_sha256' = 'be5152acd1f94ec9a44f3297cb0453d21246840abf1da8510af466927b110d99') IS NOT TRUE THEN
            RAISE EXCEPTION 'method 0.4 requires its exact formal-governance contract' USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'protocol % contract version % is not implemented by this schema',
            NEW.protocol_id, NEW.contract_version
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$gov_binding_insert_gate$;
