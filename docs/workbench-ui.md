# 工作台前端（workbench UI v0.1）

静态 HTML/CSS/ES modules，无 CDN、无框架、无新增依赖。部署入口 `/workbench/`，
数据来自同源本地 QA 代理 `/{actor}/v1/...`。演练配置从 `../case.json`
（即 `/case.json`）读取：`cases`（演练读取入口）、`state_paths`（交付/Outcome/MF
三项锚点）、`commit`（Runtime 基线）。三项锚点 ID 同样来自配置；页面不硬编码业务 ID、
状态、人物、时间与验收结论。

## 页面 ↔ API 映射

| 页面 | 使用的接口 |
| --- | --- |
| 01 对象与关系 | `GET /v1/object-types`（10 种业务类型 + 1 种仅治理登记的 ProtocolSentinel（creation_mode=control_plane_only，非新增可执行业务域）、payload schema、typed refs）、`GET /v1/domains`（消费全部 next_cursor 页，有界 20 页）、`GET /v1/objects?domain_id&object_type&limit&cursor`（按类型分页，加载更多） |
| 02 实例详情 | `GET /v1/objects/{id}`（对象 + delivery/outcome/feedback 投影）、`GET /v1/objects/{id}/revisions`（分页选择器）、`GET /v1/objects/{id}/revisions/{rid}`（精确 revision）、`GET /v1/objects/{id}/relations?revision_id`（出向一跳，分页加载更多）、`GET /v1/objects/{id}/responsibility`（仅 WorkItem）、`GET /v1/evidence-assets/{oid}/revisions/{rid}`（证据字节，textContent 预览或 Blob 下载） |
| 03 动作与回执 | `GET /v1/objects/{id}/action-receipts`（committed 分页列表）、`GET /v1/action-receipts/{rid}`（单条详情，result/object_versions 原样展示） |
| 04 Context Pack | `POST /v1/context-packs`（显式按钮触发，唯一写入口）、`GET /v1/context-packs/{sid}`（快照回读）；候选对象来自 `GET /v1/domains` + `GET /v1/objects`（有界逐页，各 10 页上限） |

三状态带仅在所选实例 == 演练 WI 锚点（`state_paths` 交付）的概览页签显示，并明确标注
「本演练闭环当前状态」：交付=WorkItem `lifecycle_status`；Outcome=`outcome_achievement`
（缺失即「未提供」，不回退 lifecycle）；MF=FeedbackThread `lifecycle_status`。互不推断联动。
其它对象不显示演练三状态，也不发起锚点读取。

## 导航连续性与上下文条

顶栏下的「当前对象 + 版本」上下文条（`lib/focus.js`，仅内存）跨页保持同一对象与
用户选过的精确 revision：主导航携带当前对象，实例 → 回执 → 实例不跳回演练锚点；
首次无对象时页面 fallback 演练 WI；从目录打开对象即替换当前对象。标题/类型只在
授权读取成功后回填；切换身份立即清空上下文条、目录记忆与全部缓存，任何
401/403/404 都不残留先前身份数据。hash 深链接语义不变。

## 页面信息架构（v0.2 布局重构）

- 页 01：左侧类型列表（中文为主、英文小字 mono），右侧主区自上而下为业务域过滤条、
  当前类型摘要 + 分页实例列表、默认收起的「类型定义与字段」details。
- 页 02：「概览 / 版本与内容 / 来源关系」本页页签（切页签不发请求）。WI 默认概览
  （交付轨迹 + 提交/评审/证据主列 + 责任人摘要右列）；非 WI 默认版本与内容；显式
  `?rev` 深链接落版本页。完整 ID/hash/assignment/baseline 与版本指针收进 details，
  展开状态在异步重绘间保持。
- 页 03：左侧动作历史列表、右侧详情（动作/时间/业务评审结果/提交序号在前，
  receiptID/actorID/auth_epoch/result 原文/对象版本收进「审计字段」details）；
  无回执时清晰空态，不无限 loading。
- 页 04：左侧约 380px 输入栏分「新建快照 / 回读快照」两模式（不同时混排），
  右侧常驻结果栏（来源/时间/纳入排除在前，完整 ID/hash 收进「技术溯源」details）。
  手动 UUID 默认折叠。

布局示意与层级变化详见 [布局重构设计记录](workbench-layout-design.md)。

