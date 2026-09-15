# Clark 工作面场景的 Runtime 独立验收

验收对象为 Runtime API、PostgreSQL 和 MinIO，使用独立 scope／合成个人与 Agent 身份。Clark、模型服务、SSO、远程部署均不在 runner 中；不会修改 Clark 代码或启动浏览器。

## 环境与复跑

Python 3.12+、uv、Node.js。按 `acceptance/runtime/README.md` 准备本地验收 PostgreSQL/MinIO；当前本地工具预期 Docker `desktop-linux`、PostgreSQL `127.0.0.1:54350`。以下创建新数据库，保留旧库与所有卷。`BASE_ENV` 是已存在的私有本地验收环境文件，禁止提交或打印内容。

```bash
uv sync --frozen --extra s3

# 在 Runtime 独立工作区执行，替换 BASE_ENV 为私有 env.json 路径。
uv run python -m acceptance.method_independent.database create \
  --env-file BASE_ENV \
  --private .runtime-acceptance/workspace-db-NEW \
  --output artifacts/workspace-db-NEW

uv run python -m acceptance.workspace_scenes.bootstrap \
  --env-file .runtime-acceptance/workspace-db-NEW/env.json \
  --output artifacts/workspace-upgrade-NEW

uv run python -m acceptance.workspace_scenes.run \
  --env-file .runtime-acceptance/workspace-db-NEW/env.json \
  --private .runtime-acceptance/workspace-run-NEW \
  --output artifacts/workspace-run-NEW

uv run python -m acceptance.workspace_scenes.regression \
  --env-file .runtime-acceptance/workspace-db-NEW/env.json \
  --output .runtime-acceptance/workspace-regression-NEW

uv run python -m acceptance.workspace_scenes.export_contract --check
```

每次使用新目录；runner 会拒绝覆盖上次结果。bootstrap 在新的 A3 历史基线上应用 0021＋0022，并验证再次运行没有迁移；不更新已有 Method 验收工具关于历史 0021 迁移的断言。应用获得新表 SELECT/INSERT，没有 UPDATE/DELETE；新表 FORCE RLS 和不可变触发器独立存在。

业务起点通过真实 M1A 战略链、LTCO、PCO、Mission、ReviewWindow 动作生成。身份／任职是隔离控制面测试准备；业务记录不使用 SQL 预置。API 使用应用角色，SQL 只观察结果、RLS 和授权；迁移、控制面与需要 owner 权限的历史叙述测试使用独立身份。

## 覆盖范围

- 月度：缺失字段、评论字段定位、本人替代／撤回、评论与关窗 CAS、Co-agent 自身 Context、缺失意见拒绝、候选差异、核对与 CEO 确认分离、整组确认、正式 Mission handoff、显式重开。
- 周度：原始来源、材料版本、本人回答和确认、答案撤回、旧材料冲突、来源刷新待办、事实修正与 PeriodReview 再生成／精确引用、无权读取新材料时不回退旧材料。
- 会议：已阅、补充、带到会议、负责人开始／结束、原始转写校验、伪造原话拒绝、单份发布稿、行动与 CEO 分流、CEO 读取、撤回最新稿不恢复旧稿。
- 权限与恢复：独立身份、冒名操作拒绝、跨域列表及数量、撤权后的读取／回执／重放、并发重复点击、场景 CAS、事件插入后故障回滚、进程重启、实际 HTTP 响应丢失后的原信封恢复。
- 物理核对：事件与 ActionReceipt 一一对应；候选成员均存在对应 payload hash 的不可变版本；场景没有外部 task，也不生成执行或交付对象；RLS 与历史写入权限正确。

`drop_response.py` 只监听一次随机 loopback 端口：真实转发请求到 Runtime，等待上游提交并返回 HTTP 200，再关闭下游连接而不发送响应。客户端实际得到传输错误，数据库独立证明已提交；重启 Runtime 后以原信封获取同一回执。

`workspace_before_receipt` 是生产空操作 instrumentation hook，只有独立验收 wrapper 可注入故障，未新增生产 HTTP 故障入口。注入错误会产生 HTTP 500 并回滚整个事务。

## 产物与状态

- `summary.json`：命名检查、请求计数、代码来源摘要、Runtime 场景接口结果；伙伴、浏览器、真实模型、发布、部署均单独记录。
- `source-manifest.json`：测试源码 SHA256 清单。
- 逐请求转录与进程日志仅存入忽略目录，不提交原始业务／证据／凭据。
- regression 执行 Python、工作台和离线 wheel/sdist 构建。原 `tests/test_migrations.py` 需要单独 CREATEDB 管理身份，不混进应用角色回归；本轮新迁移和重放由 bootstrap 实测。

本 runner 通过不等于旧 Method 完整 98 项环境矩阵重新验收，也不等于 Clark 页面或真实模型验收。伙伴按 [接入契约](../../docs/clark-workspace-integration.md) 接线后，再组织真实浏览器的两个 DRI＋CEO 会话、真实模型收拢、截图和数据库复核。
