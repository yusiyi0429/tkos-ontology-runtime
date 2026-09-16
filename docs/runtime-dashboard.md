# Runtime 经营看板（tkos.dashboard/0.1）

面向已理解 Strategy／Architecture／LTCO／PCO／Mission 的业务读者：看板把 Runtime
的真实状态、依据、责任人、正式效力与证据用可读界面呈现，替代直接阅读 API/JSON。
本页记录只读契约、严格语义、边界与验收状态。

- 新增读取契约：`tkos.dashboard/0.1`（`GET /v1/dashboard/*`）。
- 本机只读界面：`http://127.0.0.1:58802/dashboard/`（默认关闭，需显式启用）。
- 不新增业务表、不修改 Method 动作契约；浏览不产生任何业务、回执、复盘、Context
  快照或任务写入（隔离验收有数据库前后表计数 oracle）。
- 浏览器不接触任何凭据：facade 用私有 viewer token 文件在服务端重新认证。

## 本体地图与业务关系图

看板默认进入本体地图：五个业务区域按需展开对象类型，右侧用业务语言说明定义、关系、
生命周期、责任主体和正式效力。目录来自 Runtime 冻结注册规则，尚无可见记录的类型仍然显示。
规则可切换 0.1、0.2、0.3；实际对象的说明使用其自身绑定版本。

“查看实际数据”先读取该类型的授权记录，选择后进入业务关系图。图中节点绑定对象与
精确版本，连线只取已记录引用；必要的旧版依据保留。既有列表和详情继续提供窄屏及
非图形阅读入口。概念地图、实际记录、业务内容是否正式与经营状态分别表达。

设计见 [完整交互决定](runtime-ontology-views-design.md)，新增接口独立复跑见
[ontology_views](../acceptance/ontology_views/README.md)，新版状态与截图见
[本体视图验收](runtime-ontology-views-acceptance.md)。原有 58802 容器的部署记录仅代表此前版本。

## 页面层级与语义

导航是**类型化关系层级**，不是执行进度条：

```
当前正式 Strategy → Architecture → LTCO / PCO → Mission
                                      ↓
OperatingState / BusinessFact / PeriodReview / OperatingProblem
```

- 每个分组只从**已记录的精确引用**（object_id + revision_id + payload_hash）生成；
  不按名称、单位或文本相似度推断关系。
- 一个可读正式 Strategy 时自动选中；多个独立战略域必须显式选择，绝不合并。
- 仍生效但基于旧 Strategy 的目标进入**历史依据**分组，保留自己的 `strategy_ref`，
  永不重新挂到当前 Strategy；其他独立战略域的对象标为 `unrelated`，不冒充历史。
- 主题型 BusinessFact（只有 `subject_ref.topic`）标为**未关联**，不自动挂到任何
  Mission。
- `latest` 与 `effective` 始终分开；候选版本不会变成正式状态。正式状态与内容确认
  都由覆盖**所选精确版本**的记录推导。
- Mission 没有自己的周期字段：周期筛选使用其精确引用的 PCO 版本周期；`hard_deadline`
  只是期限，不作为业务周期。
- 正式经营状态面板（RAG／as_of／基准／证据）与 Mission 内容确认（谁、何时、依据什么
  版本确认）分开显示；两者都绑定所选精确 Mission/Outcome 版本。跨版本、不可读或不
  存在的状态不会显示为当前正式状态。
- 未记录／不可读一律显式标注（`missing` + reason）；未授权引用整条隐藏，不返回其
  存在性、数量或错误细节。Owner 与 DRI 永远分开；引用人只做最小身份投影，不提供
  人员目录。

## 读取接口

全部要求当前 Bearer 身份（`Authorization: Bearer <credential>`），沿用既有 RLS、
当前权限、精确版本与 `Cache-Control: no-store`。完整 schema 见
[runtime-dashboard-openapi.json](runtime-dashboard-openapi.json)（由实际 app 导出）。

