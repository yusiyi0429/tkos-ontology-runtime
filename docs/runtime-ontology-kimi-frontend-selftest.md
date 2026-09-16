# Runtime 本体视图前端自测报告（Kimi 前端范围）

日期：2026-09-16。范围：仅 `workbench/dashboard/` 前端。未改动 Python 后端、`src/lib/ontology.ts`、`tests/dashboard` 或 Codex 验收脚本。最终整合与浏览器验收归 Codex，本报告只记录我实际完成与实测的内容。

## 已完成的实现

- **本体地图（默认 view=map）**：`OntologyMap.tsx` + `PanZoomCanvas.tsx`（后者及地图视觉布局后由 Codex 接管修复）；区域折叠总览、点击区域展开类型、类型搜索定位、选中类型右侧 `TypeInfoCard`（定义/主要业务信息/对象关系/生命周期/操作主体/正式效力分段，未整理类型显式标注“暂无业务说明”，不猜文本）；`查看实际数据` 打开 `CatalogRecords`。
- **CatalogRecords.tsx**：类型真实记录分页（catalog/objects），useLiveResource 首页 + 手动加载更多（epoch/abort/isAccessDenial）、pending 更新确认条（catalog-pending）、stale 提示（catalog-stale）、空结果文案明确“不表示全局没有数据”、错误单独表达并可重试；行文案为“当前内容第 N 版 / 对象创建于 …”，徽标用 `formalBusinessText`。
- **BusinessGraph.tsx（view=graph）**：节点身份 = object_id@revision_id，同一对象不同版本永不合并；单调度次 tokens + generation + AbortController（重置/卸载时 abort 且代次递增，F10）；根解析失败有 graph-root-error + 重试；节点展开失败有 graph-retry 且 autoTried 防无限循环；正式 LTCO/PCO 主干自动展开（fanout≤12）；候选类下游（draft/candidate/under_review/proposed/recommendation/returned）默认收拢（graph-hidden-* 可展开），分析材料与未知效力不收拢，依据/来源（虚线）永不收拢；applyDetail 全量替换基础邻接并清空分页续读（graph-extras-cleared-* 明示）；5 秒/焦点定时刷新仅视图可见时运行，失败出 graph-stale 且保留上次成功内容；搜索定位、返回上一级（focusStack）、连线明细可访问列表（edge-list）。
- **App.tsx 接线**：三视图（map 默认/graph/list）；catalog 仅 map 视图加载；**F5** 类型选择按规则版本校验，目录中未注册则清除 otype 不冒充；**F3** visitedViews 懒挂载 + hidden 保持 map/graph 阅读位置（普通视图切换不重置），身份/授权变化（auth_epoch）正确重载全部受保护投影；openCatalogRecord 正式优先（effective→basis→latest）跳 view=graph 并钉版本；showTypeRules 按对象真实 contract_version 切版本，未识别版本出 rules-notice 不猜最新；pending/stale 横幅覆盖 catalog；accessLost 时各视图替换为 AuthLostPanel。
- **TopBar**：视图切换（map/graph/list）+ map 下业务规则版本选择；**DetailPane**：`show-type-rules`“查看该类型业务规则”入口。
- **测试夹具**：`fixtures.ts` 追加 `ontologyCatalog()`（v0.3 全 26 类 / v0.2 / v0.1 递减）、`catalogItem()`、`catalogPage()`；`App.test.tsx` 旧列表用例显式钉 `view=list`（任务书允许，不弱化断言）。

## 审查问题处理状态（frontend-review.md）

- F3（视图切换保留阅读位置）、F5（otype 跨版本清除）、F6（CatalogRecords 语义）、F7（文案/徽标）、F8（图节点身份/收拢/分页）、F10（刷新安全：extras 清除、元数据更新、entryFocusRef 重建）——已在代码中修复。
- F9（地图视觉布局/可读性）及 PanZoomCanvas/OntologyMap 专属修复——按分工归 Codex，我未再编辑这两个组件。
- F1/F2/F4 等已于前轮处理（详见 `.runtime-acceptance/ontology-kimi/frontend-progress.md`）。

## 实测结果（我亲自运行）

- `npx vitest run`：**11 个测试文件全部通过，81/81 通过**（含修复后的 urlState 默认序列化断言 `?view=map&group=strategy&basis=all&rules=0.3`，属更新陈旧期望，非弱化）。
- `npm run typecheck`：此前已通过（组件落盘后）；本次交接前未重跑，以 Codex 最终整合后的全量验证为准。

## 未完成 / 未验证项

- **计划中的 `OntologyViews.test.tsx`（App 级新视图测试）未写**：地图默认加载、区域折叠展开、图主干自动展开/候选收拢/分页续读/权限清空/规则入口等新路径目前**没有新增自动化测试覆盖**；既有 81 个测试覆盖的是旧列表/详情/授权失效路径与 Codex 侧的组件测试。
- **BusinessGraph 的 F10 刷新清理路径（5 秒轮询触发 extras 清除）无测试验证**；rootError 重试、候选收拢等仅有代码实现。
- 未跑 `npm run build` / build_dashboard.sh（按约定由 Codex 统一构建）。
- 真实浏览器端到端验收（含 F9 视觉确认）归 Codex，其结果不计入本报告。
