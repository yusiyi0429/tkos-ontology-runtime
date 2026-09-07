-- Working Memory Schema v3.3（WP18d）。
-- 依据 docs/设计-WorkingMemory-Schema草案.md（v3.3，冻结）§3、§13：六张核心表
-- （wm_issue_chains / wm_objects / wm_object_versions / wm_issue_signals /
-- wm_agreement_parties / wm_agreement_confirmations）+ 一张与 Context Graph 共享的
-- 集成表（wm_version_source_refs，不计入六表核心计数）。
--
-- 全部 DDL 逐字复制自设计文档的 sql 代码块（与 scripts/verify_wm_ddl.py 回放并通过
-- 的 19 条语句、7 张表、20 个行为断言一致，最后一次核对时间：v3.3 冻结当轮），
-- 未做任何改写；文档更新则本文件需同步重新逐字核对。
--
-- 依赖：本迁移假定 document_fragments(fragment_id, tenant_id, organization_id) 已存在
-- （Context Graph 交付，参见 docs/设计-ContextGraph-Schema草案.md，随其自身迁移落库，
-- 编号先于本迁移；本迁移文件不创建、不修改该表）。
--
-- WP18d 范围：只建表结构，不种子、不删除 business_records、不改 TUI/CLI（§8.1 段一）。
-- 本迁移不在本次工作中执行（不 aw db migrate），只落盘等待正式迁移窗口。

