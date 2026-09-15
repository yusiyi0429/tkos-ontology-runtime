-- Append-only application interaction records; no Method object/state changes.
CREATE TABLE gov_workspace_events (
    scope_id uuid NOT NULL,
    scene_id uuid NOT NULL,
    event_id uuid PRIMARY KEY,
    version bigint NOT NULL CHECK(version > 0),
    anchor_object_id uuid NOT NULL,
    anchor_revision_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    action_id uuid NOT NULL,
    kind text NOT NULL CHECK(kind IN ('create','comment_anchor','diff_response',
        'monthly_material','weekly_material','refresh_sources','weekly_answer',
        'weekly_confirm','read','request_supplement','bring_to_meeting',
        'meeting_start','meeting_finish','meeting_material','meeting_publish','withdraw')),
    payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
    payload_hash text NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(scope_id,scene_id,version),
    UNIQUE(scope_id,event_id),
    FOREIGN KEY(scope_id,anchor_object_id,anchor_revision_id)
        REFERENCES gov_object_revisions(scope_id,object_id,revision_id),
    FOREIGN KEY(scope_id,principal_id) REFERENCES gov_principals(scope_id,principal_id),
    FOREIGN KEY(scope_id,action_id) REFERENCES gov_action_receipts(scope_id,receipt_id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX gov_workspace_external_id ON gov_workspace_events
    (scope_id,(payload->>'scene_type'),(payload->>'external_id')) WHERE kind='create';
CREATE INDEX ON gov_workspace_events(scope_id,anchor_object_id,scene_id,version);
ALTER TABLE gov_workspace_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_workspace_events FORCE ROW LEVEL SECURITY;
CREATE POLICY gov_workspace_scope ON gov_workspace_events
    USING (gov_scope_matches(scope_id) OR gov_control_plane_on())
    WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER gov_workspace_capability BEFORE INSERT OR UPDATE ON gov_workspace_events
    FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER gov_workspace_immutable BEFORE UPDATE OR DELETE ON gov_workspace_events
    FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
