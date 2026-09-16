# 看板 0.3 隔离验收（tkos.dashboard/0.1）

在全新隔离数据库中，用合法 Method 0.3 动作建立完整链路，再以真实 HTTP/PG/MinIO
验证看板读取契约与本地 facade。运行位置为 Runtime 工作区；需要 Python 3.12+、uv
（`--extra s3`）、Docker Engine 与本机既有 PostgreSQL/MinIO 端点。

## 1. 创建隔离数据库

```sh
uv run python -m acceptance.dashboard_0_3.database \
  --env-file <private base env.json> \
  --private .runtime-acceptance/dashboard-0-3-db \
  --output .runtime-acceptance/dashboard-0-3-db-report
```

该命令委托 `acceptance.anchors_v03.database`：在既有本机 PostgreSQL 容器创建新的
`tkos_a1_method_*` 库，迁移至 0025 并重复迁移验证为空，应用/迁移身份分离；不改动任何
既有数据库、容器或凭据。私有 env 与公开报告路径必须分开。

## 2. 运行验收

```sh
uv run --extra s3 python -m acceptance.dashboard_0_3.run \
  --env-file .runtime-acceptance/dashboard-0-3-db/env.json \
  --private .runtime-acceptance/dashboard-0-3-run/private \
  --output .runtime-acceptance/dashboard-0-3-run/report
```

验收内容（58 项，全部通过才写 `runtime_dashboard_api_accepted: true`）：

- 合法链路：配对 Strategy+Architecture 更新、LTCO、PCO、Mission、OperatingState、
  BusinessFact（含纠错与主题型）、PeriodReview、OperatingProblem（关闭与战略移交）。
- 历史依据：旧 Strategy 目标单独分组；Mission 引用 PCO v1 时保留 v1 的 Strategy，
  PCO 头到 v2 后不重挂；历史 revision 不借用当前确认或 Problem 处置。
- 确认来源：M1B 整组确认、LTCO、配对战略更新与 Architecture 确认各自的确认人/
  理由/回执与**覆盖的精确版本**。
- 类型化 downstream：Strategy→LTCO/PCO/Architecture、PCO→Mission、Mission→State/
  Review、State→Review/Problem；父与子 hash 不同也能正确匹配；分页续读不重不漏。
- 周期：Mission 周期来自精确引用的 PCO 版本；`hard_deadline` 不当业务周期。
- 权限：外部身份无战略选择、无隐藏计数；不相干 DRI 404；cursor 绑定身份；撤权后
  viewer token 失效，恢复后可用。
- 同屏依据：默认合并列表同时返回当前依据与历史依据，且不混入其他独立战略域。
- 待确认候选：已确认 State 上保留一个未确认的新建议，供浏览器做候选/正式对比。
- 只读 oracle：全部浏览前后固定表计数一致。
- facade：viewer/合成标签、分页、参数白名单、Host 校验、无 Action/Context 路由、
  编译资源存在、CSP 与无外链资源。

私有目录含身份 token、请求与日志，禁止提交；只发布经审阅的 `report/summary.json`。
本验收不代表浏览器人工验收、真实模型或部署验收。
