DO $$
DECLARE old_definition text;
BEGIN
 SELECT pg_get_constraintdef(oid) INTO old_definition FROM pg_constraint
 WHERE conrelid='gov_objects'::regclass AND conname='ck_gov_object_type';
 IF old_definition IS NULL THEN RAISE EXCEPTION 'expected object constraint'; END IF;
 ALTER TABLE gov_objects DROP CONSTRAINT ck_gov_object_type;
 EXECUTE 'ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_type CHECK (' || substring(old_definition from 7) || ' OR object_type IN (''StrategicArchitecture'',''OperatingState'',''OperatingProblem''))';
END $$;
CREATE TABLE gov_method_state_keys (
 scope_id uuid NOT NULL, subject_id uuid NOT NULL, outcome_id text NOT NULL,
 as_of timestamptz NOT NULL, state_id uuid NOT NULL,
 PRIMARY KEY(scope_id,subject_id,outcome_id,as_of), UNIQUE(scope_id,state_id),
 FOREIGN KEY(scope_id,subject_id) REFERENCES gov_objects(scope_id,object_id),
 FOREIGN KEY(scope_id,state_id) REFERENCES gov_objects(scope_id,object_id)
);
CREATE TABLE gov_method_problem_keys (
 scope_id uuid NOT NULL, subject_id uuid NOT NULL, outcome_id text NOT NULL,
 question_hash text NOT NULL, problem_id uuid NOT NULL,
 PRIMARY KEY(scope_id,subject_id,outcome_id,question_hash), UNIQUE(scope_id,problem_id),
 FOREIGN KEY(scope_id,subject_id) REFERENCES gov_objects(scope_id,object_id),
 FOREIGN KEY(scope_id,problem_id) REFERENCES gov_objects(scope_id,object_id)
);
ALTER TABLE gov_method_state_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_state_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_state_keys USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT ON gov_method_state_keys FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON gov_method_state_keys FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
ALTER TABLE gov_method_problem_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_method_problem_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_method_problem_keys USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT ON gov_method_problem_keys FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON gov_method_problem_keys FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();

-- Preserve prior binding contracts; explicitly support the frozen 0.2 contract.
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
    ELSE
        RAISE EXCEPTION 'protocol % contract version % is not implemented by this schema',
            NEW.protocol_id, NEW.contract_version
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$gov_binding_insert_gate$;

