# Clark 本体叙述与 Runtime 记忆收敛

Runtime 提供 Clark 已消费的 `POST /v1/context-graph/narrative`。它把保留的历史语义记忆与新的治理事实组织成可读上下文，不执行业务动作，不创建回执、快照或第二份业务账本。默认关闭，显式设置 `TKOS_NARRATIVE_ENABLED=1` 后启用。

## 三项判断保持独立

叙述同时保留 WorkItem/Deliverable 的交付状态、CompanyOutcome 的达成评估、FeedbackThread 的 MF 关闭状态。`delivery_accepted / not_achieved / closed` 是合法组合；不能因为交付通过或 MF 关闭而改写 Outcome 结论。每一项都来自自己的版本、生命周期事件或验收/评估记录。

治理事实由确定性读取和渲染产生。可选模型只压缩历史语义背景，根愿景原文与治理事实均保持原文追加，模型不参与权限、事实筛选、状态或验收判断。历史背景和治理事实分别标识，不隐式合并成同一个对象或结论。

## 请求与响应

请求使用 Runtime 已登记、未撤销且具有当前任职的个人/服务 Bearer 凭据。Clark 共用登录口令、旧 Basic 凭据、模型 API key 均不能代替。

```json
{
  "query": "当前交付通过了吗，经营目标和反馈分别是什么状态？",
  "include_raw": true,
  "domain_id": "<authorized-domain-uuid>",
  "object_ids": ["<work-item-uuid>", "<outcome-uuid>", "<feedback-uuid>"],
  "valid_at": "2026-09-07T09:00:00Z",
  "known_at": "2026-09-07T09:00:00Z"
}
```

只有 `query` 必填（非空，最多 2000 字符）。`include_raw` 默认 false；`tenant`、`org` 只能核对凭据所属组织，不能选择另一个组织。域由请求、服务端固定域或该身份唯一可读域确定；服务端已固定域时不可由请求覆盖。多个可读域未指定时返回 422。

`object_ids` 可选且最多 50 个唯一 UUID。省略时在一个可读域中扫描最近 200 个候选，按查询词与标题/摘要的确定性匹配排序，最多返回 50 个有效事实。它不是开放域语义搜索；`truncated` 为 true 时不得把未返回对象理解为不存在。精确对象查询也会重新检查当前权限及所有递归来源的权限。

省略时间时使用请求开始的 UTC 时间；显式时间必须有时区且不能在未来。已确认的历史版本、状态和评估均按双时间读取，当前草案不会替代旧有效版本。生命周期事件没有独立的回填生效时间，因此其记录时间必须同时不晚于 `valid_at` 和 `known_at`。

响应兼容 Clark 原字段：

| 字段 | 含义 |
| --- | --- |
| `narrative` | 供 Clark 使用的正文：历史背景与独立治理事实 |
| `narrative_raw` | 仅在 `include_raw=true` 时返回的未压缩正文 |
| `hit_paths`、`lateral_nodes`、`root_statement` | 历史语义检索路径数、横向节点数与原始根愿景；纯治理模式为 0/0/空串 |
| `tenant`、`org` | 已鉴权的组织范围 |
| `model` | 实际压缩模型；未调用模型时为 `deterministic-v1` |
| `narrative_raw_chars`、`narrative_chars` | 两份正文的字符数 |
| `governed_facts` | 版本为 `governed-facts.v1` 的精确版本事实、排除原因、截断信息 |
| `provenance` | 当前读身份、授权 epoch、治理双时间、内容哈希及历史图谱来源 |

`governed_facts.selected[].payload` 是白名单脱敏投影，`payload_hash` 对应数据库的**原始不可变 revision**，不能对投影 payload 重算该 hash。`source_refs` 保留精确历史来源、版本及 hash；证据仅带内容 SHA256/长度，不带对象存储桶、key、版本位置或下载凭据。历史来源不意味着仍是当前基线。

`provenance.governed_sha256` 对整个治理投影取规范化 JSON SHA256，`narrative_sha256` 对最终正文取 UTF-8 SHA256。正文与来源按请求返回，不持久化文本缓存。慢模型返回后，接口重新检查凭据、授权 epoch、域、全部来源及图谱当前代；撤权或切代时拒绝返回旧结果。

