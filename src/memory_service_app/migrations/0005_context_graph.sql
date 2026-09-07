-- WP18a Context Graph v2.2 migration.
-- Generated directly from docs/设计-ContextGraph-Schema草案.md M1-M11.
-- M12 (wm_version_source_refs) is intentionally owned by 0006 / WP18d.
-- Destructive graph switching and legacy deletion are deliberately out of scope.

CREATE TABLE context_graph_versions (
    generation_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text NOT NULL,
    organization_id text NOT NULL,
    label           text NOT NULL,
    status          text NOT NULL DEFAULT 'shadow'
                    CHECK (status IN ('shadow','current','retired')),
    baseline_audit_seq bigint NOT NULL DEFAULT 0 CHECK (baseline_audit_seq >= 0),
    created_at      timestamptz NOT NULL DEFAULT now(),
    retired_at      timestamptz,
    CONSTRAINT uq_cgv_scope UNIQUE (generation_id, tenant_id, organization_id),
    CONSTRAINT uq_cgv_label UNIQUE (organization_id, tenant_id, label)
);

CREATE UNIQUE INDEX uq_cgv_current ON context_graph_versions(organization_id, tenant_id)
    WHERE status='current';

CREATE TABLE strategic_periods (
    tenant_id       text NOT NULL,
    organization_id text NOT NULL,
    period_code     text NOT NULL
                    CHECK (period_code = btrim(period_code) AND length(btrim(period_code)) > 0),
    starts_on       date NOT NULL,
    ends_on         date NOT NULL CHECK (ends_on > starts_on),
    PRIMARY KEY (tenant_id, organization_id, period_code)
);

ALTER TABLE documents
    ADD CONSTRAINT uq_documents_scope UNIQUE (document_id, tenant_id, organization_id);

