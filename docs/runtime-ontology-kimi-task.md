# Kimi Code 开发任务：Runtime 本体地图与业务关系图

用户已确认 docs/runtime-ontology-views-design.md 的全部设计并明确要求由 Kimi Code 开发。请实际完成实现及自测，不停在计划。Codex 负责独立验收。

## 工作区与边界

- 仅修改本工作区 /Users/yusiyi/ysy/worktrees/tkos-runtime-dashboard，分支 codex/runtime-ontology-views，基线 2f7be3d。
- 已有 CONTEXT.md 和设计文档是监督方写入的已确认内容，保留并遵循。
- 不改 Clark、不改其他 Runtime 工作区、不改正式业务动作/权限/迁移，不连接业务写入。必要的只读投影扩展允许在本工作区实现。
- 不提交、不推送、不合并、不部署或重启任何现有容器。不要读取凭据、私有验收日志或全量环境变量。保持现有 5 个容器不受影响。
- 不配置或调用额外模型。你作为开发 Agent 的现有模型调用已授权。
- 不将测试 fixture 当作页面数据；无数据、无权限、读取失败与未接入必须如实区分。所有应用查询继续当前授权、no-store、精确版本和取消迟到请求。

## 先读

README.md、CONTEXT.md、docs/runtime-ontology-views-design.md、docs/runtime-dashboard.md、docs/runtime-dashboard-field-mapping.md、docs/runtime-anchors-v03-integration.md、docs/runtime-lifecycle-v02-integration.md、三个 runtime-method-registry JSON、src/memory_service_runtime/governed 的 Method 模型/只读投影、现有前端 App/DetailPane/types/api/live/urlState。

技能：/Users/yusiyi/.codex/skills/frontend-design/SKILL.md 和 /Users/yusiyi/.codex/plugins/cache/openai-curated-remote/vercel/0.21.4/skills/shadcn/SKILL.md。保持现有视觉风格；避免不相关重构与重新初始化 shadcn。官方组件来源 https://ui.shadcn.com/docs/components；需要增加组件时使用 CLI 且锁定依赖，已有组件优先。

## 必须实现

1. 默认“本体地图”，并列“业务关系图”，保留可用列表入口。五业务区域及共同支撑，按区域展开，具备真实带业务标签的连线，而不是仅把现有列表排成卡片。
2. 三个业务规则版本准确区分：0.1/0.2/0.3 各自的类型、关系、生命周期与确认主体。0.3 CEO Agent 立项、CEO 研究指派，旧规则按已有契约验证。目录不能仅覆盖当前六组导航：完整显示注册类型；场景记录/授权底座单独标识，不虚构注册对象。嵌入的 Battlefield/Capability/Outcome 不伪装独立对象。类型说明应具体而非通用占位。
3. 点击类型右侧折叠业务说明卡；可见记录列表与具体关系图联动。若现有 dashboard reader 不支持某个已注册类型，扩展只读投影并测试，不把未接入默认为没数据。复用已有授权 reader，不拓宽权限/暴露计数。
4. 战略入口的真实实例图；多战略先选择。精确引用边、可展开邻接，正式优先，支持候选/历史查看且保留必需旧依据。节点身份含 revision，不能按 object_id 合并不同版本。分析产物的 effective 不等于审批。
5. 选中对象能查看其真实规则版本，找不到版本时明确提示不能猜测。实现搜索、缩放、适应视图、返回；切换图/列表/详情保留阅读上下文。受保护数据不写浏览器持久存储，撤权或身份变化必须清空图及在途结果。
6. 不编造可执行动作，不增加业务写按钮；权限/规则说明与当前可执行权区分。
7. 可读的中文业务标签、桌面布局、窄屏列表/详情、键盘可访问；继续 shadcn/ui。图形可选轻量 SVG 或合理库，取决于实现复杂度，说明理由；不需引入图数据库、LLM 推断或新常驻组件。

## 自测及交付

- 增加有意义的前后端回归：跨版本目录与权力边界、图版本身份、历史依据、分页/部分加载、取消迟到/撤权、两个视图跳转、空/失败状态、键盘/窄屏可读。
- 保留并运行既有前端测试、typecheck、构建；必要时调整旧“默认列表”断言以适应已确认的新默认，不能删减其数据/权限验证。
- 后端如变更运行受影响测试。tests/conftest.py 需要 DATABASE_URL，即便无 DB 单测可用不连接的占位 URI，禁止误连默认业务库；真实 DB 验收由 Codex 另行安排。
- 执行 scripts/build_dashboard.sh 更新资源与清单，uv build 验证打包。若遇到工具故障继续定位并如实报告。
- 更新 README 入口、接口/设计材料以及 docs/runtime-ontology-kimi-selftest.md，逐项记录实际执行、结果、未验证项。不得把自测当成独立验收或部署。
- 每个主要阶段在私有目录 .runtime-acceptance/ontology-kimi/progress.md 写简短无敏感进展，供监督方读取；最终答复列出改动、验证、限制。
