-- 0030: tkos.world/0.1 business world model, beside the frozen tkos.method.
--
-- Seven new object types (Strategy and Mission already exist; their meaning is
-- decided by the bound protocol).  gov_world_events is the append-only event
-- log: every world action writes exactly one event in its own transaction,
-- enforced here as UNIQUE (scope_id, action_id).  A state snapshot is an
-- object row whose single revision carries subject_ref and as_of; world refs
-- are the only subject_ref shape with an object_version key (every Method and
-- workspace ref is a strict object/revision/hash triple), which is what the
-- partial unique index keys on.  The world binding gate pins both the contract
-- bytes and the world registry JSON bytes.
DO $$
DECLARE old_definition text;
BEGIN
 SELECT pg_get_constraintdef(oid) INTO old_definition FROM pg_constraint
 WHERE conrelid='gov_objects'::regclass AND conname='ck_gov_object_type';
 IF old_definition IS NULL THEN RAISE EXCEPTION 'expected object constraint'; END IF;
 ALTER TABLE gov_objects DROP CONSTRAINT ck_gov_object_type;
 EXECUTE 'ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_type CHECK (' || substring(old_definition from 7)
   || ' OR object_type IN (''Company'',''ResponsibilityUnit'',''LongTermGoal'',''PeriodGoal'',''Task'',''Activity'',''StateSnapshot''))';
END $$;

CREATE TABLE gov_world_events (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('object.created','object.revised','state.refreshed','event.recorded',
                                       'commit','confirm','assign','relate','core_battle.marked')),
    phase text CHECK (phase IN ('initiation','delivery')),
    category text CHECK (category IN ('meeting','review','delivery','acceptance','other','correction')),
    outcome text CHECK (outcome IN ('accepted','returned','withdrawn')),
    subject_refs jsonb NOT NULL CHECK (jsonb_typeof(subject_refs) = 'array' AND jsonb_array_length(subject_refs) >= 1),
    principal_id uuid NOT NULL,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    content jsonb,
    action_id uuid NOT NULL,
    supersedes_event_id uuid,
    UNIQUE (scope_id, event_id),
    UNIQUE (scope_id, action_id),
    FOREIGN KEY (scope_id, principal_id) REFERENCES gov_principals(scope_id, principal_id),
    FOREIGN KEY (scope_id, action_id) REFERENCES gov_action_receipts(scope_id, receipt_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (scope_id, supersedes_event_id) REFERENCES gov_world_events(scope_id, event_id),
    CHECK ((kind = 'event.recorded') = (category IS NOT NULL)),
    CHECK (kind IN ('commit','confirm') OR (phase IS NULL AND outcome IS NULL)),
    CHECK (kind <> 'confirm' OR outcome IS NOT NULL),
    CHECK (kind <> 'commit' OR outcome IS NULL OR outcome = 'withdrawn'),
    CHECK ((COALESCE(category = 'correction', false) OR COALESCE(outcome = 'withdrawn', false))
           = (supersedes_event_id IS NOT NULL))
);
ALTER TABLE gov_world_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_world_events FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_world_events USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_world_events FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON gov_world_events FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();

-- Mirror the roles that already have runtime SELECT on object revisions: the
-- event log is append-only, so SELECT, INSERT is the exact grant (as in 0027).
DO $grants$
DECLARE grantee_name text;
BEGIN
    FOR grantee_name IN
        SELECT DISTINCT grantee FROM information_schema.role_table_grants
         WHERE table_schema='public' AND table_name='gov_object_revisions'
           AND privilege_type='SELECT' AND grantee <> CURRENT_USER
    LOOP
        EXECUTE format('GRANT SELECT, INSERT ON gov_world_events TO %I', grantee_name);
    END LOOP;
END
$grants$;

CREATE UNIQUE INDEX ux_gov_world_snapshot_subject_as_of
    ON gov_object_revisions (scope_id, (payload->'subject_ref'->>'object_id'), (payload->>'as_of'))
    WHERE payload->'subject_ref' ? 'object_version' AND payload ? 'as_of';
CREATE INDEX ix_gov_world_parent_ref
    ON gov_object_revisions (scope_id, (payload->'parent_ref'->>'object_id'))
    WHERE payload ? 'parent_ref';

-- Preserve prior binding contracts; add the exact world 0.1 identity.
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
            AND prow.content->'action_contract_ref'->>'content_sha256' = '984c3e09dc9771e29e26aea858d19bb4639bb3d93dc4df12841130ef4f8e44aa') IS NOT TRUE THEN
            RAISE EXCEPTION 'method 0.4 requires its exact formal-governance contract' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.method' AND NEW.contract_version = 'tkos.method/0.5' THEN
        IF (prow.schema_version = 'tkos.method-profile/0.5'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.method'
            AND prow.content->'action_contract_ref'->>'revision' = '0.5'
            AND prow.content->'action_contract_ref'->>'content_sha256' = 'd2ea113231533862fb2aed1611608b54c00fe66db0bbb99fe904ec6c69ed6d6f'
            AND prow.content->'ontology_registry_ref'->>'registry_id' = 'tkos.ontology-registry'
            AND prow.content->'ontology_registry_ref'->>'revision' = '0.7.1'
            AND prow.content->'ontology_registry_ref'->>'content_sha256' = '4f44c759d26db4e6812c60b11664add106697a1abf69935b9a9ca316e62220f8') IS NOT TRUE THEN
            RAISE EXCEPTION 'method 0.5 requires its exact ontology-alignment contract and registry' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.world' AND NEW.contract_version = 'tkos.world/0.1' THEN
        IF (prow.schema_version = 'tkos.world-profile/0.1'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.world'
            AND prow.content->'action_contract_ref'->>'revision' = '0.1'
            AND prow.content->'action_contract_ref'->>'content_sha256' = 'f72be2ee059cc1a2ebb95565f8ac463e5d521991f4cd6e71b567c63410f9cf28'
            AND prow.content->'world_registry_ref'->>'registry_id' = 'tkos.world-registry'
            AND prow.content->'world_registry_ref'->>'revision' = '0.1.0'
            AND prow.content->'world_registry_ref'->>'content_sha256' = '4be616319a48d0cf45807bac7f288bb17deaa67f601538883b6e6ea74ce0a507') IS NOT TRUE THEN
            RAISE EXCEPTION 'world 0.1 requires its exact business-world contract and registry' USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'protocol % contract version % is not implemented by this schema',
            NEW.protocol_id, NEW.contract_version
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$gov_binding_insert_gate$;
