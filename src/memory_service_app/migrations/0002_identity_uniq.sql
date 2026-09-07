-- 0002_identity_uniq：users 表按 kind 分区加唯一约束（WP16-CR P1 修复）。
--
-- 背景：identity.py 的 bootstrap_local_user / get_agent_service_user 原来是
-- "SELECT 判存在再 INSERT"，并发下（两个进程同时首次启动）会各自插入一条，
-- 一个人的本地身份/一个 Agent 服务账号被悄悄拆成两条。改成
-- INSERT ... ON CONFLICT DO NOTHING 需要这里的唯一索引作为冲突推断目标。
--
-- 可重入：CREATE ... IF NOT EXISTS；执行前先做防御性 dedupe（不删除任何数据，
-- 只把非最早创建的重复行改名腾位置，审计/外键不受影响——users 表目前没有
-- 被其它表以 display_name 为外键引用，只有 user_id，改名安全）。

-- kind='human'：按 display_name 分组，保留最早创建的一条，其余改名腾位置
WITH ranked AS (
    SELECT user_id, display_name,
           row_number() OVER (PARTITION BY display_name ORDER BY created_at) AS rn
    FROM users WHERE kind = 'human'
)
UPDATE users u
SET display_name = u.display_name || '-dup-' || substr(u.user_id::text, 1, 8)
FROM ranked r
WHERE u.user_id = r.user_id AND r.rn > 1;

-- kind='agent_service'：全局至多保留一条（不按 display_name 分组，本来就该只有一条）
WITH ranked AS (
    SELECT user_id, row_number() OVER (ORDER BY created_at) AS rn
    FROM users WHERE kind = 'agent_service'
)
UPDATE users u
SET display_name = u.display_name || '-dup-' || substr(u.user_id::text, 1, 8)
FROM ranked r
WHERE u.user_id = r.user_id AND r.rn > 1;

-- kind='human' 下 display_name 唯一（同一本机用户名只允许一条稳定身份）
CREATE UNIQUE INDEX IF NOT EXISTS users_human_display_name_uniq
    ON users(display_name) WHERE kind = 'human';

-- kind='agent_service' 全局至多一条（用常量表达式做分区唯一索引，与具体列值无关）
CREATE UNIQUE INDEX IF NOT EXISTS users_agent_service_singleton
    ON users((1)) WHERE kind = 'agent_service';
