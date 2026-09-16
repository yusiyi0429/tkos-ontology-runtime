# 本体地图与业务关系图：独立验收记录

状态：Runtime 本地接口、浏览器与打包验收通过；尚未部署新版容器。提交与合并状态以 Git 历史为准。
验收日期：2026-09-16。

## 交付与分工

- 开发：真实 Kimi Code 0.43.0 会话，模型 k3-256k；Codex 提供任务、审查反馈、修复集成问题并独立验收。
- 开发基线：`2f7be3d`；工作分支：`codex/runtime-ontology-views`。
- 只修改 Runtime；不修改 Clark，不增加业务表、迁移或写动作。
- 默认本体地图，五个业务区域与共同支撑；类型说明、授权记录目录、精确版本关系图与旧列表可以互相切换。
- 0.1/0.2/0.3 冻结类型目录分别为 22/23/26 项。Battlefield、Capability 和 Outcome 保持嵌入定义项，不冒充独立对象。
- 当前对象使用其实际绑定规则；未知规则不借用最新规则。候选不覆盖正式内容，正式对象必要的历史依据保留。

Kimi 的原始自测分别见[后端自测](runtime-ontology-kimi-backend-selftest.md)、[前端自测](runtime-ontology-kimi-frontend-selftest.md)。两份报告是当时自测快照；下表为 Codex 完成审查修复后的最终结果，不能反向归为 Kimi 自测。

## 最终代码与接口验证

| 检查 | 结果 | 实际覆盖 |
| --- | --- | --- |
| 前端回归 | 14 文件、94 项通过 | 地图、规则语义、目录、图扩展、版本、刷新、取消、移动端弹层与原列表 |
| Python dashboard 回归 | 101 项通过 | 授权、分页、目录、精确版本、正式效力、读取 facade 与资产清单 |
| 新增本体 HTTP/SQL 独立验收 | 46 项通过 | 三版本注册集合、26 类型分页、内容版本、真实引用、越权、游标错用、禁止写入及数据库不变 |
| Method 0.3 隔离场景回归 | 58 项通过 | 合法配对战略/责任结构、历史目标、状态、事实、复盘、问题移交及只读核对 |
| Method 0.1 数据副本回归 | 53 项通过 | 旧对象读取、原规则、分页、关系来源及只读表计数 |
| 类型检查与生产前端构建 | 通过 | npm ci、TypeScript、Vitest、Vite、资源 manifest |
| Python 构建与打包资源核对 | 通过 | wheel 与 sdist 分别逐文件校验 |

构建清单包含 58 个输入、8 个输出。当前 JS 为 `index-BcOtyNvx.js`，CSS 为 `index-BJxPyIlM.css`；manifest SHA256 为 `1db1ee0c6b899c9a6263bfe25e4654378e087d5a2a8c571678300786b4b8fe8e`。Vite 给出约 515 KB JS 分包建议，不影响构建通过；未另行增加分包功能。

机器可读的脱敏结果：[本体接口](acceptance/ontology-views/catalog-http-sql.json)、[0.3 回归](acceptance/ontology-views/method-0.3.json)、[0.1 回归](acceptance/ontology-views/method-0.1.json)、[浏览只读核对](acceptance/ontology-views/browser-read-only.json)。接口报告中的 `browser_accepted=false` 表示该脚本不执行浏览器，本节另行记录真实浏览器结果。

## 真实浏览器验收

使用同一 Ego Browser 验收空间，访问本轮源码服务；没有用组件测试代替浏览器结果。

| 场景 | 结果 |
| --- | --- |
| 默认地图、六个区域、展开与类型业务说明 | 通过；鼠标与键盘 Enter 均可选择 |
| 搜索、缩放、适应视图、跨视图位置保留 | 通过 |
| 0.1/0.3 规则切换与 CEO/Agent 权限说明 | 通过；旧对象的“查看该类型业务规则”自动选择 0.1 |
| 类型 → 实际记录 → 精确版本图与详情 | 通过；Mission 正式/草稿与内容版本分别显示 |
| 当前战略 → LTCO/PCO/Mission | 通过；按真实引用逐步展开，默认加载 11 个节点 |
| 候选/历史切换 | 通过；本轮固定数据由 11 增至 12 个节点，该数量仅为当前加载范围 |
| 历史 Mission 与旧 PCO/Strategy 依据 | 通过；明确标注沿用历史依据，未自动挂接当前战略 |
| 390px 窄屏地图、记录详情、关系列表 | 通过；无水平溢出，切换后只有当前详情弹层 |
| 当前身份失效 | 通过；更换源码预览身份后清空旧图和旧身份，恢复有效身份后重载成功 |
| 类型没有可见实例 | 通过；概念仍可查看，空态只说明当前身份及筛选范围 |
| 空目录断网与恢复 | 通过；旧内容明确标记未刷新，恢复网络后刷新正常 |
| 浏览器前后数据库快照 | 相同；在同一隔离 scope 核对，未产生业务写入 |

截图使用合法动作建立的合成验收数据，界面与英文合成内容分别保留真实显示。

- [本体地图总览](screenshots/ontology-views/map-overview.png)
- [Mission 业务说明](screenshots/ontology-views/mission-type.png)
- [战略关系图](screenshots/ontology-views/strategy-graph.png)
- [历史 Mission](screenshots/ontology-views/mission-history.png)
- [移动端地图](screenshots/ontology-views/mobile-map.png)／[移动端关系列表](screenshots/ontology-views/mobile-relations.png)
- [旧对象绑定规则](screenshots/ontology-views/legacy-object-rules.png)
- [空目录断网标记](screenshots/ontology-views/empty-offline.png)

## 访问与复跑

本轮源码预览：[http://127.0.0.1:58806/dashboard/](http://127.0.0.1:58806/dashboard/)。这是本机临时源码进程及独立合成数据，重启机器后需重新启动；不是新版容器或正式业务数据环境。

原本的五个容器保留，`58802/dashboard/` 仍是 `eccc346` 的旧看板。验收用的第二个源码 API（58807，0.1 数据副本）在验收后停止，保留 58806 供查看。没有新增常驻容器。

复跑说明见 [本体目录 HTTP/SQL 观察器](../acceptance/ontology_views/README.md)、[0.3 场景](../acceptance/dashboard_0_3/README.md)、[0.1 场景](../acceptance/dashboard_0_1/README.md)。初始业务对象通过合法动作建立，SQL 只核对结果；凭据、原始请求、模型日志与数据库内容保存在忽略的私有验收目录，不进入交付报告。

```sh
./scripts/build_dashboard.sh
DATABASE_URL=postgresql://unused:unused@127.0.0.1:1/unused uv run pytest tests/dashboard -q
uv build
uv run python scripts/verify_dashboard_assets.py --archive dist/tkos_memory_service-0.2.1-py3-none-any.whl
uv run python scripts/verify_dashboard_assets.py --archive dist/tkos_memory_service-0.2.1.tar.gz
```

## 尚未覆盖的边界

这次通过的是 Runtime 只读界面的本地验收。没有进行 Clark 接线或 Clark 浏览器闭环、正式企业 SSO、生产数据迁移、远程部署、全量业务用户理解度研究或真实业务模型收拢验收。Kimi 开发期间的真实模型调用不等于业务 Co-agent 收拢验收。

图按当前授权、精确引用与分页逐步展开，不宣称已展示全企业对象；候选/历史开关控制额外邻接，必要历史依据始终保留。包版本仍沿用 0.2.1，本轮没有发布新 Release。
