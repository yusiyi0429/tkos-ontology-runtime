# Method 0.4 实施与独立验收状态

日期：2026-09-17。分支：`codex/method-04-realignment`。
基线为 `acea4d4` 加复制的 57 条既有工作台改动；原工作区这 57 条路径的内容哈希核对未变。仅修改 Runtime，未修改 Clark。

开发由 pi 完成，模型为 `deepseek/deepseek-flash`，thinking level 为 `max`；Codex 负责代码评审和独立验收。开发模型不计作业务真实模型验收。

## 已交付

| 批次 | 当前结果 |
| --- | --- |
| B0 契约 | Method 0.4 / Workspace 0.2 已实现；新 scope 显式启用，旧版本保持原规则，不自动升级历史对象 |
| B1 地图 | 44 项业务定义、来源与分类；业务成熟度、编译支持、scope 启用和授权数据分别展示；0.4 地图与实际记录导航可用 |
| B2 独立来源 | 私有来源、精确分享、版本更正、撤回、Context、Agent 运行和引用草稿；当前授权重查，不产生正式业务决定 |
| B3 M1A | CEO Agent 立项、必要人类共同确认 Agreement、Co-agent 复核、CEO 原子确认 Strategy/Architecture 更新 |
| B4 M1B 与状态 | Scope 目标、完整候选集合、本人责任承诺、CEO 整组确认；State、PeriodReview 与 Problem 移交规则 |
| B5 工作台 | 本人会话、结构化表单、确切引用选择、预览、回执与恢复；伙伴 OpenAPI、事件映射、独立验收入口 |

## 独立验收结果

- HTTP：workspace 64 项、旧 0.3 回归 35 项、Method 0.4 正向 29 项与负向 39 项、真实会话 facade 47 项通过。
- Python：919 项通过，16 项跳过；另在空数据库执行全部迁移与重复重放，1 项通过。
- 前端：21 个文件、165 项通过；类型检查、生产构建、资产验证与 Python 包构建通过。资产清单为 72 个输入、8 个输出。
- 浏览器：三位必要人类确认 Agreement；两位 DRI 发布、替代、撤回评论；受控 Co-agent HTTP 收拢；四位 DRI/Owner 本人承诺；CEO 整组确认；Owner 查看正式 Mission。
- 同一链路数据库核对：2 个正式 Mission、4 条承诺、5 条评论历史、1 条撤回；执行授权与交付验收均为 0。
- 来源双会话：精确分享、Context 保存、取消分享后当前 Context 隐藏正文；并发版本冲突后的刷新重提通过。

16 项跳过分别为：1 项缺少 `agent_service` 测试身份，1 项旧的“0.4 尚未编译”检查因已启用而不适用，14 项 legacy 集成因专用隔离 seed 配置未启用而跳过。跳过不计通过。

新增迁移为 `0026_workspace_sources_v02.sql`、`0027_method_v04.sql`、`0028_method_v04_contract_repin.sql`、`0028_workspace_v02_grant_repair.sql`。两个 0028 文件名不同，迁移器按完整文件名记录；已应用文件保持不可变。

## 交付边界

源码已完成本地评审冻结。真实业务模型、Clark 接线及 Clark 浏览器验收未完成；受控 Agent 输出仅证明 Runtime 治理链路。未提交、推送、发布或远程部署。

详见 [交付报告](method-04-delivery-report.md)、[验收矩阵](method-04-acceptance-matrix.md)、[截图](acceptance/assets/method04/README.md)。
