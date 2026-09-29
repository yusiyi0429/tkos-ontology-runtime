-- 0039: tkos.world/0.2 beside the frozen tkos.world/0.1 (ticket #49).
--
-- Rewritable until the 0.2 lock (ADR-0009): it runs only on databases that are
-- rebuilt from scratch (the isolated acceptance stack and new experiment
-- instances), never on world-lab, production or the Clark-linked stack.  Each
-- change to the 0.2 contract or registry re-pins this file together with
-- world_v02_profile and docs/contracts/world-profile-0.2.json.
--
-- The event log grows once to every 0.2 kind and field: contract_version tells
-- 0.1 rows from 0.2 rows (0.1 code does not name the column and keeps writing
-- the default), phase stays a 0.1-only column, and 0.2 rows carry disposition,
-- the person recorded on behalf of, the external record id and confirmation
-- time, and per-kind structured detail.  0.1 rows are only re-validated; their
-- constraints keep their 0.1 meaning.  The binding gate keeps every prior
-- identity (0.1 exactly as 0035 pinned it) and adds the world 0.2 profile
-- pinned to the current contract and registry bytes.  No new object types,
-- tables or grants: new columns inherit the table's append-only grants.  One
-- index serves the lookup of objects by external reference.
ALTER TABLE gov_world_events
    ADD COLUMN contract_version text NOT NULL DEFAULT 'tkos.world/0.1',
    ADD COLUMN disposition text,
    ADD COLUMN on_behalf_of uuid,
    ADD COLUMN external_record_id text,
    ADD COLUMN external_confirmed_at timestamptz,
    ADD COLUMN detail jsonb,
    ADD CONSTRAINT fk_gov_world_event_on_behalf_of
        FOREIGN KEY (scope_id, on_behalf_of) REFERENCES gov_principals(scope_id, principal_id);

-- The 0030 checks are unnamed and were written for 0.1 only; replace all of
-- them with named checks that say which contract each rule belongs to.
DO $$
DECLARE name text;
BEGIN
    FOR name IN SELECT conname FROM pg_constraint
                 WHERE conrelid = 'gov_world_events'::regclass AND contype = 'c'
    LOOP
        EXECUTE format('ALTER TABLE gov_world_events DROP CONSTRAINT %I', name);
    END LOOP;
END $$;

ALTER TABLE gov_world_events
    ADD CONSTRAINT ck_gov_world_event_contract
        CHECK (contract_version IN ('tkos.world/0.1','tkos.world/0.2')),
    ADD CONSTRAINT ck_gov_world_event_kind CHECK (kind IN (
        'object.created','object.revised','state.refreshed','event.recorded','assign','relate',
        'core_battle.marked','issue.raised','issue.routed','issue.owned','issue.disposed','issue.returned',
        'delegation.granted','delegation.revoked','commit','confirm','reconfirm','agreement','review.confirmed',
        'start','deliver','accept','reject','reopen','cancel')),
    ADD CONSTRAINT ck_gov_world_event_category
        CHECK (category IN ('meeting','review','delivery','acceptance','other','correction')),
    ADD CONSTRAINT ck_gov_world_event_outcome CHECK (outcome IN ('accepted','returned','withdrawn')),
    ADD CONSTRAINT ck_gov_world_event_disposition CHECK (disposition IN (
        'no_action_close','current_layer_action','roll_forward','immediate_reopen','route_escalate','pushback')),
    ADD CONSTRAINT ck_gov_world_event_subjects
        CHECK (jsonb_typeof(subject_refs) = 'array' AND jsonb_array_length(subject_refs) >= 1),
    ADD CONSTRAINT ck_gov_world_event_category_iff_external CHECK ((kind = 'event.recorded') = (category IS NOT NULL)),
    ADD CONSTRAINT ck_gov_world_event_supersedes CHECK (
        (COALESCE(category = 'correction', false) OR COALESCE(outcome = 'withdrawn', false))
        = (supersedes_event_id IS NOT NULL)),
    -- 0.1 rows: the 0.1 kinds, phase only on commit/confirm, and none of the 0.2 fields.
    ADD CONSTRAINT ck_gov_world_event_v01 CHECK (contract_version <> 'tkos.world/0.1' OR (
        kind IN ('object.created','object.revised','state.refreshed','event.recorded',
                 'commit','confirm','assign','relate','core_battle.marked')
        AND (phase IS NULL OR phase IN ('initiation','delivery'))
        AND (kind IN ('commit','confirm') OR (phase IS NULL AND outcome IS NULL))
        AND (kind <> 'confirm' OR outcome IS NOT NULL)
        AND (kind <> 'commit' OR outcome IS NULL OR outcome = 'withdrawn')
        AND disposition IS NULL AND on_behalf_of IS NULL AND external_record_id IS NULL
        AND external_confirmed_at IS NULL AND detail IS NULL)),
    -- 0.2 rows (contract section 8): no phase; a confirmation always has an outcome, the other gate and
    -- lifecycle events carry one only when withdrawing, record events never; a disposition exactly on
    -- issue.disposed; on-behalf recording only for the delegable families (gates, assignments, lifecycle),
    -- with the external record id and a confirmation time no later than the recording.
    ADD CONSTRAINT ck_gov_world_event_v02 CHECK (contract_version <> 'tkos.world/0.2' OR (
        phase IS NULL
        AND CASE WHEN kind = 'confirm' THEN outcome IS NOT NULL
                 WHEN kind IN ('commit','agreement','review.confirmed','start','deliver','accept','reject',
                               'reopen','cancel') THEN outcome IS NULL OR outcome = 'withdrawn'
                 ELSE outcome IS NULL END
        AND (kind = 'issue.disposed') = (disposition IS NOT NULL)
        AND (on_behalf_of IS NULL) = (external_record_id IS NULL)
        AND (on_behalf_of IS NULL) = (external_confirmed_at IS NULL)
        AND (on_behalf_of IS NULL OR kind IN ('assign','core_battle.marked','commit','confirm','reconfirm',
                                             'agreement','review.confirmed','start','deliver','accept',
                                             'reject','reopen','cancel'))
        AND (external_record_id IS NULL OR external_record_id ~ '\S')
        AND (external_confirmed_at IS NULL OR external_confirmed_at <= recorded_at)
        AND (detail IS NULL OR jsonb_typeof(detail) = 'object')
        -- contract section 11: nothing happens after it is recorded; only external events and state
        -- refreshes may be backdated, every other kind happens at the moment it is recorded.
        AND occurred_at <= recorded_at
        AND (kind IN ('event.recorded','state.refreshed') OR occurred_at = recorded_at)));

-- Preserve prior binding contracts (world 0.1 exactly as 0035 pinned it); add the world 0.2 identity.
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
            AND prow.content->'action_contract_ref'->>'content_sha256' = '1a56c60a7210732f51f1bec3d566e725ff27414970ade369bd43aa2fb7792209'
            AND prow.content->'world_registry_ref'->>'registry_id' = 'tkos.world-registry'
            AND prow.content->'world_registry_ref'->>'revision' = '0.1.4'
            AND prow.content->'world_registry_ref'->>'content_sha256' = 'be6559996c8aa10be130758cbdfbf72e8940b01f6bb2d8f6dbd3a1b54062cefd') IS NOT TRUE THEN
            RAISE EXCEPTION 'world 0.1 requires its exact business-world contract and registry' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.world' AND NEW.contract_version = 'tkos.world/0.2' THEN
        IF (prow.schema_version = 'tkos.world-profile/0.2'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.world'
            AND prow.content->'action_contract_ref'->>'revision' = '0.2'
            AND prow.content->'action_contract_ref'->>'content_sha256' = 'fb260b28283fa0fa1a0b7d17291e9b18cbbeea32db37f0408be44cd8bc24a0df'
            AND prow.content->'world_registry_ref'->>'registry_id' = 'tkos.world-registry'
            AND prow.content->'world_registry_ref'->>'revision' = '0.2.0'
            AND prow.content->'world_registry_ref'->>'content_sha256' = '098dd564a5e4297602ef4e99871730998d6fff2efddcf9344b56abb8f1264bd1') IS NOT TRUE THEN
            RAISE EXCEPTION 'world 0.2 requires its exact business-world contract and registry' USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'protocol % contract version % is not implemented by this schema',
            NEW.protocol_id, NEW.contract_version
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$gov_binding_insert_gate$;

-- Ticket #63: business objects carry external references and are looked up by (system, id) (contract
-- sections 3.4 and 15.2).  The service keeps a pair unique within a scope, judged on each object's latest
-- revision (writes in a scope are serialised by the scope fence); this index serves that check and the
-- lookup, a containment query on the latest revisions.
CREATE INDEX ix_gov_world_external_refs
    ON gov_object_revisions USING gin ((payload->'external_refs') jsonb_path_ops)
    WHERE payload ? 'external_refs';