-- ===== §3.1 wm_issue_chains —— 议题链 =====
CREATE TABLE wm_issue_chains (
    chain_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           text NOT NULL DEFAULT 'local',
    organization_id     text NOT NULL DEFAULT 'local-org',
    title               text NOT NULL,              -- 人类可读主线名，{{对象类型:主线名}} 按它解析
    status              text NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','monitoring','closed')),
    derived_from_chain  uuid,
    -- v3.2 ⑤：禁止自指；来源链必须真实存在且同 tenant+org（复合 FK，DDL 级硬约束）
    -- 目标唯一约束见下方 uq_wm_chains_scope
    CONSTRAINT ck_wm_no_self_derive CHECK (derived_from_chain IS NULL OR derived_from_chain <> chain_id),
    CONSTRAINT fk_wm_derive_same_scope FOREIGN KEY (derived_from_chain, tenant_id, organization_id)
        REFERENCES wm_issue_chains (chain_id, tenant_id, organization_id),
                        -- Issue 分裂时新链记录来源链；原链保持线性（§5.1）
    created_by          uuid NOT NULL REFERENCES users(user_id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    closed_at           timestamptz,

    -- v3.2 ⑤：复合 FK 目标（表内约束，先于下方 fk_wm_derive_same_scope 存在）
    CONSTRAINT uq_wm_chains_scope UNIQUE (chain_id, tenant_id, organization_id)
);

-- 主线名按 tenant+org 内唯一（v2 ⑤）：{{对象类型:主线名}} 的确定性解析依赖它
CREATE UNIQUE INDEX uq_wm_chains_title
    ON wm_issue_chains (tenant_id, organization_id, lower(title));
CREATE INDEX idx_wm_chains_org ON wm_issue_chains (tenant_id, organization_id, status);
-- v3.2 ⑤：scope 唯一性已改为表内 UNIQUE 表约束（见 CREATE TABLE 内），供复合 FK 引用。

-- ===== §3.2 wm_objects —— 稳定对象身份行 =====
CREATE TABLE wm_objects (
    object_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    chain_id      uuid NOT NULL REFERENCES wm_issue_chains(chain_id),
    object_type   text NOT NULL CHECK (object_type IN
                  ('Signal','Issue','Judgment','Agreement','Strategic Mission','Close')),
                  -- 六类封闭词表（§5.2）；Research Task/Result 不在白名单（验收 12）
    issue_id      uuid,      -- 非 Signal 类必填、指向本链 Issue 身份行；见下方复合外键与 CHECK
    created_by    uuid NOT NULL REFERENCES users(user_id),
    created_at    timestamptz NOT NULL DEFAULT now(),

    -- Issue 的稳定标识就是自身：issue_id 恒等于 object_id（“至多一个稳定 Issue”的锚定方式）
    -- v3 ①：三分支——Signal 不挂 Issue；Issue 自锚；其余四类必挂非自身 Issue
    CONSTRAINT ck_wm_issue_selfref CHECK (
        (object_type = 'Signal' AND issue_id IS NULL)
        OR (object_type = 'Issue'   AND issue_id = object_id)
        OR (object_type NOT IN ('Signal','Issue') AND issue_id IS NOT NULL AND issue_id <> object_id)
    ),

    -- 复合外键目标（v3 ②）：表内 UNIQUE 表约束，建表语句内即存在，供复合 FK 引用
    CONSTRAINT uq_wm_obj_chain UNIQUE (object_id, chain_id),
    CONSTRAINT uq_wm_obj_type  UNIQUE (object_id, object_type)   -- 供版本表/关系表的类型复合 FK 使用
);

-- 复合外键目标已改为 §3.2 表内 UNIQUE 表约束（v3 ②），此处不再另建索引。

-- 非 Signal 类的 issue_id 必须指向【同一条链上的 Issue 身份行】：
-- 复合外键把 (issue_id, chain_id) 绑定到身份行的 (object_id, chain_id)，跨链引用直接违反 FK
ALTER TABLE wm_objects
    ADD CONSTRAINT fk_wm_issue_same_chain
    FOREIGN KEY (issue_id, chain_id) REFERENCES wm_objects (object_id, chain_id);
-- 注：FK 保证"目标行存在且同链"；"目标行 object_type='Issue'"由 service 层校验
--（PG 外键无法约束目标行类型；此为显式声明的逻辑校验点，不是伪外键）。

-- 每链对象数约束（v2 ① 的裁定，见 §3.2.1）：Issue/Judgment/Agreement/Strategic Mission/Close
-- 每链至多一个稳定对象；Signal 不限（多条 Signal 各自是独立稳定对象，各有版本链）
CREATE UNIQUE INDEX uq_wm_one_issue_per_chain     ON wm_objects (chain_id) WHERE object_type = 'Issue';
CREATE UNIQUE INDEX uq_wm_one_judgment_per_chain  ON wm_objects (chain_id) WHERE object_type = 'Judgment';
CREATE UNIQUE INDEX uq_wm_one_agreement_per_chain ON wm_objects (chain_id) WHERE object_type = 'Agreement';
CREATE UNIQUE INDEX uq_wm_one_mission_per_chain   ON wm_objects (chain_id) WHERE object_type = 'Strategic Mission';
CREATE UNIQUE INDEX uq_wm_one_close_per_chain     ON wm_objects (chain_id) WHERE object_type = 'Close';

-- ===== §3.3 wm_object_versions —— 版本行（核心表） =====
CREATE TABLE wm_object_versions (
    record_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                   -- 版本行 id；Agreement"确切 confirmed Judgment 版本"、Mission/Close 承接均按它引用
    object_id      uuid NOT NULL,
    object_type    text NOT NULL CHECK (object_type IN
                   ('Signal','Issue','Judgment','Agreement','Strategic Mission','Close')),
                   -- v3 ⑥：身份表类型的受控冗余（由复合 FK 保证与身份行一致，见下），
                   -- 使“某列仅某类型版本行可填”能用原生 CHECK 表达，无需触发器
    version        int NOT NULL CHECK (version >= 1),
    supersedes     uuid,      -- 指向被本版替代的上一版本行；首版 NULL。复合 FK 见下（同对象硬保证）
    issue_state    text CHECK (issue_state IN ('potential','strategic','closed')),
                   -- 仅 Issue 的版本行可填（原生 CHECK，见下）
    confirmation_status text NOT NULL DEFAULT 'unconfirmed'
                   CHECK (confirmation_status IN ('unconfirmed','confirmed')),
    content        jsonb NOT NULL DEFAULT '{}',
                   -- 分类型薄 schema 由 profiles v2 校验（§6）；Agreement 的 disposition 存此
    -- v3.3：删除 source_refs jsonb 列。来源引用改为真关联表 wm_version_source_refs
    -- （§3.4），不保留 JSON 双写；API 只读投影从关联表现场拼。
    confirmed_judgment_record_id uuid REFERENCES wm_object_versions(record_id),
                   -- 仅 Agreement 版本行可填：确切 confirmed Judgment 版本（§3.6）；真外键（v3 ⑧）
    agreement_record_id          uuid REFERENCES wm_object_versions(record_id),
                   -- 仅 Mission/Close 版本行可填：承接的 confirmed Agreement 版本（§3.6）；真外键（v3 ⑧）
    created_by     uuid NOT NULL REFERENCES users(user_id),
    confirmed_by   uuid REFERENCES users(user_id),  -- 单确认人快照（Agreement 多人语义另表，§3.7）
    confirmed_at   timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),

    -- ===== 硬约束（v3 ②③⑥ 全部为原生 DDL，无触发器）=====

    -- v3 ②：复合 FK 目标用表内 UNIQUE 表约束，建表时即存在，排在 FK 之前
    CONSTRAINT uq_wm_ver_record_obj UNIQUE (record_id, object_id),
    CONSTRAINT uq_wm_obj_version    UNIQUE (object_id, version),

    -- v3 ⑥：冗余 object_type 必须与身份行一致（复合 FK）
    CONSTRAINT fk_wm_ver_obj_type FOREIGN KEY (object_id, object_type)
        REFERENCES wm_objects (object_id, object_type),

    -- v3 ③：链头与版本号的一致性——首版必无前驱，非首版必有前驱（拒 version=2 且 supersedes=NULL 的第二头）
    CONSTRAINT ck_wm_version_head CHECK (
        (version = 1 AND supersedes IS NULL)
        OR (version > 1 AND supersedes IS NOT NULL)
    ),

    -- v3 ⑧：禁止自环（supersedes 指向自身行）；version 严格连续（目标=version-1）由 service 保证，
    -- FK 不保证“更早版本”，只保证目标行存在且同对象
    CONSTRAINT ck_wm_no_self_supersede CHECK (supersedes IS NULL OR supersedes <> record_id),

    -- supersedes 必须指向同对象的上一版本行（复合 FK；NULL 不受 FK 约束）
    CONSTRAINT fk_wm_supersedes_same_object FOREIGN KEY (supersedes, object_id)
        REFERENCES wm_object_versions (record_id, object_id),

    -- v3 ⑥：类型适用性原生 CHECK（不再依赖触发器/拆表）
    -- v3.2 ①：确认状态与确认人/时间的三元一致性（原生 CHECK）
    CONSTRAINT ck_wm_confirmation_consistency CHECK (
        (confirmation_status = 'unconfirmed' AND confirmed_by IS NULL      AND confirmed_at IS NULL)
        OR (confirmation_status = 'confirmed'   AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)
    ),
    CONSTRAINT ck_wm_issue_state_type CHECK (
        object_type <> 'Issue' AND issue_state IS NULL
        OR object_type = 'Issue' AND issue_state IS NOT NULL
    ),
    CONSTRAINT ck_wm_agr_judgment_ref_type CHECK (
        (object_type = 'Agreement' AND confirmed_judgment_record_id IS NOT NULL)
        OR (object_type <> 'Agreement' AND confirmed_judgment_record_id IS NULL)
    ),
    CONSTRAINT ck_wm_agreement_ref_type CHECK (
        (object_type IN ('Strategic Mission','Close') AND agreement_record_id IS NOT NULL)
        OR (object_type NOT IN ('Strategic Mission','Close') AND agreement_record_id IS NULL)
    )
);

