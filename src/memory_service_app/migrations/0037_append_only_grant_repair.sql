-- 0037: repair the runtime grants of 0027, 0030 and 0036.
--
-- Those migrations granted SELECT, INSERT on a new append-only table to every
-- role that held SELECT on an existing table (gov_method_state for 0027,
-- gov_object_revisions for 0030 and 0036).  That could promote a read-only
-- reporting or review role to a writer: the same widening 0028 repaired for
-- the workspace 0.2 tables.  Append-only only forbids changing history; it
-- does not stop a reader from adding commitments, events or context packs.
-- The applied migrations stay immutable; this additive, idempotent repair
-- removes INSERT from every role that has no INSERT on the source table.
-- SELECT is left as granted, and PUBLIC is never granted anything.
DO $grants$
DECLARE
    pair record;
    grantee_name text;
BEGIN
    FOR pair IN
        SELECT * FROM (VALUES
            ('gov_method_commitments', 'gov_method_state'),
            ('gov_world_events', 'gov_object_revisions'),
            ('gov_world_context_packs', 'gov_object_revisions')
        ) AS t(target, source)
    LOOP
        FOR grantee_name IN
            SELECT DISTINCT g.grantee
              FROM information_schema.role_table_grants g
             WHERE g.table_schema='public' AND g.table_name=pair.target
               AND g.privilege_type='INSERT'
               AND g.grantee <> CURRENT_USER AND g.grantee <> 'PUBLIC'
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.role_table_grants s
                    WHERE s.table_schema='public' AND s.table_name=pair.source
                      AND s.privilege_type='INSERT' AND s.grantee=g.grantee)
        LOOP
            EXECUTE format('REVOKE INSERT ON %I FROM %I', pair.target, grantee_name);
        END LOOP;
    END LOOP;
END
$grants$;