## 部署静态资源路径

```
workbench/
  index.html            外壳（顶栏身份选择器、当前对象上下文条、左侧导航、案例栏）
  styles.css            收敛后的色板与组件 + 状态/表单/快照/details/页签组件
  app.js                config 加载、身份切换、focus 上下文条、hash 路由、复制委托
  package.json          仅声明 type=module（供 Node 测试导入）
  lib/                  api / loaders / snapshot / timeline / url / focus / format /
                        html / payloadview / contextbody / guard（纯模块，可测）
  pages/                catalog / instance / receipts / context
```

代理需把 `/workbench/` 映射到该目录（本轮由 Codex 的 deploy/workbench-local 承载）。

## 本地合成角色 proxy 边界

- 身份选择器固定四项（ceo / mission_dri / verifier / outsider），只切换本地
  合成代理路径前缀 `/{actor}`，**不是正式登录或授权判定**；界面明确标注。
- 浏览器不接触任何 token/凭据，不写 localStorage；所有请求 `cache: 'no-store'`。
- 切身份立即清掉全部已读对象/回执/快照缓存与界面旧数据，中止在途请求
  （AbortController + epoch），以新身份重新查询；hash 仅保留页面对象位置。

## Context 审计写入例外

- `POST /v1/context-packs` 是唯一写操作，仅由「生成审计快照」按钮显式触发；
  页面载入、角色切换、时间编辑绝不自动 POST；按钮带 disabled/busy 防双击。
- 按钮旁说明：POST 持久化 append-only 审计快照，不改变业务对象状态。
- 时间输入 step=0.001（毫秒）；快照卡显示服务端原始 ISO 时间。
- 条件比较保留微秒精度（`Z` 与 `+00:00` 等价），并比较对象 ID 集合；输入与快照
  条件不一致或时间尚未填写完整时，快照卡显著标注，且不重建 DOM（保留输入焦点）。
  输入控件仅支持毫秒，因此服务端快照含非零微秒时，不会误报为条件相同。

## 明确不提供

- 无任何业务 Action 按钮（承接/提交/评审等正式操作由 Clark 承载）。
- 无正式登录、无 impersonation 接口、无权限结论展示（类型目录标注为静态说明，
  assignment active 不代表执行权）。
- 无入向关系全图、无拒绝请求示例（授权拒绝不产生持久化回执，页面不虚构）。
- 无 Clark 接入；无跨请求一致性快照承诺。

## 本地验证入口

打开 <http://127.0.0.1:8032/workbench/#/instance>。
原始 API 验证入口保留在 <http://127.0.0.1:8032/>。
安装与代理配置见 [部署说明](../deploy/workbench-local/README.md)。

## 文件与验证结果（v0.2 布局重构后）

- `workbench/`：19 个公开文件，含 16 个 JS 模块（lib 11 个、pages 4 个、app 1 个）。
- `tests/workbench-ui/`：12 个 `*.test.mjs`，共 60 项测试；另有 GET 联调脚本。
- 本轮（v0.2 信息架构与布局重构）由 Kimi 实施，自测：JS 语法检查全部通过、
  60/60 测试通过、34/34 本地 GET 联调通过（未做任何 POST）。
- v0.1 历史验证：Codex 复验 51/51 测试、34/34 GET 联调与浏览器独立验收，见
  [独立验收记录](acceptance/workbench-ui-v0.1-20260909.md)；数据库前后对比仅
  `gov_context_snapshots` 增加 5 条显式测试快照，其余 16 张受检表一致。
- v0.2 独立复验：60/60 自动回归、77/77 严格 HTTP 检查；浏览器四页渲染、
  390/850/1200px 布局、导航/历史版本、分页、证据、Context 模式与身份切换通过，见
  [v0.2 独立验收](acceptance/workbench-ui-v0.2-20260910.md)。本轮未修改业务接口。

## 验证范围与限制

这是本机容器、合成案例的工作台接入验收，不代表真实企业数据或生产验收。
未验证正式登录、运行中撤回授权后的自动刷新、完整读屏器行为和大规模数据的上限截断路径。
业务域目录上限为 20 页（每页 100），Context 候选域和对象分别有 10 页上限；
这些上限是防止无限加载的保护，不是已经验证过的规模容量。
本轮未修改 Clark、Runtime 业务接口或生产部署。