-- v3 ③：唯一链头（每对象至多一个无前驱版本），与 ck_wm_version_head、uq_wm_supersedes
-- 共同构成“一头且无分叉”的完整硬保证
CREATE UNIQUE INDEX uq_wm_chain_head ON wm_object_versions (object_id) WHERE supersedes IS NULL;

-- 防分叉：每个被替代版本至多一个直接后继（兼作后继查询索引）
CREATE UNIQUE INDEX uq_wm_supersedes ON wm_object_versions (supersedes) WHERE supersedes IS NOT NULL;

-- 查询索引：按对象取版本链 / 当前版（见 §4.2）
CREATE INDEX idx_wm_ver_object ON wm_object_versions (object_id, version DESC);

-- 版本行不带 tenant/org（v2 ④）：组织过滤一律 JOIN wm_issue_chains 派生

-- ===== §3.4 wm_version_source_refs —— 版本到文档片段的真关联表（v3.3，共享集成表，依赖 document_fragments） =====
CREATE TABLE wm_version_source_refs (
    record_id              uuid NOT NULL,
    object_id              uuid NOT NULL,
    chain_id               uuid NOT NULL,
    tenant_id              text NOT NULL,
    organization_id        text NOT NULL,
    fragment_id            uuid NOT NULL,
    ordinal                int  NOT NULL CHECK (ordinal >= 1),
                           -- 同一版本行内多条引用的展示顺序（从 1 开始，与 Context Graph 三表契约一致）
    excerpt_snapshot        text NOT NULL,
                           -- 确认时点的受控摘录快照（不是 fragment 自身的存量，是本版本引用它时的快照）
    content_hash_snapshot  text NOT NULL CHECK (content_hash_snapshot ~ '^[0-9a-f]{64}$'),
                           -- 确认时点的 fragment 内容 sha256 快照，用于离线导出/独立校验（见下方说明）
    created_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_wm_vsr PRIMARY KEY (record_id, fragment_id),
    CONSTRAINT uq_wm_vsr_ordinal UNIQUE (record_id, ordinal),

    -- owner FK：所属版本行被删除时级联删除引用（唯一的 CASCADE）
    CONSTRAINT fk_wm_vsr_version FOREIGN KEY (record_id, object_id)
        REFERENCES wm_object_versions (record_id, object_id) ON DELETE CASCADE,

    -- 对象/链所属需一致（防“版本属于 A 对象却写成 B 对象的 chain_id”）：显式 RESTRICT
    CONSTRAINT fk_wm_vsr_object FOREIGN KEY (object_id, chain_id)
        REFERENCES wm_objects (object_id, chain_id) ON DELETE RESTRICT,

    -- 链必须属于声明的 tenant+org（防跨链/跨 org 引用）：显式 RESTRICT
    CONSTRAINT fk_wm_vsr_chain FOREIGN KEY (chain_id, tenant_id, organization_id)
        REFERENCES wm_issue_chains (chain_id, tenant_id, organization_id) ON DELETE RESTRICT,

    -- fragment 必须属于同一 tenant+org（防跨组织引用其他组织的文档片段）：显式 RESTRICT
    CONSTRAINT fk_wm_vsr_fragment FOREIGN KEY (fragment_id, tenant_id, organization_id)
        REFERENCES document_fragments (fragment_id, tenant_id, organization_id) ON DELETE RESTRICT
);