| 接口 | 说明 |
| --- | --- |
| `GET /v1/dashboard/ontology/catalog` | 三个规则版本的完整注册类型目录，不含业务记录或实例数量 |
| `GET /v1/dashboard/catalog/objects` | 任一注册类型的授权记录分页，内容版本、协议绑定与正式效力 |
| `GET /v1/dashboard/overview` | 当前 viewer、可读正式 Strategy 选择、分组可用性、历史依据提示 |
| `GET /v1/dashboard/objects` | 分组 + 战略 + 筛选 + keyset 分页列表（默认 25，最大 100） |
| `GET /v1/dashboard/objects/{id}` | 所选精确版本详情：业务内容、责任、类型化关系、正式状态、候选、历史、证据、回执 |
| `GET /v1/dashboard/objects/{id}/downstream` | 所选精确版本的下一层对象，分页续读 |
| `GET /v1/dashboard/objects/{id}/revisions` | 精确版本列表（复用既有 reader） |
| `GET /v1/dashboard/objects/{id}/revisions/{rid}` | 单条精确版本（复用既有 reader） |
| `GET /v1/dashboard/objects/{id}/reviews` | 人工确认/评审记录（复用 Method reader） |
| `GET /v1/dashboard/action-receipts/{rid}` | 回执详情（复用既有 reader） |
| `GET /v1/dashboard/evidence-assets/{oid}/revisions/{rid}` | 原始证据字节（按当前权限与 hash 校验） |

`objects` 参数：`group`（`strategy|architecture|ltco|pco|mission|operating`）、
`basis`（`current|historical|unattached|all`）、`strategy_id`、`object_type`、
`domain_id`、`period_from`、`period_to`、`owner_id`、`scope_id`、`limit`、`cursor`。
cursor 是不透明书签，绑定 endpoint、身份、scope、分组、basis、所选 Strategy
**及其 revision** 与全部筛选；越权或跨参数复用一律 422。

`catalog/objects` 参数：`object_type`（必填注册类型）、`domain_id`、
`limit`（1–100）、`cursor`。返回 `items`、`loaded_count`、`has_more` 与 `next_cursor`；
`loaded_count` 仅为本页数量，不是全库总数。记录优先采用生效内容版本，否则使用最新版本；
`object_version` 与 `basis_revision_id` 指向同一份内容。目录游标绑定当前身份及筛选，
跨类型或身份复用返回 422；未授权记录不计数。Battlefield、Capability、Outcome 是定义项，
不能作为独立类型查询。读取失败不应在页面转换为空列表。

`200` 响应包含 `schema_version`、`read_at`、可用性与精确引用；错误统一
`{error:{code,message}}`，不包含 SQL、凭据或内部细节。

## 本机 facade 与静态资源

- 默认 `TKOS_DASHBOARD_ENABLED` 关闭；关闭时 `/dashboard/` 与 `/dashboard/api` 均不存在。
- `/dashboard/api/v1/...` 是**显式 GET 只读白名单**：只调用上表的命名 reader，没有任意
  URL 代理、没有 Action、没有 `POST /v1/context-packs`、没有业务写转发。
- viewer 身份来自私有 token 文件（`TKOS_DASHBOARD_VIEWER_TOKEN_FILE`，mode 0600，
  不得 group/other 可写或可读），每个请求重新读取并重新认证；文件缺失/无效/撤权返回
  503/401/403，绝不回退到更高权限身份。浏览器 HTML/JS 与网络请求中没有任何 token。
- Host／Origin／Sec-Fetch 检查将页面限制在 loopback 允许列表；缺少 Host 直接拒绝。
- 构建清单 `asset-manifest.json` 记录前端输入与产出资源 SHA256；`scripts/build_dashboard.sh`
  写入并校验，Hatch build hook 在打包前再次校验，Dockerfile 在安装 wheel 后逐文件校验；
  前端源码改动未重新构建会使打包失败。验收端点另含
  `GET /v1/dashboard/objects/{id}/receipts`（回执分页，facade 同路径）。
- 静态响应带 CSP（`default-src 'none'; script-src 'self'; ...`）、`nosniff`、
  `Referrer-Policy: no-referrer`、`X-Frame-Options: DENY`；API 响应强制 `no-store`，
  带 hash 的静态资源可长期缓存。资源全部编译进 wheel，无 CDN/外链字体。
- 既有 `/v1` 认证完全不变；facade 的包装独立于核心动作路由。

## 本机运行

```sh
# 一次性：构建前端并验证资源（npm ci + typecheck + vitest + vite build）
./scripts/build_dashboard.sh

# Python 打包（wheel 内含 memory_service_app/dashboard_dist）
uv build
python3 scripts/verify_dashboard_assets.py \
  --archive dist/tkos_memory_service-<version>-py3-none-any.whl \
  --archive dist/tkos_memory_service-<version>.tar.gz
```

启用 facade（示例；token 文件路径必须私有）：

