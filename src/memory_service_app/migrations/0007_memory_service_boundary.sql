-- Separate the reusable memory service from distill-owned document storage.
-- Source references retain immutable excerpt/hash and locator snapshots; no service table
-- has a foreign key to document_fragments after this migration.

ALTER TABLE semantic_entity_source_refs
    ADD COLUMN IF NOT EXISTS source_locator_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE semantic_relation_source_refs
    ADD COLUMN IF NOT EXISTS source_locator_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE wm_version_source_refs
    ADD COLUMN IF NOT EXISTS source_locator_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;

UPDATE semantic_entity_source_refs ref
   SET source_locator_snapshot = jsonb_strip_nulls(jsonb_build_object(
       'document_id', f.document_id,
       'filename', d.filename,
       'heading_path', f.heading_path,
       'page_no', f.page_no,
       'paragraph_pos', f.paragraph_pos,
       'chunk_index', f.chunk_index,
       'fragment_ordinal', f.fragment_ordinal
   ))
  FROM document_fragments f
  JOIN documents d
    ON d.document_id=f.document_id
   AND d.tenant_id=f.tenant_id
   AND d.organization_id=f.organization_id
 WHERE ref.fragment_id=f.fragment_id
   AND ref.tenant_id=f.tenant_id
   AND ref.organization_id=f.organization_id
   AND ref.source_locator_snapshot = '{}'::jsonb;

UPDATE semantic_relation_source_refs ref
   SET source_locator_snapshot = jsonb_strip_nulls(jsonb_build_object(
       'document_id', f.document_id,
       'filename', d.filename,
       'heading_path', f.heading_path,
       'page_no', f.page_no,
       'paragraph_pos', f.paragraph_pos,
       'chunk_index', f.chunk_index,
       'fragment_ordinal', f.fragment_ordinal
   ))
  FROM document_fragments f
  JOIN documents d
    ON d.document_id=f.document_id
   AND d.tenant_id=f.tenant_id
   AND d.organization_id=f.organization_id
 WHERE ref.fragment_id=f.fragment_id
   AND ref.tenant_id=f.tenant_id
   AND ref.organization_id=f.organization_id
   AND ref.source_locator_snapshot = '{}'::jsonb;

UPDATE wm_version_source_refs ref
   SET source_locator_snapshot = jsonb_strip_nulls(jsonb_build_object(
       'document_id', f.document_id,
       'filename', d.filename,
       'heading_path', f.heading_path,
       'page_no', f.page_no,
       'paragraph_pos', f.paragraph_pos,
       'chunk_index', f.chunk_index,
       'fragment_ordinal', f.fragment_ordinal
   ))
  FROM document_fragments f
  JOIN documents d
    ON d.document_id=f.document_id
   AND d.tenant_id=f.tenant_id
   AND d.organization_id=f.organization_id
 WHERE ref.fragment_id=f.fragment_id
   AND ref.tenant_id=f.tenant_id
   AND ref.organization_id=f.organization_id
   AND ref.source_locator_snapshot = '{}'::jsonb;

ALTER TABLE semantic_entity_source_refs DROP CONSTRAINT IF EXISTS fk_esr_fragment;
ALTER TABLE semantic_relation_source_refs DROP CONSTRAINT IF EXISTS fk_rsr_fragment;
ALTER TABLE wm_version_source_refs DROP CONSTRAINT IF EXISTS fk_wm_vsr_fragment;

-- The retired M1-A/current_records table was already replaced by Working Memory.
-- Refuse to erase unexpected user data on an existing installation.
DO $drop_retired_business_records$
DECLARE
    has_rows boolean;
BEGIN
    IF to_regclass('business_records') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM business_records LIMIT 1)' INTO has_rows;
        IF has_rows THEN
            RAISE EXCEPTION 'business_records contains data; migrate it before applying 0007';
        END IF;
    END IF;
END
$drop_retired_business_records$;
DROP TABLE IF EXISTS business_records;