CREATE INDEX idx_wm_vsr_fragment ON wm_version_source_refs (fragment_id);
-- 注：不单建 (record_id) 索引—— pk_wm_vsr 主键 (record_id, fragment_id) 已覆盖前缀查询，重复索引无意义。

-- ===== §3.6.1 wm_issue_signals —— Issue 由哪些 Signal 形成 =====
CREATE TABLE wm_issue_signals (
    chain_id         uuid NOT NULL,
    issue_object_id  uuid NOT NULL,
    signal_object_id uuid NOT NULL,
    noted_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (issue_object_id, signal_object_id),
    -- v3 ⑤：两组复合 FK 把“两端同链”做成硬约束（目标是 wm_objects 的表内 UNIQUE 约束）
    CONSTRAINT fk_wm_is_issue  FOREIGN KEY (issue_object_id,  chain_id)
        REFERENCES wm_objects (object_id, chain_id),
    CONSTRAINT fk_wm_is_signal FOREIGN KEY (signal_object_id, chain_id)
        REFERENCES wm_objects (object_id, chain_id),
    -- chain_id 本身必须是真实存在的链
    CONSTRAINT fk_wm_is_chain  FOREIGN KEY (chain_id) REFERENCES wm_issue_chains(chain_id)
);
-- service 校验（逻辑层，无法用 FK 表达）：issue_object_id 是 Issue 身份行、signal_object_id
-- 是 Signal 身份行（FK 只保证存在且同链，不保证目标类型）。
-- 对象级（不是版本级）：Signal 后续修订不影响形成关系。

-- ===== §3.7 wm_agreement_parties / wm_agreement_confirmations —— Agreement 多人确认 =====
CREATE TABLE wm_agreement_parties (        -- 有权确认人名单（版本级，required 语义）
    agreement_record_id uuid NOT NULL REFERENCES wm_object_versions(record_id),
    party               uuid NOT NULL REFERENCES users(user_id),
    added_by            uuid NOT NULL REFERENCES users(user_id),
    added_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agreement_record_id, party)
);

CREATE TABLE wm_agreement_confirmations (  -- 逐人确认记录（版本级，与 parties 同粒度）
    agreement_record_id uuid NOT NULL REFERENCES wm_object_versions(record_id),
    confirmer           uuid NOT NULL REFERENCES users(user_id),
    confirmed_at        timestamptz NOT NULL DEFAULT now(),
    note                text,
    PRIMARY KEY (agreement_record_id, confirmer)   -- 同一人对同一版本不重复确认
);