```sh
install -m 600 /dev/null private/viewer-token
printf '%s' "<existing integration viewer bearer token>" > private/viewer-token
TKOS_DASHBOARD_ENABLED=1 \
TKOS_DASHBOARD_VIEWER_TOKEN_FILE="$PWD/private/viewer-token" \
TKOS_DASHBOARD_ALLOWED_HOSTS=127.0.0.1:58802,localhost:58802 \
uv run uvicorn memory_service_app.main:app --host 127.0.0.1 --port 58802
```

## 前端工程与可复现依赖

React 19 + TypeScript + Vite + Tailwind CSS v4 + 官方 shadcn/ui 组件源码，位于
`workbench/dashboard/`。旧的 `workbench/` 四页原型保留不变。

| 项目 | 记录 |
| --- | --- |
| shadcn CLI | `4.21.0`（`npx shadcn@latest`，官方默认 registry `https://ui.shadcn.com`） |
| 初始化 | `shadcn init -d --base radix`（非交互默认；style `radix-nova`，baseColor `neutral`） |
| 组件 | `button badge select tabs sheet collapsible skeleton alert separator input label`（仅实际使用，未使用组件已从源码删除；不安装整套 registry） |
| 依赖版本 | `package.json` + `package-lock.json` 锁定；`npm ci` 可复现 |
| 运行时资源 | 全部本地打包（含 Geist 字体 woff2）；无 CDN、无外链字体、无外部请求 |

> 本机 npm 12 的全局 `~/.npmrc` 含 `allow-scripts=<pkg>`，会让 shadcn CLI 的
> `npm install` 子进程报 `EALLOWSCRIPTS`。生成组件时可在该命令上使用
> `NPM_CONFIG_USERCONFIG=/dev/null npx shadcn@latest ...`（仅限子进程，不修改全局
> npm 配置）；`npm ci`/`npm run build` 不受影响。

前端行为：前台每 5 秒、窗口获得焦点与手动刷新；切换战略/对象/筛选即取消过期请求并
以 epoch 丢弃迟到结果；手动“加载更多”同样带取消与 epoch，不会把旧页追加到新筛选；
服务端内容变化以提示形式出现，保留正在阅读的历史 revision；“加载更多”返回 `null`
即明确耗尽，不会复活第一页 cursor；网络失败保留并明确标注 stale；401/403/404 与
viewer 不可用会立即清空受保护内容并阻止所有在途响应回填；授权版本
（scope+principal+auth_epoch）变化时先作废全部受保护投影再重新读取。

## 验收状态

| 范围 | 结果 | 说明 |
| --- | --- | --- |
| Python dashboard 回归 | `76 passed` | 边界/分页/basis/正式状态/确认来源/任命语义/构建清单（无数据库） |
| Node 前端回归 | `56 passed` | 错误映射、URL 状态、轮询/取消/授权清空、列表与详情语义、同屏依据分组 |
| 0.3 隔离 HTTP/PG/MinIO 验收 | `58/58` | 合法动作建立完整链路；含历史依据、精确旧引用、确认来源、类型化 downstream、权限与只读 oracle |
| 当前 0.1 数据只读验收 | `49/49` | 真实 0.1 数据（隔离 clone）：Strategy→LTCO/PCO、LTCO→PCO、PCO→Mission、任命解析、前后表计数一致 |
| Runtime 浏览器只读场景 | 通过 | Codex 在真实浏览器读取 0.1 副本、独立 0.3 场景及新运行容器，见[验收报告](runtime-dashboard-acceptance.md) |
| 本机部署/发布 | 本机通过，未发布新版 | API/Worker 重建为 `dashboard-20260916-eccc346`；现有业务行保留，五容器健康；远程生产未部署 |

复跑入口：[acceptance/dashboard_0_3/README.md](../acceptance/dashboard_0_3/README.md)、
[acceptance/dashboard_0_1/README.md](../acceptance/dashboard_0_1/README.md)。

## 明确不提供

- 无业务动作按钮、无审批入口、无 Context 快照写入口、无任意代理。
- 无正式登录/模拟身份切换；viewer 由部署方固定配置。
- 无人员目录；只显示被授权对象引用到的负责人最小投影。
- 无全局总数、无隐藏数量、无样例回退、无模型调用、无汇总推断。
- 业务同事理解度、Clark 浏览器闭环与真实模型不由本轮 Runtime 看板检查代替；未发布新版，未做远程生产部署。
