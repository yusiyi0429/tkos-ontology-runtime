-- 0001_init: 多租户标识 + Conversation/Run/Event Store + 四层记忆权威表（docs/需求-阶段1.md §3/§9/§10/§14）
CREATE EXTENSION IF NOT EXISTS vector;

-- ============ 身份（§3：本地单用户也必须有稳定 user_id）============
CREATE TABLE users (
    user_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      text NOT NULL DEFAULT 'local',
    organization_id text NOT NULL DEFAULT 'local-org',
    display_name   text NOT NULL,
    kind           text NOT NULL DEFAULT 'human' CHECK (kind IN ('human','agent_service')),
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- ============ Conversation / Run / Run Event（§3.1、§4.6、§9.1）============
CREATE TABLE conversations (
    conversation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text NOT NULL,
    organization_id text NOT NULL,
    workspace_id    text NOT NULL DEFAULT 'default',
    user_id         uuid NOT NULL REFERENCES users(user_id),
    agent_id        text NOT NULL,
    title           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE runs (
    run_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversations(conversation_id),
    user_id         uuid NOT NULL REFERENCES users(user_id),
    agent_id        text NOT NULL,
    agent_version   text NOT NULL DEFAULT 'v1',
    mode            text NOT NULL DEFAULT 'chat' CHECK (mode IN ('chat','deep_research')),
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running','completed','failed','cancelled')),
    run_config      jsonb NOT NULL DEFAULT '{}',
    total_cost_usd  numeric(10,6) NOT NULL DEFAULT 0,
    turns_used      int NOT NULL DEFAULT 0,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

-- 不可变事件（Event Sourcing 权威，§9.1）
CREATE TABLE run_events (
    event_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES runs(run_id),
    sequence_number bigint NOT NULL,
    event_type      text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}',
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence_number)
);
CREATE INDEX run_events_run_idx ON run_events(run_id);
CREATE INDEX run_events_type_idx ON run_events(event_type);

-- ============ Episodic 派生：摘要 + 用户长期记忆（§9.2）============
CREATE TABLE episodic_summaries (
    summary_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversations(conversation_id),
    run_id          uuid REFERENCES runs(run_id),
    content         text NOT NULL,
    source_event_range jsonb NOT NULL DEFAULT '{}',
    model_used      text,
    embedding       halfvec(2048),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX episodic_summaries_conv_idx ON episodic_summaries(conversation_id);
CREATE INDEX episodic_summaries_emb_idx ON episodic_summaries
    USING hnsw (embedding halfvec_cosine_ops);

CREATE TABLE user_memories (
    memory_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(user_id),
    kind        text NOT NULL DEFAULT 'preference'
                CHECK (kind IN ('preference','ongoing_item','conclusion')),
    content     text NOT NULL,
    source      jsonb NOT NULL DEFAULT '{}',
    embedding   halfvec(2048),
    deleted     boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX user_memories_user_idx ON user_memories(user_id) WHERE NOT deleted;

-- ============ Semantic Memory：快照 + 图 + 提案 + 审计（§10）============
-- 10 类核心本体（§10.2），entity_type 用 text + CHECK 便于 Profile 扩展时放开
CREATE TABLE semantic_entities (
    entity_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text NOT NULL,
    organization_id text NOT NULL,
    entity_type     text NOT NULL CHECK (entity_type IN
        ('Organization','Strategy','Outcome','DecisionRecord','Evidence',
         'Assumption','Risk','Mission','Actor','Document')),
    name            text NOT NULL,
    content         jsonb NOT NULL DEFAULT '{}',
    revision        int  NOT NULL DEFAULT 1,
    status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','deprecated')),
    embedding       halfvec(2048),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX semantic_entities_org_idx ON semantic_entities(organization_id, entity_type);
CREATE INDEX semantic_entities_emb_idx ON semantic_entities
    USING hnsw (embedding halfvec_cosine_ops);

CREATE TABLE semantic_relations (
    relation_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text NOT NULL,
    organization_id text NOT NULL,
    source_id       uuid NOT NULL REFERENCES semantic_entities(entity_id),
    target_id       uuid NOT NULL REFERENCES semantic_entities(entity_id),
    relation_type   text NOT NULL,
    content         jsonb NOT NULL DEFAULT '{}',
    revision        int  NOT NULL DEFAULT 1,
    status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','deprecated')),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX semantic_relations_src_idx ON semantic_relations(source_id) WHERE status='active';
CREATE INDEX semantic_relations_tgt_idx ON semantic_relations(target_id) WHERE status='active';

-- 提案（§10.6：Agent 只能 propose，人审后生效）
CREATE TABLE memory_proposals (
    proposal_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text NOT NULL,
    organization_id text NOT NULL,
    target_kind     text NOT NULL CHECK (target_kind IN ('entity','relation')),
    target_id       uuid,                          -- NULL = 新建
    base_revision   int,                           -- 乐观并发（§10.5）
    action          text NOT NULL CHECK (action IN ('create','update','deprecate')),
    proposed_content jsonb NOT NULL,
    change_reason   text,
    evidence        jsonb NOT NULL DEFAULT '[]',   -- 证据链接（含蒸馏管线文档锚点）
    source          jsonb NOT NULL DEFAULT '{}',   -- run_id / import_session 等来源
    import_session_id uuid,                        -- 蒸馏管线批量分组（§11.4）
    confidence      real,
    proposed_by     uuid NOT NULL REFERENCES users(user_id),
    status          text NOT NULL DEFAULT 'pending_approval'
                    CHECK (status IN ('pending_approval','approved','rejected','superseded')),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX memory_proposals_pending_idx ON memory_proposals(organization_id)
    WHERE status='pending_approval';
CREATE INDEX memory_proposals_import_idx ON memory_proposals(import_session_id);

-- 审计（§10.5：every approve/reject 留痕）
CREATE TABLE memory_audit (
    audit_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id     uuid NOT NULL REFERENCES memory_proposals(proposal_id),
    target_kind     text NOT NULL,
    target_id       uuid,
    revision        int,
    before_content  jsonb,
    after_content   jsonb,
    decision        text NOT NULL CHECK (decision IN ('approved','rejected')),
    decided_by      uuid NOT NULL REFERENCES users(user_id),
    decision_note   text,
    decided_at      timestamptz NOT NULL DEFAULT now()
);

-- ============ 文档登记（§11 蒸馏管线 / §4.4 文件能力）============
CREATE TABLE documents (
    document_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    text NOT NULL,
    organization_id text NOT NULL,
    filename     text NOT NULL,
    content_type text,
    s3_key_raw   text NOT NULL,
    s3_key_parsed text,
    status       text NOT NULL DEFAULT 'ingested'
                 CHECK (status IN ('ingested','parsed','extracted','failed')),
    uploaded_by  uuid NOT NULL REFERENCES users(user_id),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ============ M1-A 业务记录（§7：权威在 PG 业务表，Profile 驱动）============
CREATE TABLE business_records (
    record_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text NOT NULL,
    organization_id text NOT NULL,
    profile         text NOT NULL DEFAULT 'm1a',
    record_type     text NOT NULL,     -- Signal / Judgment / Agreement / ...（Profile 定义）
    content         jsonb NOT NULL DEFAULT '{}',
    status          text NOT NULL DEFAULT 'active',
    created_by      uuid NOT NULL REFERENCES users(user_id),
    confirmed_by    uuid REFERENCES users(user_id),  -- Judgment/Agreement 的人类确认（§7）
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX business_records_org_idx ON business_records(organization_id, profile, record_type);
