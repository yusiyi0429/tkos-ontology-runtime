# tests/workbench-ui

工作台前端纯模块的 Node 内建测试（无生产依赖，不接触网络与浏览器）。

运行：

```bash
node --test tests/workbench-ui/*.test.mjs
node --check workbench/app.js  # 各 JS 文件逐一检查语法
```

覆盖范围：

- `focus.test.mjs` — 当前对象导航状态（v0.2）：跨页保持同一对象与已选 revision、
  换对象清旧标题、授权读取才回填（防迟到响应）、切身份清空、目录选择记忆。
- `layout.test.mjs` — 实例页签默认值（显式 ?rev 落版本页、非 WI 直接看内容）、
  回执详情空态（无回执时给空态而非无限 loading）。
- `loaders.test.mjs` — 同页筛选切换竞态（旧响应乱序到达不污染新 pager）、
  AbortError 视同 stale、next_cursor 真实消费、busy 防重、错误后重试、
  回执详情/证据连续选中的覆盖防护。
- `snapshot.test.mjs` — 快照条件比较：`Z` vs `+00:00`、保留微秒精度；
  对象集合（顺序/大小写无关）差异识别；GET 回读从 selected+excluded 还原集合。
- `independent-temporal.test.mjs` — 微秒差异、时区等价与无效时间回归。
- `timeline.test.mjs` — 从真实 delivery 投影推导交付轨迹：排序、
  receiptId=action_id、未知评审结果不虚构文案。
- `contextbody.test.mjs` — POST 请求体双时间校验、UUID 集合约束、毫秒精度。
- `api.test.mjs` — actor 前缀、no-store、错误映射（含非 JSON 错误体）、
  网络失败、403 通用文案、缓存失败淘汰。
- `html.test.mjs` / `url.test.mjs` / `format.test.mjs` / `payloadview.test.mjs` —
  escaping、hash 路由与 case 路径解析、枚举翻译回退、payload 拍平。

浏览器与 HTTP 集成验收由 Codex 独立执行。

`integration.local.mjs` 验证 A1 下四种合成身份的实际 GET 请求。使用已准备好的
本机 A1 网关，显式设置 `TKOS_WORKBENCH_URL`（例如 `http://127.0.0.1:PORT/`）。
其 `case.json` 必须含交付/Outcome 锚点、Context Pack 回读案例，以及
`evidence: [{object_id, revision_id, sha256}]`。证据从当前案例读取并核对原始字节 hash，
不再固定旧案例 ID。有权身份收到 403/404 判失败，只有 outsider 的受限读取可拒绝。
现有 8032 演示仍可能运行旧版，不能用其结果声称 A1 联调已通过。
