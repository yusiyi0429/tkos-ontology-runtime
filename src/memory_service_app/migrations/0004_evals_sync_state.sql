-- 0004_evals_sync_state: 评测集同步状态表（WP17b-4）
-- 用途：aw evals sync 的本地去重账本——记录哪些 audit_id 已同步进 LangSmith Dataset，
-- 替代此前"全量拉取远端 dataset 内全部 example 的 metadata.audit_id"的 O(N) 判重方式。
-- 治理语义不变：audit 记录本身不可变，本表只是同步进度指针的物化，删掉可全量重建。
CREATE TABLE IF NOT EXISTS evals_sync_state (
    audit_id   text PRIMARY KEY,
    synced_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evals_sync_state_synced_at_idx ON evals_sync_state(synced_at);
