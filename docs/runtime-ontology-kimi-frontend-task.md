# Kimi Code 前端实现任务（与另一 Kimi 会话分工）

用户已确认 docs/runtime-ontology-views-design.md 并要求 Kimi Code 开发。请在当前工作区实际完成图形组件与页面接线，不重新访谈、不派探索代理、不停在计划。

## 文件分工（避免冲突）

- 你只修改 workbench/dashboard/ 内除 src/lib/ontology.ts 以外的前端文件。可以新增前端测试及新组件，更新构建资源 src/memory_service_app/dashboard_dist/（最终统一构建由 Codex 做）。
- 另一 Kimi 会话负责 Python 后端、tests/dashboard 和 src/lib/ontology.ts 的业务说明及规则修正。不要编辑这些文件，发现接口/语义问题写入 .runtime-acceptance/ontology-kimi/frontend-needs.md 即可。
- 不修改任何其他仓库、已有容器、凭据、身份与业务数据；不提交推送或部署。
- 保留监督方 docs 与 acceptance/ontology_views；浏览器真实验收由 Codex 负责，你先用组件测试验证。

## 先读的有限上下文

docs/runtime-ontology-views-design.md、docs/runtime-dashboard.md、workbench/dashboard/src/App.tsx、lib/types.ts/api.ts/live.ts/urlState.ts/labels.ts/ontology.ts、components/DetailPane.tsx。充分后直接实现，不要再次广泛探索。

现有 React19 + Vite + shadcn/ui，继续复用组件，保持白底深色导航的现有主题，不重新初始化 UI。官方 https://ui.shadcn.com/docs/components 。可用轻量 SVG 实现层级布局及业务标签连线，无需引入图数据库或运行时外网服务。

## 已就绪的接口／数据

- GET /dashboard/api/v1/ontology/catalog 返回 versions:[{contract_version,object_types}] 和 types:{[type]:{group,listable}}，该目录是完整注册类型，不能由当前数据反推。
- GET /dashboard/api/v1/catalog/objects?object_type=Mission&limit=25&cursor=... 返回任一注册类型的授权分页目录；items 含 object_id、object_type、title、object_version（所选内容版本）、basis_revision_id、contract_version、effective_revision_id、latest_revision_id、formal_state，loaded_count 仅当前页。
- 现有 fetchDetail 读取精确版本、protocol、relations（own_basis_refs/upstream_refs/downstream）、正式状态、候选、版本、责任人与证据；fetchDownstream 支持分页。后端伙伴正在扩展所有 Method 类型的邻接及正式状态，不只原六组。
- lib/ontology.ts 已有 RulesVersion、typeInfo、areasFor、mapEdges、RULES_VERSIONS、rulesOfContractVersion；该模块只供概念规则，不可代替真实业务数据，另一会话会修正文案，但尽量保持接口不变。
- lib/types.ts/api.ts/urlState.ts 已加入新目录类型、fetchOntologyCatalog/fetchCatalogObjects（以实际函数名为准）以及 view=map（默认）、rules=0.3、otype。你可以继续扩展这些文件。后端伙伴不编辑它们。

## 必须完成的前端行为

1. 默认本体地图，并列业务关系图，保留原列表/详情可用入口。地图五区域+共同支撑，按区展开类型；使用真实可见且有业务标签的连线，不仅堆卡片。注册类型全部可见，无实例也不隐藏。
2. 点击类型在右侧显示折叠说明卡：定义、主要信息、关系、生命周期、操作主体、正式效力，以及查看实际数据。技术字段放现有技术溯源页；业务说明以中文为主。
3. 类型→真实记录分页列表→选中具体记录进入关系图。任意已注册类型都可查询，不仅六组。空结果不声称全局无数据；请求失败/接口不可用不可冒充空结果。
4. 业务关系图直接进入时从所选战略展示相关 LTCO/PCO/Mission，支持逐步展开状态、事实、复盘、问题及研究关系；多战略明确先选择。选中具体对象时可聚焦它，点节点查看现有详情。标注部分加载，分页可继续，不静默截断。
5. 节点身份 object_id+revision_id，精确记录连线；禁止合并同一对象不同版本。默认正式内容优先，允许候选/历史查看，正式 Mission 的必需旧版依据永远保留。分析材料可用不等于人类审批，不能因为没有审批动作就丢掉必要依据。
6. 查看实际对象的规则时按 detail.protocol.contract_version（核对真实结构）切换对应0.1/0.2/0.3，未知版本必须提示，不猜最新。实现视图联动与返回保留战略/对象/版本/筛选/阅读位置。
7. 搜索定位、缩放、适应视图、返回上一级；窄屏列表/详情可读，键盘能够选择节点及展开。控件使用现有 shadcn/ui。
8. 保持当前身份认证、no-store、前台5秒与焦点刷新、切换取消及epoch丢弃迟到响应。身份/授权版本改变或401/403/404时清空全部受保护图和数据，禁止回填旧请求。受保护业务内容不写 localStorage。
9. 所有动作说明均只读，无业务提交按钮。

## 验证与反馈

- 补有意义组件/状态测试，保留既有56项的权限/版本验证；旧默认列表测试可显式指定 view=list，不删除或弱化断言。
- npm run typecheck、npm test、npm run build。不要同时 npm ci 破坏另一进程；Codex 最终统一 scripts/build_dashboard.sh 与uvbuild。
- 每完成一个组件在 .runtime-acceptance/ontology-kimi/frontend-progress.md 写简短无敏感进展；完成写 docs/runtime-ontology-kimi-frontend-selftest.md（唯一允许新增的docs）。
- 如果后端契约或 ontology.ts 有问题，写 frontend-needs.md 并继续独立可做部分；不要悄悄用fixture或假数据代替。
