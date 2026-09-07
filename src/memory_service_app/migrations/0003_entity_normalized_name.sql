-- 0003_entity_normalized_name：semantic_entities 加 normalized_name 列 + 部分唯一索引
-- （WP16-CR P1 根治：占位符解析之前只能在"命中多个同名实体"时事后报错，现在从写入路径
-- 就不可能产生同组织/租户/类型下的重复活跃实体）。
--
-- 规整化规则与 aw.distill.reduce.normalize_name（Python 单一实现：strip+小写+折叠空白）
-- 保持一致；写入路径（governance.py 的 approve）新增/更新实体时都会用该函数重新计算
-- normalized_name，这里的 SQL 表达式只用于一次性 backfill 存量数据。
--
-- 可重入：ADD COLUMN/CREATE INDEX 都带 IF NOT EXISTS；建唯一索引前先做防御性 dedupe
-- （不删除、不改名，只把非 canonical 的重复行降级为 deprecated——semantic_relations 的
-- 外键引用不受影响，被降级的实体仍可通过审计/历史查到）。

ALTER TABLE semantic_entities ADD COLUMN IF NOT EXISTS normalized_name text;

UPDATE semantic_entities
SET normalized_name = lower(btrim(regexp_replace(name, '\s+', ' ', 'g')))
WHERE normalized_name IS NULL;

-- 防御性 dedupe：CREATE UNIQUE INDEX 前，若已有重复 active 实体
-- （同 organization_id+tenant_id+entity_type+normalized_name），保留最近更新的一条为
-- canonical，其余降级为 deprecated。
WITH ranked AS (
    SELECT entity_id,
           row_number() OVER (
               PARTITION BY organization_id, tenant_id, entity_type, normalized_name
               ORDER BY updated_at DESC, entity_id
           ) AS rn
    FROM semantic_entities
    WHERE status = 'active'
)
UPDATE semantic_entities e
SET status = 'deprecated', revision = revision + 1, updated_at = now()
FROM ranked r
WHERE e.entity_id = r.entity_id AND r.rn > 1;

ALTER TABLE semantic_entities ALTER COLUMN normalized_name SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS semantic_entities_active_uniq
    ON semantic_entities(organization_id, tenant_id, entity_type, normalized_name)
    WHERE status = 'active';