## 历史记忆的权限与时间边界

历史语义表只有 tenant/org 范围，没有新的业务域 ACL。启用 `TKOS_NARRATIVE_LEGACY_ENABLED=1` 时必须同时满足：

1. 凭据所属 tenant/company 与进程 `MEMORY_TENANT`、`MEMORY_ORG` 相同。
2. 当前域策略 `action_roles.read_legacy_context` 明确允许当前任职角色，不能仅凭普通 `read` 权限读取整家公司的旧图谱。
3. 检索得到真实的 current generation、路径与可解析来源。没有当前代、源损坏或必须的历史上下文为空时显式失败，不用占位文本假装读取成功。

旧图谱仍按 current generation 检索；`provenance.legacy` 单独记录 `temporal_mode=current_generation`、`retrieved_at`、generation ID、Pack hash 及来源引用。它与治理事实不是同一个数据库历史快照。因此启用历史图谱时拒绝显式 `valid_at/known_at` 查询；需要历史治理审计时使用纯治理模式。

## 配置

| 服务端配置 | 默认与要求 |
| --- | --- |
| `TKOS_NARRATIVE_ENABLED` | `0`；设置 `1` 启用 HTTP 叙述及匿名最小健康探针 |
| `TKOS_NARRATIVE_DOMAIN_ID` | 可选固定业务域；Clark 原请求不带 domain 时建议明确设置 |
| `TKOS_NARRATIVE_LEGACY_ENABLED` | `0`；设置 `1` 才读取历史语义图谱 |
| `TKOS_NARRATIVE_COMPRESSION` | `none` 或 `chat`；默认确定性渲染，不调用模型 |
| `TKOS_NARRATIVE_TIMEOUT_SECONDS` | 外部调用超时默认 20 秒，范围 1–60 秒 |
| `MEMORY_EMBEDDING_API_KEY/BASE_URL/MODEL/DIM` | 历史检索的 embedding 配置；沿用 Ark multimodal 协议，默认维度 2048 |
| `TKOS_NARRATIVE_MODEL_API_KEY/BASE_URL/MODEL` | `chat` 模式的服务端模型配置；兼容 chat completions 协议 |

模型与 embedding 凭据支持同名 `_FILE` 私有文件（不能与内联值同时配置）。上游地址固定在服务端，只允许 HTTPS 或本机 HTTP，不接收客户端 URL，不跟随携带凭据的重定向。无需新增生产依赖。

Clark 仍读取 `TKOS_MEMORY_API_BASE_URL`、`TKOS_MEMORY_API_KEY`；将其指向新服务并设置合适的只读服务凭据即可保持原 NarrativeClient 协议。已有个人 `/delivery` 身份映射继续独立，不把服务凭据提升为人类验收权。

启用叙述后，无 Authorization 的 `GET /healthz` 返回最小匿名 `{ok, service, narrative}`，不泄漏业务计数、组织或凭据；带 Authorization 的旧兼容探针保持既有 Basic 行为。健康探针只证明进程能访问数据库，不证明图谱、模型或业务闭环通过验收。

## 错误与替换边界

缺失/无效凭据 401；越权 403 或不可见对象 404；请求不合法 422；所需有效上下文为空 404 `NARRATIVE_EMPTY`；接口关闭、源/模型故障 503；处理期间权限或图谱发生变化 409。错误不回显 SQL、上游响应正文或凭据。成功响应禁止缓存。

保留旧 P0–P3 接口不意味着旧数据已迁移。新 `gov_*` 与历史 `semantic_*`/`wm_*` 通过上述只读上下文组合共存，旧判断不会自动变成新的承诺、交付验收或 Outcome 达成记录。切换前须完成 [迁移预检与演练](../deploy/convergence/README.md) 以及真实数据、当前 Clark 客户端的对照检查。

本协议不代表已完成生产切换、全企业 SSO 或所有旧 Workspace 工具的替代。
