-- 0028: repair tkos.workspace/0.2 runtime grants (0026 follow-up).
--
-- 0026 granted SELECT, INSERT on the new 0.2 tables to every role that held
-- SELECT on gov_workspace_events. That could promote a read-only reporting or
-- review role to a writer. 0026 is already applied and stays immutable; this
-- additive, idempotent repair restores the exact privilege level of the 0.1
-- table per role: SELECT follows SELECT, INSERT follows INSERT, and PUBLIC is
-- never granted anything.
DO $grants$
DECLARE grantee_name text;
BEGIN
    -- Remove INSERT from any role that never had INSERT on the 0.1 table.
    FOR grantee_name IN
        SELECT DISTINCT g.grantee
          FROM information_schema.role_table_grants g
         WHERE g.table_schema='public'
           AND g.table_name IN ('gov_workspace_v02_events',
                                'gov_workspace_v02_contexts',
                                'gov_workspace_v02_assets')
           AND g.privilege_type='INSERT'
           AND g.grantee <> CURRENT_USER AND g.grantee <> 'PUBLIC'
           AND NOT EXISTS (
               SELECT 1 FROM information_schema.role_table_grants w
                WHERE w.table_schema='public' AND w.table_name='gov_workspace_events'
                  AND w.privilege_type='INSERT' AND w.grantee=g.grantee)
    LOOP
        EXECUTE format('REVOKE INSERT ON gov_workspace_v02_events, gov_workspace_v02_contexts, gov_workspace_v02_assets FROM %I',
                       grantee_name);
    END LOOP;
    -- Mirror SELECT for readers of the 0.1 table.
    FOR grantee_name IN
        SELECT DISTINCT g.grantee
          FROM information_schema.role_table_grants g
         WHERE g.table_schema='public' AND g.table_name='gov_workspace_events'
           AND g.privilege_type='SELECT'
           AND g.grantee <> CURRENT_USER AND g.grantee <> 'PUBLIC'
    LOOP
        EXECUTE format('GRANT SELECT ON gov_workspace_v02_events, gov_workspace_v02_contexts, gov_workspace_v02_assets TO %I',
                       grantee_name);
    END LOOP;
    -- Mirror INSERT only for writers of the 0.1 table.
    FOR grantee_name IN
        SELECT DISTINCT g.grantee
          FROM information_schema.role_table_grants g
         WHERE g.table_schema='public' AND g.table_name='gov_workspace_events'
           AND g.privilege_type='INSERT'
           AND g.grantee <> CURRENT_USER AND g.grantee <> 'PUBLIC'
    LOOP
        EXECUTE format('GRANT INSERT ON gov_workspace_v02_events, gov_workspace_v02_contexts, gov_workspace_v02_assets TO %I',
                       grantee_name);
    END LOOP;
END
$grants$;