CREATE TABLE document_fragments (
    fragment_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     uuid NOT NULL,
    tenant_id       text NOT NULL,
    organization_id text NOT NULL,
    chunk_index     int  NOT NULL CHECK (chunk_index >= 0),
    fragment_ordinal int NOT NULL CHECK (fragment_ordinal >= 0),
    heading_path    text,
    page_no         int  CHECK (page_no IS NULL OR page_no > 0),
    paragraph_pos   int  CHECK (paragraph_pos IS NULL OR paragraph_pos > 0),
    excerpt         text NOT NULL,
    content_hash    text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_frag_locator CHECK (
        heading_path IS NOT NULL OR page_no IS NOT NULL OR paragraph_pos IS NOT NULL
    ),
    CONSTRAINT uq_frag_scope UNIQUE (fragment_id, tenant_id, organization_id),
    CONSTRAINT uq_frag_ordinal UNIQUE (document_id, chunk_index, fragment_ordinal),
    CONSTRAINT fk_frag_document FOREIGN KEY (document_id, tenant_id, organization_id)
        REFERENCES documents (document_id, tenant_id, organization_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_frag_document ON document_fragments(document_id);

CREATE INDEX idx_frag_doc_hash ON document_fragments(document_id, content_hash);

CREATE FUNCTION reject_document_fragment_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $fragment_immutable$
BEGIN
    RAISE EXCEPTION 'document_fragments is append-only; insert a new fragment instead'
        USING ERRCODE = '55000';
END;
$fragment_immutable$;

CREATE TRIGGER trg_document_fragments_append_only
BEFORE UPDATE OR DELETE ON document_fragments
FOR EACH ROW EXECUTE FUNCTION reject_document_fragment_mutation();

INSERT INTO context_graph_versions(generation_id, tenant_id, organization_id, label, status)
VALUES ('00000000-0000-0000-0000-0000000000e1', 'local', 'local-org', 'legacy-phase1', 'current')
ON CONFLICT (generation_id) DO NOTHING;

ALTER TABLE semantic_entities
    ADD COLUMN type_key text,
    ADD COLUMN rationale text,
    ADD COLUMN confirmed_by uuid REFERENCES users(user_id),
    ADD COLUMN confirmed_at timestamptz,
    ADD COLUMN graph_generation_id uuid,
    ADD COLUMN strategic_level text,
    ADD COLUMN strategic_period text,
    ADD COLUMN outcome_level text,
    ADD COLUMN status_scope text,
    ADD COLUMN org_subtype text,
    ADD COLUMN is_moat boolean;

ALTER TABLE semantic_relations
    ADD COLUMN rationale text,
    ADD COLUMN confirmed_by uuid REFERENCES users(user_id),
    ADD COLUMN confirmed_at timestamptz,
    ADD COLUMN graph_generation_id uuid,
    ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE memory_proposals
    ADD COLUMN graph_generation_id uuid;

ALTER TABLE memory_audit
    ADD COLUMN audit_seq bigint GENERATED ALWAYS AS IDENTITY,
    ADD CONSTRAINT uq_memory_audit_seq UNIQUE (audit_seq);

ALTER TABLE memory_audit
    ALTER COLUMN audit_seq SET NOT NULL;

ALTER TABLE documents
    ADD COLUMN content_hash text CHECK (content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'),
    ADD COLUMN file_version text;

UPDATE semantic_entities SET graph_generation_id = '00000000-0000-0000-0000-0000000000e1';

UPDATE semantic_relations SET graph_generation_id = '00000000-0000-0000-0000-0000000000e1';

UPDATE memory_proposals SET graph_generation_id = '00000000-0000-0000-0000-0000000000e1';

UPDATE semantic_entities e SET confirmed_by = a.decided_by, confirmed_at = a.decided_at
  FROM (SELECT DISTINCT ON (target_id) target_id, decided_by, decided_at
          FROM memory_audit
         WHERE target_kind = 'entity' AND decision IN ('confirmed','approved')
         ORDER BY target_id, decided_at DESC) a
 WHERE e.entity_id = a.target_id AND e.confirmed_by IS NULL;

UPDATE semantic_relations r SET confirmed_by = a.decided_by, confirmed_at = a.decided_at
  FROM (SELECT DISTINCT ON (target_id) target_id, decided_by, decided_at
          FROM memory_audit
         WHERE target_kind = 'relation' AND decision IN ('confirmed','approved')
         ORDER BY target_id, decided_at DESC) a
 WHERE r.relation_id = a.target_id AND r.confirmed_by IS NULL;

ALTER TABLE semantic_entities DROP CONSTRAINT IF EXISTS semantic_entities_status_check;

ALTER TABLE semantic_relations DROP CONSTRAINT IF EXISTS semantic_relations_status_check;

ALTER TABLE memory_proposals  DROP CONSTRAINT IF EXISTS memory_proposals_status_check;

ALTER TABLE memory_audit     DROP CONSTRAINT IF EXISTS memory_audit_decision_check;

ALTER TABLE documents        DROP CONSTRAINT IF EXISTS documents_status_check;

UPDATE semantic_entities SET status='confirmed' WHERE status='active';

UPDATE semantic_relations SET status='confirmed' WHERE status='active';

UPDATE memory_proposals SET status='confirmed' WHERE status='approved';

UPDATE memory_proposals SET status='pending'    WHERE status='pending_approval';

UPDATE memory_audit    SET decision='confirmed' WHERE decision='approved';

ALTER TABLE semantic_entities  ALTER COLUMN status SET DEFAULT 'confirmed';

ALTER TABLE semantic_relations ALTER COLUMN status SET DEFAULT 'confirmed';

ALTER TABLE memory_proposals   ALTER COLUMN status SET DEFAULT 'pending';

ALTER TABLE semantic_entities
    ALTER COLUMN graph_generation_id SET NOT NULL,
    ALTER COLUMN confirmed_by SET NOT NULL,
    ALTER COLUMN confirmed_at SET NOT NULL;

ALTER TABLE semantic_relations
    ALTER COLUMN graph_generation_id SET NOT NULL,
    ALTER COLUMN confirmed_by SET NOT NULL,
    ALTER COLUMN confirmed_at SET NOT NULL;

ALTER TABLE memory_proposals
    ALTER COLUMN graph_generation_id SET NOT NULL;

ALTER TABLE semantic_entities
    ALTER COLUMN entity_type DROP NOT NULL,
    ADD CONSTRAINT ck_sem_ent_status CHECK (status IN ('confirmed','deprecated')),
    ADD CONSTRAINT ck_sem_ent_type_truth CHECK (
        (entity_type IS NOT NULL AND entity_type IN
            ('Organization','Strategy','Outcome','DecisionRecord','Evidence',
             'Assumption','Risk','Mission','Actor','Document') AND type_key IS NULL)
     OR (type_key IS NOT NULL AND type_key IN
            ('CompanyVision','OperatingPrinciple','ValueProposition','StrategicChoice',
             'BusinessModel','Organization','ProductArchitecture','ManagementMethod',
             'Outcome','OperatingStatus','OrganizationalCapability') AND entity_type IS NULL)
    ),
    ADD CONSTRAINT ck_sem_ent_level_strategic CHECK (
        (type_key IS DISTINCT FROM 'StrategicChoice' AND strategic_level IS NULL AND strategic_period IS NULL)
     OR (type_key = 'StrategicChoice' AND strategic_level IN ('enterprise','sub_strategy')
                               AND strategic_period IS NOT NULL)
    ),
    ADD CONSTRAINT ck_sem_ent_level_outcome CHECK (
        (type_key IS DISTINCT FROM 'Outcome' AND outcome_level IS NULL)
     OR (type_key = 'Outcome' AND outcome_level IN ('company','domain'))
    ),
    ADD CONSTRAINT ck_sem_ent_scope CHECK (
        (type_key IS DISTINCT FROM 'OperatingStatus' AND status_scope IS NULL)
     OR (type_key = 'OperatingStatus' AND status_scope IN ('company','outcome'))
    ),
    ADD CONSTRAINT ck_sem_ent_orgsub CHECK (
        (type_key IS DISTINCT FROM 'Organization' AND org_subtype IS NULL)
     OR (type_key = 'Organization' AND org_subtype IN
            ('company','business_unit','team','role','person'))
    ),
    ADD CONSTRAINT ck_sem_ent_moat CHECK (
        (type_key IS DISTINCT FROM 'OrganizationalCapability' AND is_moat IS NULL)
     OR (type_key = 'OrganizationalCapability' AND is_moat IS NOT NULL)
    );

ALTER TABLE semantic_relations
    ADD CONSTRAINT ck_sem_rel_status CHECK (status IN ('confirmed','deprecated')),
    ADD CONSTRAINT ck_sem_rel_no_self CHECK (source_id <> target_id),
    ADD CONSTRAINT ck_sem_rel_conflict_canonical CHECK (
        relation_type <> 'conflicts_with'
        OR graph_generation_id = '00000000-0000-0000-0000-0000000000e1'
        OR source_id < target_id
    ),
    ADD CONSTRAINT ck_sem_rel_type CHECK (relation_type IN
        ('primary_alignment','sub_strategy_of','domain_outcome_of',
         'addresses','assigned_to','assigns','changes','complements','confirms',
         'conflicts_with','defines','defines_responsibility','depends_on','describes',
         'enables','executes','has_outcome','influences','input_to','instance_of',
         'involved_in','is_input_to','leads_to','owned_by','owns','participates_in',
         'part_of','produces','provides','records','responsible_for','supports','works_at'));

ALTER TABLE memory_proposals
    ADD CONSTRAINT ck_prop_status CHECK (status IN ('pending','confirmed','rejected'));

ALTER TABLE memory_audit
    ADD CONSTRAINT ck_audit_decision CHECK (decision IN ('confirmed','rejected'));

ALTER TABLE documents
    ADD CONSTRAINT ck_documents_status CHECK (status IN
        ('ingested','parsed','extracted','failed','shadow_extracted','retired'));

ALTER TABLE semantic_entities
    ADD CONSTRAINT uq_sem_ent_scope UNIQUE (entity_id, graph_generation_id, tenant_id, organization_id),
    ADD CONSTRAINT fk_sem_ent_generation FOREIGN KEY
        (graph_generation_id, tenant_id, organization_id)
        REFERENCES context_graph_versions (generation_id, tenant_id, organization_id),
    ADD CONSTRAINT fk_sem_ent_period FOREIGN KEY
        (tenant_id, organization_id, strategic_period)
        REFERENCES strategic_periods (tenant_id, organization_id, period_code);

ALTER TABLE semantic_relations
    ADD CONSTRAINT uq_sem_rel_scope UNIQUE (relation_id, graph_generation_id, tenant_id, organization_id),
    ADD CONSTRAINT fk_sem_rel_generation FOREIGN KEY
        (graph_generation_id, tenant_id, organization_id)
        REFERENCES context_graph_versions (generation_id, tenant_id, organization_id),
    ADD CONSTRAINT fk_sem_rel_source FOREIGN KEY
        (source_id, graph_generation_id, tenant_id, organization_id)
        REFERENCES semantic_entities (entity_id, graph_generation_id, tenant_id, organization_id),
    ADD CONSTRAINT fk_sem_rel_target FOREIGN KEY
        (target_id, graph_generation_id, tenant_id, organization_id)
        REFERENCES semantic_entities (entity_id, graph_generation_id, tenant_id, organization_id);

ALTER TABLE memory_proposals
    ADD CONSTRAINT fk_prop_generation FOREIGN KEY
        (graph_generation_id, tenant_id, organization_id)
        REFERENCES context_graph_versions (generation_id, tenant_id, organization_id);

CREATE UNIQUE INDEX uq_rel_primary_alignment
    ON semantic_relations(graph_generation_id, source_id)
    WHERE relation_type='primary_alignment' AND status='confirmed';

CREATE UNIQUE INDEX uq_rel_sub_strategy
    ON semantic_relations(graph_generation_id, source_id)
    WHERE relation_type='sub_strategy_of' AND status='confirmed';

CREATE UNIQUE INDEX uq_rel_domain_outcome
    ON semantic_relations(graph_generation_id, source_id)
    WHERE relation_type='domain_outcome_of' AND status='confirmed';

CREATE UNIQUE INDEX uq_ent_vision_root
    ON semantic_entities(graph_generation_id, organization_id, tenant_id)
    WHERE type_key='CompanyVision' AND status='confirmed';

CREATE UNIQUE INDEX uq_ent_enterprise_strategy
    ON semantic_entities(graph_generation_id, organization_id, tenant_id, strategic_period)
    WHERE type_key='StrategicChoice' AND strategic_level='enterprise' AND status='confirmed';

CREATE UNIQUE INDEX uq_rel_confirmed_dedup
    ON semantic_relations(graph_generation_id, tenant_id, organization_id,
                          relation_type, source_id, target_id)
    WHERE status='confirmed';

DROP INDEX IF EXISTS semantic_entities_active_uniq;

CREATE UNIQUE INDEX uq_ent_confirmed_identity
    ON semantic_entities(graph_generation_id, organization_id, tenant_id,
                         (coalesce(type_key, entity_type)), normalized_name)
    WHERE status='confirmed';

DROP INDEX IF EXISTS semantic_relations_src_idx;

DROP INDEX IF EXISTS semantic_relations_tgt_idx;

DROP INDEX IF EXISTS memory_proposals_pending_idx;

CREATE INDEX semantic_relations_src_idx ON semantic_relations(source_id)
    WHERE status='confirmed';

CREATE INDEX semantic_relations_tgt_idx ON semantic_relations(target_id)
    WHERE status='confirmed';

CREATE INDEX memory_proposals_pending_idx ON memory_proposals(organization_id)
    WHERE status='pending';

CREATE TABLE context_graph_snapshots (
    snapshot_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_generation_id uuid NOT NULL,
    tenant_id         text NOT NULL,
    organization_id   text NOT NULL,
    s3_key            text NOT NULL,
    snapshot_sha256   text NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    entity_count      int  NOT NULL CHECK (entity_count >= 0),
    relation_count    int  NOT NULL CHECK (relation_count >= 0),
    hash_verified     boolean NOT NULL DEFAULT false,
    hash_verified_by  uuid REFERENCES users(user_id),
    hash_verified_at  timestamptz,
    recovery_drill_passed boolean NOT NULL DEFAULT false,
    recovery_drill_by uuid REFERENCES users(user_id),
    recovery_drill_at timestamptz,
    recovery_drill_report_key text,
    created_by        uuid NOT NULL REFERENCES users(user_id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_snap_scope UNIQUE (snapshot_id, graph_generation_id, tenant_id, organization_id),
    CONSTRAINT fk_snap_generation FOREIGN KEY
        (graph_generation_id, tenant_id, organization_id)
        REFERENCES context_graph_versions (generation_id, tenant_id, organization_id),
    CONSTRAINT ck_snap_hash_audit CHECK (
        (hash_verified AND hash_verified_by IS NOT NULL AND hash_verified_at IS NOT NULL)
     OR (NOT hash_verified AND hash_verified_by IS NULL AND hash_verified_at IS NULL)
    ),
    CONSTRAINT ck_snap_drill_audit CHECK (
        (recovery_drill_passed AND recovery_drill_by IS NOT NULL AND recovery_drill_at IS NOT NULL
                                 AND recovery_drill_report_key IS NOT NULL)
     OR (NOT recovery_drill_passed AND recovery_drill_by IS NULL AND recovery_drill_at IS NULL
                                     AND recovery_drill_report_key IS NULL)
    )
);

CREATE TABLE context_graph_switch_log (
    switch_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id text NOT NULL,
    tenant_id       text NOT NULL,
    from_generation uuid NOT NULL,
    to_generation   uuid NOT NULL,
    snapshot_id     uuid NOT NULL,
    reconcile_report_key    text NOT NULL,
    reconcile_report_sha256 text NOT NULL CHECK (reconcile_report_sha256 ~ '^[0-9a-f]{64}$'),
    reconcile_delta_count   int  NOT NULL CHECK (reconcile_delta_count >= 0),
    query_replay_report_key text NOT NULL,
    query_replay_sha256     text NOT NULL CHECK (query_replay_sha256 ~ '^[0-9a-f]{64}$'),
    switched_by     uuid NOT NULL REFERENCES users(user_id),
    switched_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_sw_from FOREIGN KEY (from_generation, tenant_id, organization_id)
        REFERENCES context_graph_versions (generation_id, tenant_id, organization_id),
    CONSTRAINT fk_sw_to   FOREIGN KEY (to_generation, tenant_id, organization_id)
        REFERENCES context_graph_versions (generation_id, tenant_id, organization_id),
    CONSTRAINT fk_sw_snapshot FOREIGN KEY
        (snapshot_id, from_generation, tenant_id, organization_id)
        REFERENCES context_graph_snapshots (snapshot_id, graph_generation_id, tenant_id, organization_id),
    CONSTRAINT ck_sw_gens CHECK (from_generation <> to_generation)
);

CREATE TABLE semantic_entity_source_refs (
    entity_id            uuid NOT NULL,
    graph_generation_id  uuid NOT NULL,
    tenant_id            text NOT NULL,
    organization_id      text NOT NULL,
    fragment_id          uuid NOT NULL,
    ordinal              int  NOT NULL CHECK (ordinal >= 1),
    excerpt_snapshot     text NOT NULL,
    content_hash_snapshot text NOT NULL CHECK (content_hash_snapshot ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (entity_id, fragment_id),
    CONSTRAINT uq_esr_ordinal UNIQUE (entity_id, ordinal),
    CONSTRAINT fk_esr_entity FOREIGN KEY
        (entity_id, graph_generation_id, tenant_id, organization_id)
        REFERENCES semantic_entities (entity_id, graph_generation_id, tenant_id, organization_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_esr_fragment FOREIGN KEY (fragment_id, tenant_id, organization_id)
        REFERENCES document_fragments (fragment_id, tenant_id, organization_id)
        ON DELETE RESTRICT
);

CREATE TABLE semantic_relation_source_refs (
    relation_id          uuid NOT NULL,
    graph_generation_id  uuid NOT NULL,
    tenant_id            text NOT NULL,
    organization_id      text NOT NULL,
    fragment_id          uuid NOT NULL,
    ordinal              int  NOT NULL CHECK (ordinal >= 1),
    excerpt_snapshot     text NOT NULL,
    content_hash_snapshot text NOT NULL CHECK (content_hash_snapshot ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (relation_id, fragment_id),
    CONSTRAINT uq_rsr_ordinal UNIQUE (relation_id, ordinal),
    CONSTRAINT fk_rsr_relation FOREIGN KEY
        (relation_id, graph_generation_id, tenant_id, organization_id)
        REFERENCES semantic_relations (relation_id, graph_generation_id, tenant_id, organization_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_rsr_fragment FOREIGN KEY (fragment_id, tenant_id, organization_id)
        REFERENCES document_fragments (fragment_id, tenant_id, organization_id)
        ON DELETE RESTRICT
);
