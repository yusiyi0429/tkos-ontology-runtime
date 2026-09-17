-- 0026: tkos.workspace/0.2 standalone source collaboration (B2).
-- Additive only. gov_workspace_events (0.1 scenes) is untouched and keeps its
-- required Method anchor; 0.2 records live in parallel tables, have no Method
-- anchor and produce no formal business effect.
--
-- Privacy is scene-scoped, never domain/role-scoped. Source content segments
-- are immutable event payloads. When a source version references an existing
-- EvidenceAsset (any active domain protocol), gov_workspace_v02_assets links
-- that exact revision to the source version so the runtime can apply the
-- workspace_v02_guard source fence to direct object/revision/download reads.
-- No MethodRun, Issue, Method object or task is created here.

CREATE TABLE gov_workspace_v02_events (
    scope_id uuid NOT NULL,
    scene_id uuid NOT NULL,
    event_id uuid PRIMARY KEY,
    version bigint NOT NULL CHECK(version > 0),
    principal_id uuid NOT NULL,
    action_id uuid NOT NULL,
    kind text NOT NULL CHECK(kind IN ('scene_create','source_add','source_version',
        'source_correct','source_withdraw','source_share','source_unshare',
        'agent_run','followup_draft','draft_decision')),
    payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
    payload_hash text NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(scope_id,scene_id,version),
    UNIQUE(scope_id,event_id),
    FOREIGN KEY(scope_id,principal_id) REFERENCES gov_principals(scope_id,principal_id),
    FOREIGN KEY(scope_id,action_id) REFERENCES gov_action_receipts(scope_id,receipt_id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX gov_workspace_v02_external_id ON gov_workspace_v02_events
    (scope_id,(payload->>'scene_type'),(payload->>'external_id')) WHERE kind='scene_create';
CREATE INDEX ON gov_workspace_v02_events(scope_id,scene_id,version);
CREATE INDEX ON gov_workspace_v02_events(scope_id,kind,(payload->>'source_id'));
ALTER TABLE gov_workspace_v02_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_workspace_v02_events FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_workspace_v02_scope ON gov_workspace_v02_events
    USING (gov_scope_matches(scope_id) OR gov_control_plane_on())
    WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER gov_workspace_v02_capability BEFORE INSERT OR UPDATE ON gov_workspace_v02_events
    FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER gov_workspace_v02_immutable BEFORE UPDATE OR DELETE ON gov_workspace_v02_events
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();

CREATE TABLE gov_workspace_v02_contexts (
    context_id uuid PRIMARY KEY,
    scope_id uuid NOT NULL,
    scene_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    purpose text NOT NULL,
    selected jsonb NOT NULL CHECK(jsonb_typeof(selected)='array'),
    excluded jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(excluded)='array'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(scope_id,context_id),
    UNIQUE(scope_id,principal_id,idempotency_key),
    FOREIGN KEY(scope_id,principal_id) REFERENCES gov_principals(scope_id,principal_id)
);
CREATE INDEX ON gov_workspace_v02_contexts(scope_id,scene_id,recorded_at,context_id);
ALTER TABLE gov_workspace_v02_contexts ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_workspace_v02_contexts FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_workspace_v02_context_scope ON gov_workspace_v02_contexts
    USING (gov_scope_matches(scope_id) OR gov_control_plane_on())
    WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER gov_workspace_v02_context_capability BEFORE INSERT OR UPDATE ON gov_workspace_v02_contexts
    FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER gov_workspace_v02_context_immutable BEFORE UPDATE OR DELETE ON gov_workspace_v02_contexts
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();

-- Source fence index: exact EvidenceAsset revision linked to the authoritative
-- source version that carries its scene-scoped privacy. Append-only; only the
-- version event that references the evidence may create a link, and the same
-- revision may be linked by more than one source version (e.g. a correction).
CREATE TABLE gov_workspace_v02_assets (
    scope_id uuid NOT NULL,
    object_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    scene_id uuid NOT NULL,
    source_id uuid NOT NULL,
    version_event_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(scope_id,object_id,revision_id,version_event_id),
    FOREIGN KEY(scope_id,object_id,revision_id)
        REFERENCES gov_object_revisions(scope_id,object_id,revision_id),
    FOREIGN KEY(scope_id,version_event_id)
        REFERENCES gov_workspace_v02_events(scope_id,event_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ON gov_workspace_v02_assets(scope_id,object_id,revision_id);
CREATE INDEX ON gov_workspace_v02_assets(scope_id,scene_id,source_id);
ALTER TABLE gov_workspace_v02_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_workspace_v02_assets FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_workspace_v02_asset_scope ON gov_workspace_v02_assets
    USING (gov_scope_matches(scope_id) OR gov_control_plane_on())
    WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER gov_workspace_v02_asset_capability BEFORE INSERT OR UPDATE ON gov_workspace_v02_assets
    FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER gov_workspace_v02_asset_immutable BEFORE UPDATE OR DELETE ON gov_workspace_v02_assets
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();

-- Runtime table grants stay declarative in the migration: mirror whatever role
-- already has runtime SELECT on the 0.1 event table (the application role when
-- pre-existing grants are present) instead of hardcoding a deployment role.
DO $grants$
DECLARE grantee_name text;
BEGIN
    FOR grantee_name IN
        SELECT DISTINCT grantee FROM information_schema.role_table_grants
         WHERE table_schema='public' AND table_name='gov_workspace_events'
           AND privilege_type='SELECT' AND grantee <> CURRENT_USER
    LOOP
        EXECUTE format('GRANT SELECT, INSERT ON gov_workspace_v02_events, gov_workspace_v02_contexts, gov_workspace_v02_assets TO %I',
                       grantee_name);
    END LOOP;
END
$grants$;
