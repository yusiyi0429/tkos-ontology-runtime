-- 0036: tkos.world/0.1 context packs (ticket #25).
-- Every get-context call over /v1/world/objects/{id}/context records one row:
-- the question, the context pack (layered blocks, snapshots and events, all
-- with pinned citations, plus the rendered Markdown), the retrieval plan, the
-- six-question coverage, and the budget with its trimming record.  The table
-- is append-only and scope-fenced like gov_world_events; it does not reuse the
-- kernel's gov_context_snapshots.  The contract is unchanged (its section 12
-- leaves the projection shapes to spec #17), so the binding gate is not re-pinned.
CREATE TABLE gov_world_context_packs (
    context_pack_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    object_id uuid NOT NULL,
    question text NOT NULL CHECK (question ~ '\S'),
    pack jsonb NOT NULL CHECK (jsonb_typeof(pack) = 'object'),
    plan jsonb NOT NULL CHECK (jsonb_typeof(plan) = 'object'),
    coverage jsonb NOT NULL CHECK (jsonb_typeof(coverage) = 'object'),
    budget jsonb NOT NULL CHECK (jsonb_typeof(budget) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (scope_id, context_pack_id),
    FOREIGN KEY (scope_id, principal_id) REFERENCES gov_principals(scope_id, principal_id),
    FOREIGN KEY (scope_id, object_id) REFERENCES gov_objects(scope_id, object_id)
);
ALTER TABLE gov_world_context_packs ENABLE ROW LEVEL SECURITY;
ALTER TABLE gov_world_context_packs FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_fence ON gov_world_context_packs USING (gov_scope_matches(scope_id) OR gov_control_plane_on()) WITH CHECK (gov_scope_matches(scope_id) OR gov_control_plane_on());
CREATE TRIGGER write_capability BEFORE INSERT OR UPDATE ON gov_world_context_packs FOR EACH ROW EXECUTE FUNCTION gov_require_runtime_capability();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON gov_world_context_packs FOR EACH ROW EXECUTE FUNCTION gov_reject_mutation();
CREATE INDEX ix_gov_world_context_packs_object ON gov_world_context_packs (scope_id, object_id, created_at);

-- Mirror the roles that already have runtime SELECT on object revisions: the
-- table is append-only, so SELECT, INSERT is the exact grant (as in 0030).
DO $grants$
DECLARE grantee_name text;
BEGIN
    FOR grantee_name IN
        SELECT DISTINCT grantee FROM information_schema.role_table_grants
         WHERE table_schema='public' AND table_name='gov_object_revisions'
           AND privilege_type='SELECT' AND grantee <> CURRENT_USER
    LOOP
        EXECUTE format('GRANT SELECT, INSERT ON gov_world_context_packs TO %I', grantee_name);
    END LOOP;
END
$grants$;
